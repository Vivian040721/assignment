"""Independent loopback service: MCP HTTP transport and durable research jobs.

The GUI is a detachable client. Reloading it cannot own or cancel these tasks.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import secrets
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from paper_agent import __version__
from paper_agent.config import Settings
from paper_agent.service_control import API_VERSION, default_data_root, descriptor_path
from paper_agent.services.pipeline import ResearchPipeline
from paper_agent.web import (
    ACTIVE,
    UIError,
    Workbench,
    artifact_location_endpoint,
    artifact_text_endpoint,
    atomic_json,
    cancel_endpoint,
    config_endpoint,
    conversation_endpoint,
    conversations_endpoint,
    events_endpoint,
    job_endpoint,
    now,
    payload,
)


class ServiceLock:
    """OS-owned file lock is released on a crash, unlike PID sentinel files."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()

    def __enter__(self):
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("A service already owns this workspace.") from None
        return self

    def __exit__(self, *_):
        self.file.close()


class LocalTokenMiddleware:
    def __init__(self, app, token: str, is_shutting_down=lambda: False):
        self.app, self.expected = app, ("Bearer " + token).encode("ascii")
        self.is_shutting_down = is_shutting_down

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            supplied = headers.get(b"authorization", b"")
            # Native clients don't send Origin. Reject browser-originated
            # requests rather than exposing local settings to an arbitrary page.
            if b"origin" in headers:
                await JSONResponse({"error": "本机服务不接受浏览器来源请求。"}, status_code=403)(scope, receive, send)
                return
            if not hmac.compare_digest(supplied, self.expected):
                await JSONResponse({"error": "需要本机服务凭据。"}, status_code=401)(scope, receive, send)
                return
            if self.is_shutting_down():
                await JSONResponse({"error": "服务正在停止，请稍后重新打开应用。"}, status_code=503)(
                    scope, receive, send)
                return
        await self.app(scope, receive, send)


