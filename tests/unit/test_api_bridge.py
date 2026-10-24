"""Isolated contracts for the authenticated Electron service bridge."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
from starlette.requests import Request

from paper_agent import service_control
from paper_agent.config import Settings
from paper_agent.service_control import (
    API_VERSION,
    descriptor_path,
    ensure_service,
    read_descriptor,
    service_info,
)
from paper_agent.service_daemon import create_app
from paper_agent.web import UIError, Workbench, events_endpoint

TOKEN = "bridge-test-token-not-a-real-credential"
JOB_ID = "a" * 32


def test_artifact_read_allows_other_lazily_created_output_directories(tmp_path):
    from paper_agent.api_bridge import artifact_text

    report = tmp_path / "report.md"
    report.write_text("fixture report", encoding="utf-8")
    job = {"artifacts": [{"artifact_id": "report", "name": "report.md", "kind": "report"}],
           "_artifact_paths": {"report": str(report)},
           "_artifact_roots": [str(tmp_path), str(tmp_path / "unused-pdfs")]}
    assert artifact_text(job, "report")["text"] == "fixture report"


def _settings(tmp_path: Path) -> Settings:
    return Settings(mode="demo", data_dir=tmp_path / "data", output_dir=tmp_path / "runs",
                    llm_api_key="test-only-deepseek-placeholder",
                    openalex_api_key="test-only-openalex-placeholder")


def _app_at(tmp_path: Path):
    root = (tmp_path / "data-root").resolve()
    endpoint = {"pid": 1234, "api_version": API_VERSION, "url": "http://127.0.0.1:43871",
                "mcp_url": "http://127.0.0.1:43871/mcp", "data_root": str(root), "token": TOKEN}
    app = create_app(root, endpoint, lambda: None, settings=_settings(tmp_path))
    manager = Workbench(_settings(tmp_path), root=root / "service", config_path=root / "config.json")
    app.state.workbench = manager
    app.state.service_data_root = root
    return app, manager, endpoint


async def _request(app, method: str, path: str, *, token: str | None = TOKEN,
                   origin: str | None = None, json_body: dict | None = None):
    headers = {"host": "127.0.0.1"}
    if token is not None:
        headers["authorization"] = "Bearer " + token
    if origin is not None:
        headers["origin"] = origin
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        return await client.request(method, path, headers=headers, json=json_body)


def test_service_bridge_requires_bearer_and_rejects_any_browser_origin(tmp_path):
    app, _, _ = _app_at(tmp_path)

    async def scenario():
        missing = await _request(app, "GET", "/api/service", token=None)
        invalid = await _request(app, "GET", "/api/service", token="wrong-test-token")
        origin = await _request(app, "GET", "/api/service", origin="https://untrusted.example")
        native = await _request(app, "GET", "/api/service")
        return missing, invalid, origin, native

    missing, invalid, origin, native = asyncio.run(scenario())
    assert missing.status_code == invalid.status_code == 401
    assert origin.status_code == 403
    assert native.status_code == 200


def test_service_info_matches_contract_and_never_contains_tokens_or_keys(tmp_path):
    app, _, endpoint = _app_at(tmp_path)
    response = asyncio.run(_request(app, "GET", "/api/service"))
    info = response.json()
    assert response.status_code == 200
    assert set(info) == {"version", "api_version", "pid", "active_jobs", "data_root", "config_path"}
    assert info["version"] == "0.2.2"
    assert info["api_version"] == 3
    assert info["pid"] == endpoint["pid"]
    assert info["active_jobs"] == 0
    assert info["data_root"] == endpoint["data_root"]
    encoded = response.text
    assert TOKEN not in encoded
    assert "test-only-deepseek-placeholder" not in encoded
    assert "test-only-openalex-placeholder" not in encoded


def test_endpoint_descriptor_rejects_external_urls_wrong_roots_and_invalid_pids(tmp_path):
    root = (tmp_path / "user-data").resolve()
    endpoint = {"url": "http://127.0.0.1:43871", "mcp_url": "http://127.0.0.1:43871/mcp",
                "pid": 1234, "api_version": API_VERSION, "data_root": str(root), "token": TOKEN}
    path = descriptor_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(endpoint), encoding="utf-8")
    assert read_descriptor(root) == endpoint
    for field, value in (("url", "http://192.0.2.2:43871"), ("data_root", str(tmp_path / "elsewhere")),
                         ("pid", True)):
        invalid = endpoint | {field: value}
        if field == "url":
            invalid["mcp_url"] = value + "/mcp"
        path.write_text(json.dumps(invalid), encoding="utf-8")
        assert read_descriptor(root) is None


def test_service_info_checks_pid_and_canonical_data_root(monkeypatch, tmp_path):
    root = (tmp_path / "user-data").resolve()
    endpoint = {"url": "http://127.0.0.1:43871", "mcp_url": "http://127.0.0.1:43871/mcp",
                "pid": 1234, "api_version": API_VERSION, "data_root": str(root), "token": TOKEN}
    valid = {"version": "0.2.2", "api_version": API_VERSION, "pid": 1234, "active_jobs": 0,
             "data_root": str(root), "config_path": str(root / "config.json")}

    class Response:
        status_code = 200

        def __init__(self, value):
            self.value = value

        def json(self):
            return self.value

    monkeypatch.setattr(service_control.httpx, "get", lambda *_args, **_kwargs: Response(valid))
    assert service_info(endpoint) == valid
    for invalid in (valid | {"pid": 9876}, valid | {"data_root": str(tmp_path / "elsewhere")},
                    valid | {"config_path": str(tmp_path / "wrong.json")}):
        monkeypatch.setattr(service_control.httpx, "get",
                            lambda *_args, _value=invalid, **_kwargs: Response(_value))
        assert service_info(endpoint) is None


def test_start_refuses_to_attach_or_spawn_over_an_old_service(monkeypatch, tmp_path):
    endpoint = {"url": "http://127.0.0.1:43871", "mcp_url": "http://127.0.0.1:43871/mcp",
                "pid": 1234, "api_version": API_VERSION - 1, "data_root": str(tmp_path), "token": TOKEN}
    monkeypatch.setattr(service_control, "read_descriptor", lambda _root: endpoint)
    monkeypatch.setattr(service_control, "service_is_ready", lambda _endpoint: True)
    monkeypatch.setattr(service_control.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("must not start another daemon"))
    with pytest.raises(RuntimeError, match="取消活动任务"):
        ensure_service(tmp_path)


def test_service_control_json_is_private_only_for_start(monkeypatch, capsys, tmp_path):
    endpoint = {"url": "http://127.0.0.1:43871", "mcp_url": "http://127.0.0.1:43871/mcp",
                "pid": 1234, "api_version": API_VERSION, "data_root": str(tmp_path), "token": TOKEN}
    info = {"version": "0.2.2", "api_version": API_VERSION, "pid": 1234, "active_jobs": 0,
            "data_root": str(tmp_path), "config_path": str(tmp_path / "config.json")}
    monkeypatch.setattr(service_control, "ensure_service", lambda _root: endpoint)
    monkeypatch.setattr(service_control, "read_descriptor", lambda _root: endpoint)
    monkeypatch.setattr(service_control, "service_is_ready", lambda _endpoint: True)
    monkeypatch.setattr(service_control, "service_info", lambda _endpoint: info)

    monkeypatch.setattr("sys.argv", ["service_control", "start", "--json"])
    assert service_control.main() == 0
    start = json.loads(capsys.readouterr().out)
    assert start["token"] == TOKEN
    assert set(start) == {"url", "mcp_url", "pid", "api_version", "data_root", "token"}

    monkeypatch.setattr("sys.argv", ["service_control", "start"])
    assert service_control.main() == 0
    traditional = capsys.readouterr().out
    assert "MCP: http://127.0.0.1:43871/mcp" in traditional
    assert TOKEN not in traditional

    monkeypatch.setattr("sys.argv", ["service_control", "status", "--json"])
    assert service_control.main() == 0
    status = capsys.readouterr().out
    assert TOKEN not in status
    assert json.loads(status) == {"running": True} | info


def test_artifact_text_enforces_registered_root_pdf_and_size_limit(tmp_path):
    app, manager, _ = _app_at(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    markdown = root / "report.md"
    markdown.write_bytes(b"# Test report\n")
    large_json = root / "large.json"
    large_json.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"%PDF-test")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    manager.jobs[JOB_ID] = {
        "job_id": JOB_ID, "session_id": "b" * 32, "idea": "artifact test", "status": "completed",
        "result": None, "events": [], "artifacts": [
            {"artifact_id": "md1", "name": "report.md", "kind": "report"},
            {"artifact_id": "js1", "name": "large.json", "kind": "run"},
            {"artifact_id": "pdf1", "name": "paper.pdf", "kind": "pdf"},
            {"artifact_id": "out1", "name": "outside.md", "kind": "report"},
        ],
        "_artifact_paths": {"md1": str(markdown), "js1": str(large_json), "pdf1": str(pdf),
                             "out1": str(outside)},
        "_artifact_roots": [str(root)],
    }

    async def scenario():
        good = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/md1/text")
        large = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/js1/text")
        pdf_result = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/pdf1/text")
        location = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/pdf1/location")
        outside_result = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/out1/text")
        outside_location = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/out1/location")
        missing = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/missing/text")
        return good, large, pdf_result, location, outside_result, outside_location, missing

    good, large, pdf_result, location, outside_result, outside_location, missing = asyncio.run(scenario())
    assert good.status_code == 200
    assert good.json() == {"text": "# Test report\n", "name": "report.md",
                           "kind": "report", "truncated": False}
    assert large.status_code == 200
    assert len(large.json()["text"].encode("utf-8")) == 2 * 1024 * 1024
    assert large.json()["truncated"] is True
    assert pdf_result.status_code == 415
    assert location.status_code == 200
    assert location.json() == {"path": str(pdf.resolve()), "name": "paper.pdf", "kind": "pdf"}
    assert outside_result.status_code == outside_location.status_code == missing.status_code == 404


def test_job_events_and_artifact_payloads_redact_configured_keys(tmp_path):
    app, manager, _ = _app_at(tmp_path)
    key = manager.settings().llm_api_key
    artifact_root = tmp_path / "private-artifacts"
    artifact_root.mkdir()
    document = artifact_root / "report.md"
    document.write_text("private value: " + key, encoding="utf-8")
    manager.jobs[JOB_ID] = {
        "job_id": JOB_ID, "session_id": "b" * 32, "idea": "secret redaction", "status": "completed",
        "result": {"report": {"markdown": "model echoed " + key}},
        "events": [{"seq": 1, "timestamp": "t1", "kind": "trace", "message": key,
                    "stage": "report", "data": {"value": key}}],
        "artifacts": [{"artifact_id": "safe1", "name": "report.md", "kind": "report"}],
        "_artifact_paths": {"safe1": str(document)}, "_artifact_roots": [str(artifact_root)],
    }

    async def scenario():
        job = await _request(app, "GET", f"/api/jobs/{JOB_ID}")
        events = await _request(app, "GET", f"/api/jobs/{JOB_ID}/events?after=0")
        text = await _request(app, "GET", f"/api/jobs/{JOB_ID}/artifacts/safe1/text")
        return job, events, text

    job, events, text = asyncio.run(scenario())
    assert all(response.status_code == 200 for response in (job, events, text))
    assert all(key not in response.text for response in (job, events, text))
    assert "[已隐藏]" in job.text and "[已隐藏]" in events.text and "[已隐藏]" in text.text


def test_sse_replays_after_sequence_then_emits_terminal_done(tmp_path):
    app, manager, _ = _app_at(tmp_path)
    manager.jobs[JOB_ID] = {
        "job_id": JOB_ID, "session_id": "b" * 32, "idea": "trace", "status": "completed",
        "events": [
            {"seq": 1, "timestamp": "t1", "kind": "status", "message": "queued", "stage": "", "data": {}},
            {"seq": 2, "timestamp": "t2", "kind": "trace", "message": "", "stage": "main_agent_tool_call",
             "data": {"tool": "search_papers"}},
            {"seq": 3, "timestamp": "t3", "kind": "status", "message": "done", "stage": "",
             "data": {"status": "completed"}},
        ],
    }
    response = asyncio.run(_request(app, "GET", f"/api/jobs/{JOB_ID}/events?after=1"))
    assert response.status_code == 200
    assert "id: 2\nevent: update" in response.text
    assert '"seq": 2' in response.text
    assert '"stage": "main_agent_tool_call"' in response.text
    assert "id: 1" not in response.text
    assert f'event: done\ndata: {{"job_id": "{JOB_ID}", "status": "completed"}}' in response.text


def test_service_shutdown_refuses_active_jobs(tmp_path):
    root = (tmp_path / "data-root").resolve()
    shutdowns = []
    endpoint = {"pid": 1234, "api_version": API_VERSION, "url": "http://127.0.0.1:43871",
                "mcp_url": "http://127.0.0.1:43871/mcp", "data_root": str(root), "token": TOKEN}
    app = create_app(root, endpoint, lambda: shutdowns.append(True), settings=_settings(tmp_path))
    manager = Workbench(_settings(tmp_path), root=root / "service", config_path=root / "config.json")
    manager.jobs[JOB_ID] = {"job_id": JOB_ID, "status": "running"}
    app.state.workbench = manager
    app.state.service_data_root = root
    response = asyncio.run(_request(app, "POST", "/api/service/shutdown"))
    assert response.status_code == 409
    assert "活动任务" in response.json()["error"]
    assert not shutdowns


def test_new_jobs_are_refused_after_graceful_shutdown_is_requested(tmp_path):
    root = (tmp_path / "data-root").resolve()
    endpoint = {"pid": 1234, "api_version": API_VERSION, "url": "http://127.0.0.1:43871",
                "mcp_url": "http://127.0.0.1:43871/mcp", "data_root": str(root), "token": TOKEN}
    app = create_app(root, endpoint, lambda: None, settings=_settings(tmp_path))
    manager = Workbench(_settings(tmp_path), root=root / "service", config_path=root / "config.json")
    app.state.workbench = manager
    app.state.service_data_root = root

    async def scenario():
        stopped = await _request(app, "POST", "/api/service/shutdown")
        submitted = await _request(app, "POST", "/api/conversations/messages",
                                   json_body={"text": "a valid research question", "action": "research"})
        return stopped, submitted

    stopped, submitted = asyncio.run(scenario())
    assert stopped.status_code == 200
    assert submitted.status_code == 503
    assert not manager.jobs


def test_disconnecting_sse_does_not_cancel_the_owned_job(tmp_path):
    app, manager, _ = _app_at(tmp_path)

    async def scenario():
        cancelled = asyncio.Event()
        manager.jobs[JOB_ID] = {
            "job_id": JOB_ID, "session_id": "b" * 32, "idea": "still running", "status": "running",
            "events": [{"seq": 1, "timestamp": "t1", "kind": "progress", "message": "real event",
                        "stage": "", "data": {}}],
        }

        async def job_task():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(job_task())
        manager.tasks[JOB_ID] = task
        scope = {"type": "http", "method": "GET", "path": f"/api/jobs/{JOB_ID}/events",
                 "raw_path": f"/api/jobs/{JOB_ID}/events".encode(), "query_string": b"after=0",
                 "headers": [], "scheme": "http", "server": ("127.0.0.1", 43871),
                 "client": ("127.0.0.1", 10000), "path_params": {"job_id": JOB_ID}, "app": app}
        response = await events_endpoint(Request(scope))
        stream = response.body_iterator
        await anext(stream)  # connected comment
        update = await anext(stream)
        await stream.aclose()
        await asyncio.sleep(0)
        still_running = not task.done()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return update, still_running, cancelled.is_set()

    update, still_running, cancelled_by_test_cleanup = asyncio.run(scenario())
    assert "event: update" in update
    assert still_running
    assert cancelled_by_test_cleanup  # Only the explicit cleanup above cancelled the test-owned task.


def test_legacy_jobs_are_discoverable_as_stable_read_only_conversations(tmp_path):
    root = tmp_path / "service"
    settings = _settings(tmp_path)
    root.mkdir()
    session_id = "c" * 32
    job_id = "d" * 32
    stamp = "2026-10-24T00:00:00+00:00"
    (root / "sessions").mkdir()
    (root / "jobs").mkdir()
    (root / "sessions" / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id, "title": "legacy session", "created_at": stamp,
        "updated_at": stamp, "job_ids": [job_id],
    }), encoding="utf-8")
    (root / "jobs" / f"{job_id}.json").write_text(json.dumps({
        "job_id": job_id, "session_id": session_id, "idea": "旧版论文研究记录", "status": "completed",
        "created_at": stamp, "updated_at": stamp, "run_id": "e" * 32,
        "result": {"mode": "demo", "report": {"markdown": "旧版真实结果。"}, "selected_papers": []},
        "events": [], "error": None, "artifacts": [],
    }), encoding="utf-8")

    first = Workbench(settings, root=root, config_path=tmp_path / "config.json")
    rows = first.list_conversations()
    assert len(rows) == 1
    assert rows[0]["read_only"] is True
    identifier = rows[0]["conversation_id"]
    assert len(identifier) == 32 and all(char in "0123456789abcdef" for char in identifier)
    reopened = Workbench(settings, root=root, config_path=tmp_path / "config.json")
    conversation = reopened.load_conversation(identifier)
    assert conversation["read_only"] is True
    assert [message["content"] for message in conversation["messages"]] == [
        "旧版论文研究记录", "【Demo 合成示例】\n\n旧版真实结果。"]
    assert conversation["result"]["report"]["markdown"] == "旧版真实结果。"
    with pytest.raises(UIError, match="旧版记录仅供阅读，请新建会话") as error:
        reopened.submit_message({"text": "继续讨论", "conversation_id": identifier, "action": "chat"})
    assert error.value.status == 409
    assert not (root / "conversations").exists()