class PipelineRegistry:
    def __init__(self):
        self.pipelines: dict[str, ResearchPipeline] = {}
        self.calls: dict[str, set[asyncio.Task]] = {}
        self.manager: Workbench | None = None
        self.default_pipeline: ResearchPipeline | None = None
        self.default_settings: Settings | None = None

    def provider(self, context) -> ResearchPipeline:
        job_id = (context.headers or {}).get("x-paper-agent-job")
        if job_id:
            if job_id not in self.pipelines:
                raise ValueError("This research job is no longer active.")
            task = asyncio.current_task()
            if task is not None:
                calls = self.calls.setdefault(job_id, set())
                calls.add(task)
                task.add_done_callback(calls.discard)
            return self.pipelines[job_id]
        if self.manager is None:
            raise RuntimeError("Service is not ready.")
        settings = self.manager.settings()
        if self.default_pipeline is None or settings != self.default_settings:
            self.default_settings = settings
            self.default_pipeline = ResearchPipeline(settings)
        return self.default_pipeline

    async def clear(self, job_id: str) -> None:
        # HTTP cancellation alone must not leave an MCP handler spending tokens.
        calls = self.calls.pop(job_id, set())
        pending = [task for task in calls if not task.done() and task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.pipelines.pop(job_id, None)


def create_app(data_root: Path, endpoint: dict, shutdown, *, settings: Settings | None = None) -> Starlette:
    from paper_agent.main_agent import run_research_http
    from paper_agent.server import create_server

    data_root = data_root.expanduser().resolve()
    registry = PipelineRegistry()
    mcp_app = create_server(pipeline_provider=registry.provider).streamable_http_app(
        stateless_http=False, json_response=True,
    )
    shutdown_pending = False
    base_settings = settings or Settings.from_env()
    base_settings = replace(base_settings, mode="live", data_dir=data_root,
                             output_dir=data_root / "service" / "external-runs",
                             pdf_dir=None, markdown_dir=None, summaries_dir=None, reports_dir=None)

    async def runner(job: dict, config: Settings, progress) -> dict:
        job_id = job["job_id"]
        registry.pipelines[job_id] = ResearchPipeline(config)
        task = asyncio.create_task(run_research_http(
            job["idea"], config, job["top_k"], job["query"], progress,
            url=endpoint["mcp_url"],
            headers={"Authorization": "Bearer " + endpoint["token"], "x-paper-agent-job": job_id},
        ))
        try:
            # First cancel the server handlers, then let the client's transport
            # unwind; a disconnected GUI never enters this cancellation path.
            return await asyncio.shield(task)
        finally:
            await registry.clear(job_id)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @asynccontextmanager
    async def lifespan(app):
        manager = Workbench(base_settings, root=data_root / "service", config_path=data_root / "config.json",
                            research_runner=runner)
        app.state.workbench, registry.manager = manager, manager
        app.state.service_data_root = data_root.resolve()
        async with mcp_app.router.lifespan_context(mcp_app):
            try:
                yield
            finally:
                await manager.close()

    async def jobs(request):
        manager = request.app.state.workbench
        if request.method == "POST":
            values = await payload(request)
            if shutdown_pending:
                raise UIError("服务正在停止，请稍后重新打开应用。", 503)
            # A single GUI attaches to the existing job instead of silently
            # creating another billable job after a reload.
            if any(job["status"] in ACTIVE for job in manager.jobs.values()):
                raise UIError("已有研究任务运行中，请先等待完成或取消。", 409)
            session = manager.create_session()
            try:
                return JSONResponse(manager.submit(session["session_id"], values), status_code=202)
            except Exception:
                manager.sessions.pop(session["session_id"], None)
                (manager.root / "sessions" / (session["session_id"] + ".json")).unlink(missing_ok=True)
                raise
        rows = [{"run_id": job["job_id"], "job_id": job["job_id"], "idea": job["idea"],
                 "status": job["status"], "mode": job["mode"],
                 "execution_mode": job["execution_mode"], "updated_at": job["updated_at"]}
                | {"conversation_id": job.get("conversation_id"), "action": job.get("action", "research")}
                for job in sorted(manager.jobs.values(), key=lambda item: item["updated_at"], reverse=True)]
        return JSONResponse(manager._redact_public(rows))

    async def info(request):
        manager = request.app.state.workbench
        root = request.app.state.service_data_root
        return JSONResponse({
            "version": __version__, "api_version": API_VERSION, "pid": endpoint["pid"],
            "active_jobs": sum(job["status"] in ACTIVE for job in manager.jobs.values()),
            "data_root": str(root), "config_path": str(root / "config.json"),
        })

    async def stop(request):
        nonlocal shutdown_pending
        manager = request.app.state.workbench
        active_jobs = sum(job["status"] in ACTIVE for job in manager.jobs.values())
        if active_jobs:
            raise UIError("仍有活动任务，服务未停止。请等待任务完成，或在应用中取消任务后再停止服务。", 409)
        # Give the response time to flush before uvicorn starts graceful shutdown.
        shutdown_pending = True
        asyncio.get_running_loop().call_later(0.2, shutdown)
        return JSONResponse({"status": "stopping"})

    async def submit_message(request):
        if shutdown_pending:
            raise UIError("服务正在停止，请稍后重新打开应用。", 503)
        values = await payload(request)
        if shutdown_pending:
            raise UIError("服务正在停止，请稍后重新打开应用。", 503)
        return JSONResponse(request.app.state.workbench.submit_message(values), status_code=202)

    async def ui_error(request, error: UIError):
        return JSONResponse({"error": error.message}, status_code=error.status)

    return Starlette(lifespan=lifespan, exception_handlers={UIError: ui_error}, middleware=[
        Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"]),
        Middleware(LocalTokenMiddleware, token=endpoint["token"],
                   is_shutting_down=lambda: shutdown_pending),
    ], routes=[
        Route("/api/service", info),
        Route("/api/service/shutdown", stop, methods=["POST"]),
        Route("/api/config", config_endpoint, methods=["GET", "PUT"]),
        Route("/api/conversations", conversations_endpoint),
        Route("/api/conversations/messages", submit_message, methods=["POST"]),
        Route("/api/conversations/{conversation_id}", conversation_endpoint),
        Route("/api/desktop/jobs", jobs, methods=["GET", "POST"]),
        Route("/api/jobs/{job_id}", job_endpoint),
        Route("/api/jobs/{job_id}/cancel", cancel_endpoint, methods=["POST"]),
        Route("/api/jobs/{job_id}/events", events_endpoint),
        Route("/api/jobs/{job_id}/artifacts/{artifact_id}/text", artifact_text_endpoint),
        Route("/api/jobs/{job_id}/artifacts/{artifact_id}/location", artifact_location_endpoint),
        Mount("/", app=mcp_app),
    ])


async def serve(data_root: Path, port: int = 0, *, settings: Settings | None = None) -> None:
    # Bind once before generating the descriptor; no find-free-port race.
    data_root = data_root.expanduser().resolve()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        endpoint = {"pid": os.getpid(), "url": url, "mcp_url": url + "/mcp",
                    "api_version": API_VERSION, "started_at": now(),
                    "data_root": str(data_root), "token": secrets.token_urlsafe(48)}
        server = None

        def shutdown():
            server.should_exit = True

        app = create_app(data_root, endpoint, shutdown, settings=settings)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_level="warning",
                                             http="h11", ws="none", loop="asyncio", lifespan="on",
                                             access_log=False, timeout_graceful_shutdown=10))
        path = descriptor_path(data_root)

        async def publish():
            while not server.started:
                await asyncio.sleep(0.05)
            atomic_json(path, endpoint)
            if os.name != "nt":
                path.chmod(0o600)

        publisher = asyncio.create_task(publish())
        try:
            await server.serve(sockets=[listener])
        finally:
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent Paper Agent MCP and job service")
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses an available port")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Port must be in 0..65535")
    root = (args.data_root or default_data_root()).expanduser().resolve()
    if os.name != "nt":
        os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.WARNING)
    try:
        with ServiceLock(root / "service" / "service.lock"):
            asyncio.run(serve(root, args.port))
    except RuntimeError as error:
        logging.error("Service could not start: %s", error)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
