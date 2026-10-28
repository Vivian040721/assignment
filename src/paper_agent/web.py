"""Local research workbench: durable sessions, cancellable MCP jobs and SSE.

Loading the application never starts research or calls a model. Only submitting a
job starts the existing MCP client. Model keys stay on the local Python server.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from paper_agent.api_bridge import (
    ArtifactAccessError,
    artifact_location,
    artifact_text,
    redact_secrets,
    resolve_artifact,
)
from paper_agent.config import Settings
from paper_agent.conversations import ConversationStore, discussion_messages
from paper_agent.llm_client import ChatClient, LLMError, LLMOutputError
from paper_agent.models import DeliveryStatus, Paper, PipelineResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
ACTIVE = {"pending", "queued", "running"}
EXECUTION_REQUESTS = {"auto", "parallel", "sequential", "concurrent"}
SAFE_ID = re.compile(r"[0-9a-f]{32}\Z")
RUN_ID = re.compile(r"run_id=([0-9a-f]{32})")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _missing_paper_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_replace(temporary: Path, target: Path) -> None:
    # Windows scanners and sync clients briefly hold just-written files. On this
    # path every job event rewrites state, so one transient lock must never abort
    # a finished research run and strand it in "running" forever.
    for attempt in range(8):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.02 * (attempt + 1))


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("请求内容必须是 JSON 对象。")
    return value


class UIError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message, self.status = message, status


class Workbench:
    def __init__(self, settings: Settings, *, root: Path | None = None,
                 config_path: Path | None = None, research_runner=None,
                 private_secrets: tuple[str, ...] = ()):
        self.base = settings
        self.root = (root or settings.data_dir.resolve() / "web").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path = config_path or self.root / "config.json"
        self.research_runner = research_runner
        self.private_secrets = private_secrets
        self.preferences = read_json(self.config_path) if self.config_path.is_file() else {}
        self.sessions: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.queue = asyncio.Semaphore(1)
        self.conversations = ConversationStore(self.root / "conversations")
        self._load_history()
        for job in sorted(self.jobs.values(), key=lambda item: item["updated_at"]):
            self._sync_conversation(job)

    def _load_history(self) -> None:
        for path in sorted((self.root / "sessions").glob("*.json")):
            try:
                session = read_json(path)
                if SAFE_ID.fullmatch(session["session_id"]):
                    self.sessions[session["session_id"]] = session
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for path in sorted((self.root / "jobs").glob("*.json")):
            try:
                job = read_json(path)
                if not SAFE_ID.fullmatch(job["job_id"]) or job["session_id"] not in self.sessions:
                    continue
                self.jobs[job["job_id"]] = job
                self._restore_loaded_artifacts(job)
                if job["status"] in ACTIVE:
                    # Restart never retries billable work without a new user action.
                    job["status"] = "failed"
                    job["error"] = "服务已重启，任务已中断。已有资料保留，请重新提交以继续研究。"
                    if job.get("requested_execution_mode") in {"auto", "parallel", "sequential"}:
                        try:
                            result = job.get("result")
                            run = PipelineResult.model_validate(result) if isinstance(result, dict) else None
                            self._finalize_terminal_result(job, run, self._job_settings(job), "failed")
                        except Exception:
                            job["delivery_status"] = None
                    self.event(job, "error", message=job["error"],
                               data={"status": "failed", "delivery_status": job.get("delivery_status")})
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def settings(self) -> Settings:
        values = self.preferences
        return replace(
            self.base, mode="live",
            llm_base_url=values.get("base_url", self.base.llm_base_url),
            llm_model=values.get("model", self.base.llm_model),
            llm_api_key=values.get("api_key", self.base.llm_api_key),
            openalex_api_key=values.get("openalex_api_key", self.base.openalex_api_key),
            llm_thinking_enabled=values.get("thinking_enabled", self.base.llm_thinking_enabled),
            http_proxy=values.get("http_proxy", self.base.http_proxy),
            top_k=values.get("default_top_k", self.base.top_k),
            concurrency=values.get("concurrency", self.base.concurrency),
            search_concurrency=values.get("concurrency", self.base.search_concurrency),
            download_concurrency=values.get("concurrency", self.base.download_concurrency),
            conversion_concurrency=values.get("concurrency", self.base.conversion_concurrency),
            llm_concurrency=values.get("concurrency", self.base.llm_concurrency),
            llm_timeout=values.get("llm_timeout", self.base.llm_timeout),
            http_timeout=values.get("http_timeout", self.base.http_timeout),
        )

    def public_config(self) -> dict:
        settings = self.settings()
        return {
            "provider": "deepseek", "base_url": settings.llm_base_url,
            "model": settings.llm_model, "api_key_configured": bool(settings.llm_api_key),
            "openalex_api_key_configured": bool(settings.openalex_api_key),
            "thinking_enabled": settings.llm_thinking_enabled, "http_proxy": settings.http_proxy,
            "default_mode": "live", "default_top_k": settings.top_k,
            "default_execution_mode": "auto",
            "concurrency": settings.concurrency, "llm_timeout": settings.llm_timeout,
            "http_timeout": settings.http_timeout,
        }

    def save_config(self, values: dict) -> dict:
        allowed = {"base_url", "model", "api_key", "clear_api_key", "http_proxy", "thinking_enabled",
                   "default_top_k", "concurrency", "llm_timeout", "http_timeout",
                   "openalex_api_key", "clear_openalex_api_key"}
        if set(values) - allowed:
            raise UIError("模型设置包含不支持的字段。")
        updated = dict(self.preferences)
        for key, low, high in (("default_top_k", 1, min(15, self.base.candidate_limit)),
                               ("concurrency", 1, 8), ("llm_timeout", 5, 600),
                               ("http_timeout", 5, 300)):
            if key in values:
                value = values[key]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not low <= value <= high
                        or (key in {"default_top_k", "concurrency"} and not isinstance(value, int))):
                    raise UIError(f"{key} 需要在 {low} 至 {high} 之间。")
                updated[key] = value
        for key in ("base_url", "model", "api_key", "openalex_api_key", "http_proxy"):
            if key not in values:
                continue
            value = values[key]
            if not isinstance(value, str) or len(value) > 4096:
                raise UIError("模型设置的格式不正确。")
            value = value.strip()
            if key in {"api_key", "openalex_api_key"} and not value:
                continue  # Blank input retains a previously configured key.
            if key in {"base_url", "http_proxy"} and value:
                parsed = urlsplit(value)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise UIError("API 地址和代理地址需要完整的 http:// 或 https:// 地址。")
                if parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise UIError("地址中不能包含账号、密码、查询参数或片段。")
            if key in {"model", "base_url"} and not value:
                raise UIError("模型名称和 API 地址不能为空。")
            if key == "model" and (len(value) > 200 or any(char.isspace() for char in value)):
                raise UIError("模型名称不应包含空格。")
            if key == "base_url":
                value = value.rstrip("/")
                if value.endswith("/chat/completions"):
                    value = value[:-len("/chat/completions")]
            updated[key] = value
        for key in ("thinking_enabled", "clear_api_key", "clear_openalex_api_key"):
            if key in values and not isinstance(values[key], bool):
                raise UIError("开关值需要为布尔类型。")
        if "thinking_enabled" in values:
            updated["thinking_enabled"] = values["thinking_enabled"]
        if values.get("clear_api_key"):
            updated["api_key"] = ""
        if values.get("clear_openalex_api_key"):
            updated["openalex_api_key"] = ""
        # Save locally; never include the key in the returned object or events.
        atomic_json(self.config_path, updated)
        self.preferences = updated
        return self.public_config()

    def get_session(self, session_id: str) -> dict:
        if not SAFE_ID.fullmatch(session_id) or session_id not in self.sessions:
            raise UIError("会话不存在。", 404)
        return self.sessions[session_id]

    def get_job(self, job_id: str) -> dict:
        if not SAFE_ID.fullmatch(job_id) or job_id not in self.jobs:
            raise UIError("任务不存在。", 404)
        return self.jobs[job_id]

    def persist_session(self, session: dict) -> None:
        atomic_json(self.root / "sessions" / (session["session_id"] + ".json"), session)

    def persist_job(self, job: dict) -> None:
        job["updated_at"] = now()
        atomic_json(self.root / "jobs" / (job["job_id"] + ".json"), job)
        session = self.sessions.get(job["session_id"])
        if session:
            session["updated_at"] = job["updated_at"]
            self.persist_session(session)
        self._sync_conversation(job)

    def event(self, job: dict, kind: str, *, message: str = "", stage: str = "", data=None) -> None:
        seq = job.get("_event_seq", 0) + 1
        job["_event_seq"] = seq
        event = {"seq": seq, "timestamp": now(), "kind": kind, "message": message,
                 "stage": stage, "data": data or {}}
        job["events"] = [*job.get("events", []), event][-800:]
        self.persist_job(job)

    def public_job(self, job: dict) -> dict:
        value = {key: value for key, value in job.items() if not key.startswith("_")}
        value.setdefault("delivery_status", None)
        result = value.get("result")
        if job.get("status") in ACTIVE:
            # An intermediate pipeline snapshot is not a final delivery decision.
            value["delivery_status"] = None
            if isinstance(result, dict) and result.get("delivery_status") is not None:
                value["result"] = result | {"delivery_status": None}
        elif value["delivery_status"] is None and isinstance(result, dict):
            # Some early v3 records saved the authoritative classification only
            # on PipelineResult. Copy that value; never derive it from Job.status.
            delivery = result.get("delivery_status")
            if delivery in {"result", "preliminary_notes", "no_result"}:
                value["delivery_status"] = delivery
        if job.get("conversation_id") in self.conversations.items:
            value["conversation"] = self.load_conversation(job["conversation_id"])
        else:
            legacy = self._legacy_conversation_for_job(job)
            if legacy is not None:
                value["conversation"] = legacy
        return self._redact_public(value)

    def _secret_values(self) -> tuple[str, ...]:
        settings = self.settings()
        return tuple(secret for secret in (
            *self.private_secrets, settings.llm_api_key, settings.openalex_api_key,
        ) if secret)

    def _redact_public(self, value):
        return redact_secrets(value, self._secret_values())

    @staticmethod
    def _legacy_id(job_id: str) -> str:
        return hashlib.sha256(("paper-agent:legacy-conversation:" + job_id).encode()).hexdigest()[:32]

    @staticmethod
    def _legacy_message_id(job_id: str, kind: str) -> str:
        return hashlib.sha256(("paper-agent:legacy-message:" + job_id + ":" + kind).encode()).hexdigest()[:32]

    @staticmethod
    def _research_message(result: dict) -> str:
        brief = result.get("brief")
        if isinstance(brief, dict):
            findings = [item.get("statement", "") for item in brief.get("findings", [])
                        if isinstance(item, dict) and item.get("statement")]
            content = "\n".join(f"{index}. {statement}" for index, statement in enumerate(findings, 1))
            next_step = brief.get("next_step") or {}
            if isinstance(next_step, dict) and next_step.get("action"):
                content += ("\n\n" if content else "") + "推荐下一步：" + next_step["action"]
            if not content:
                content = "本次未形成可靠研究结论，请查看研究限制与执行记录。"
            if brief.get("report_artifact_id"):
                content += "\n\n完整报告请通过研究结果中的报告入口阅读。"
            if result.get("mode") == "demo":
                content = "【Demo 合成示例】\n\n" + content
            return content
        # Legacy records did not persist a brief. Keep their historical report
        # readable instead of destructively migrating or hiding the old content.
        report = result.get("report") or {}
        content = report.get("markdown") or report.get("overview")
        if not content:
            content = (f"研究已结束，已选择 {len(result.get('selected_papers', []))} 篇论文。"
                       "请查看资料与执行记录。")
        if result.get("mode") == "demo":
            content = "【Demo 合成示例】\n\n" + content
        return content

    def _legacy_conversation_for_job(self, job: dict) -> dict | None:
        if job.get("conversation_id") or not isinstance(job.get("job_id"), str):
            return None
        idea = job.get("idea") or job.get("query") or "旧版研究记录"
        status = job.get("status", "failed")
        action = job.get("action", "research")
        messages = [{
            "id": self._legacy_message_id(job["job_id"], "user"),
            "role": "user", "content": str(idea), "kind": action,
            "status": status, "created_at": job.get("created_at", ""),
            "job_id": job["job_id"],
        }]
        result = job.get("result")
        if action == "chat" and isinstance(job.get("assistant_text"), str) and job["assistant_text"]:
            content = job["assistant_text"]
            role, kind = "assistant", "chat"
        elif isinstance(result, dict):
            content = self._research_message(result)
            role, kind = "assistant", "research"
        else:
            content, role, kind = "", "assistant", "research"
        if content:
            messages.append({
                "id": self._legacy_message_id(job["job_id"], "assistant"),
                "role": role, "content": content, "kind": kind, "status": status,
                "created_at": job.get("updated_at", ""), "job_id": job["job_id"],
            })
        if status in {"failed", "cancelled", "partial", "partial_success"}:
            messages.append({
                "id": self._legacy_message_id(job["job_id"], "status"),
                "role": "status", "content": job.get("error") or "任务已结束，请查看执行记录。",
                "kind": "status", "status": status, "created_at": job.get("updated_at", ""),
                "job_id": job["job_id"],
            })
        return {
            "conversation_id": self._legacy_id(job["job_id"]),
            "title": str(idea)[:60], "updated_at": job.get("updated_at", ""),
            "status": status, "job_id": job["job_id"], "messages": messages,
            "result": result, "result_job_id": job["job_id"], "read_only": True,
        }

    def _legacy_conversation_by_id(self, identifier: str) -> dict | None:
        for job in self.jobs.values():
            if self._legacy_id(job.get("job_id", "")) == identifier:
                return self._legacy_conversation_for_job(job)
        return None

    def load_conversation(self, identifier: str) -> dict:
        item = self.conversations.items.get(identifier)
        if item is None:
            legacy = self._legacy_conversation_by_id(identifier)
            if legacy is None:
                raise UIError("会话不存在。", 404)
            return self._redact_public(legacy)
        research = self.jobs.get(item.get("research_job_id"), {})
        value = {key: value for key, value in item.items() if key != "research_job_id"} | {
            "result": research.get("result"), "result_job_id": research.get("job_id")}
        return self._redact_public(value)

    def list_conversations(self) -> list[dict]:
        rows = [{key: item[key] for key in ("conversation_id", "title", "updated_at", "status", "job_id")}
                for item in self.conversations.items.values()]
        rows.extend({key: conversation[key] for key in (
            "conversation_id", "title", "updated_at", "status", "job_id", "read_only")}
                    for job in self.jobs.values()
                    if (conversation := self._legacy_conversation_for_job(job)) is not None)
        rows.sort(key=lambda item: item["updated_at"], reverse=True)
        return self._redact_public(rows)

    def _sync_conversation(self, job: dict) -> None:
        item = self.conversations.items.get(job.get("conversation_id"))
        if item is None:
            return
        item["job_id"], item["status"] = job["job_id"], job["status"]
        item["updated_at"] = job["updated_at"]
        if job.get("action", "research") == "research":
            item["research_job_id"] = job["job_id"]
        if job["status"] not in ACTIVE:
            action = job.get("action", "research")
            if action == "chat" and job.get("assistant_text"):
                self.conversations.append(item, "assistant", job["assistant_text"],
                                          job_id=job["job_id"], key=job["job_id"] + ":answer")
            elif action == "research" and job.get("result"):
                content = self._research_message(job["result"])
                self.conversations.append(item, "assistant", content, kind="research",
                                          status=job["status"], job_id=job["job_id"],
                                          key=job["job_id"] + ":answer")
            if job["status"] in {"failed", "cancelled", "partial", "partial_success"}:
                content = job.get("error") or {"cancelled": "任务已停止，已有资料已保留。",
                                               "partial": "部分研究步骤未完成，请查看执行记录。",
                                               "partial_success": "部分研究步骤未完成，请查看执行记录。"}.get(
                                                   job["status"], "任务未能完成，请查看执行记录。")
                self.conversations.append(item, "status", content, kind="status", status=job["status"],
                                          job_id=job["job_id"], key=job["job_id"] + ":terminal")
        self.conversations.persist(item)

    @staticmethod
    def _execution_modes(requested: str, settings: Settings) -> tuple[str, str]:
        """Return the frozen public request and the actual internal mode."""
        canonical = "parallel" if requested == "concurrent" else requested
        if canonical == "auto":
            return canonical, settings.execution_mode
        return canonical, "sequential" if canonical == "sequential" else "concurrent"

    def submit_message(self, values: dict) -> dict:
        text, action = values.get("text", ""), values.get("action", "research")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000:
            raise UIError("请输入 1 至 12000 字的消息。")
        if not isinstance(action, str) or action not in {"research", "chat"}:
            raise UIError("消息动作需要为 research 或 chat。")
        if any(job["status"] in ACTIVE for job in self.jobs.values()):
            raise UIError("已有任务运行中，请先等待完成或停止任务。", 409)
        identifier = values.get("conversation_id")
        if identifier is not None:
            if isinstance(identifier, str) and self._legacy_conversation_by_id(identifier) is not None:
                raise UIError("旧版记录仅供阅读，请新建会话。", 409)
            if not isinstance(identifier, str) or identifier not in self.conversations.items:
                raise UIError("会话不存在。", 404)
            item = self.conversations.items[identifier]
        else:
            item = None
        settings = self.settings()
        if action == "chat" and not settings.llm_api_key:
            raise UIError("请先打开模型设置，填写 DeepSeek API Key。", 409)
        # Reuse the durable session/job lifecycle; the conversation is an added
        # presentation layer and never changes the research result contract.
        session = self.sessions.get(identifier)
        if session is None:
            if item is not None:
                # Conversations created here always own an existing session.
                raise UIError("会话的研究记录无法读取。", 409)
            created = self.create_session(text.strip()[:60])
            session = self.sessions[created["session_id"]]
        submit_values = {"idea": text.strip(), "query": values.get("query", ""),
                         "top_k": values.get("top_k", settings.top_k),
                         "mode": values.get("mode", "live"),
                         "execution_mode": values.get("execution_mode", "auto")}
        try:
            if action == "chat":
                if (not isinstance(submit_values["query"], str) or len(submit_values["query"]) > 1000
                        or isinstance(submit_values["top_k"], bool)
                        or not isinstance(submit_values["top_k"], int)
                        or not 1 <= submit_values["top_k"] <= 15
                        or submit_values["execution_mode"] not in EXECUTION_REQUESTS
                        or submit_values["mode"] not in ("live", "demo")):
                    raise UIError("讨论请求的研究选项格式不正确。")
            if action == "research":
                job_value = self.submit(session["session_id"], submit_values)
                job = self.jobs[job_value["job_id"]]
            else:
                stamp, job_id = now(), uuid4().hex
                requested, resolved = self._execution_modes(submit_values["execution_mode"], settings)
                job = submit_values | {"job_id": job_id, "session_id": session["session_id"],
                    "status": "pending", "delivery_status": None,
                    "requested_execution_mode": requested, "execution_mode": resolved,
                    "created_at": stamp, "updated_at": stamp,
                    "mode": "live", "result": None, "run_id": None,
                    "events": [], "error": None, "artifacts": [], "_event_seq": 0}
                self.jobs[job_id] = job
                session["job_ids"].append(job_id)
        except Exception:
            if item is None:
                self.sessions.pop(session["session_id"], None)
                (self.root / "sessions" / (session["session_id"] + ".json")).unlink(missing_ok=True)
            raise
        if item is None:
            item = self.conversations.create(text.strip(), session["session_id"])
        job["conversation_id"], job["action"] = item["conversation_id"], action
        self.conversations.append(item, "user", text.strip(), kind=action, job_id=job["job_id"])
        self.event(job, "status", message="研究已提交。" if action == "research" else "正在整理已有上下文，准备讨论。")
        if action == "chat":
            self.tasks[job["job_id"]] = asyncio.create_task(self._execute_chat(job, settings))
        return self.public_job(job)

    async def _execute_chat(self, job: dict, settings: Settings) -> None:
        final = "failed"
        client = None
        try:
            async with self.queue:
                job["status"] = "running"
                self.event(job, "status", message="正在基于已有资料回复；本次不会检索新论文。")
                item = self.conversations.items[job["conversation_id"]]
                research = self.jobs.get(item.get("research_job_id"), {})
                client = ChatClient(replace(settings, llm_max_calls=min(settings.llm_max_calls, 3), llm_max_output_tokens=min(
                    settings.llm_max_output_tokens, 3000)))
                async with asyncio.timeout(min(settings.llm_timeout * 3 + 30, 1830)):
                    answer = await client.chat(discussion_messages(item, research.get("result")))
                content = answer.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise LLMOutputError("模型没有返回讨论文本。")
                job["assistant_text"] = content.strip()
                final = "completed"
        except asyncio.CancelledError:
            final = "cancelled"
        except LLMError as error:
            job["error"] = "模型请求未能完成：" + str(error)
        except TimeoutError:
            job["error"] = "本次讨论请求超时，消息已保留，请稍后重试。"
        except Exception as error:
            job["error"] = "讨论未能完成。错误类型：" + type(error).__name__
        finally:
            if client is not None:
                # Only adapter-owned counters and call metadata are durable.
                # Never persist prompts, provider bodies, headers or settings.
                usage = getattr(client, "usage", None)
                if usage is not None:
                    job["llm_usage"] = {name: getattr(usage, name) for name in (
                        "logical_calls", "requests", "successful_requests", "failed_requests",
                        "retries", "invalid_outputs", "prompt_tokens", "completion_tokens",
                        "total_tokens", "usage_missing_responses")
                        if isinstance(getattr(usage, name, None), int)}
                job["llm_calls"] = []
                for entry in getattr(client, "call_log", []):
                    call = {name: entry[name] for name in (
                        "request_number", "attempt", "status", "http_status", "error_type") if name in entry}
                    if isinstance(entry.get("usage"), dict):
                        call["usage"] = {name: entry["usage"][name] for name in (
                            "prompt_tokens", "completion_tokens", "total_tokens")
                            if isinstance(entry["usage"].get(name), int)}
                    job["llm_calls"].append(call)
            job["status"] = final
            try:
                self.event(job, "status", message=job.get("error") or "讨论状态已更新。", data={"status": final})
            except OSError:
                pass
            finally:
                self.tasks.pop(job["job_id"], None)

    def public_session(self, session: dict) -> dict:
        return {key: value for key, value in session.items() if key != "job_ids"} | {
            "jobs": [self.public_job(self.jobs[job_id]) for job_id in session["job_ids"]
                     if job_id in self.jobs],
            "job_count": len(session["job_ids"]),
            "active": any(self.jobs.get(j, {}).get("status") in ACTIVE for j in session["job_ids"]),
        }

    def list_sessions(self) -> list[dict]:
        return [{key: value for key, value in session.items() if key != "job_ids"} | {
            "job_count": len(session["job_ids"]),
            "active": any(self.jobs.get(j, {}).get("status") in ACTIVE for j in session["job_ids"]),
        } for session in sorted(self.sessions.values(), key=lambda item: item["updated_at"], reverse=True)]

    def create_session(self, title: str = "新研究") -> dict:
        timestamp = now()
        session = {"session_id": uuid4().hex, "title": title, "created_at": timestamp,
                   "updated_at": timestamp, "job_ids": []}
        self.sessions[session["session_id"]] = session
        self.persist_session(session)
        return self.public_session(session)

    def submit(self, session_id: str, values: dict) -> dict:
        session = self.get_session(session_id)
        if any(self.jobs.get(j, {}).get("status") in ACTIVE for j in session["job_ids"]):
            raise UIError("这个会话还有任务在运行，请先等待完成或停止任务。", 409)
        idea, query = values.get("idea", ""), values.get("query", "")
        top_k, mode = values.get("top_k", 5), values.get("mode", "live")
        execution = values.get("execution_mode", "auto")
        if not isinstance(idea, str) or not 3 <= len(idea.strip()) <= 12000:
            raise UIError("请输入 3 至 12000 字的研究问题。")
        if not isinstance(query, str) or len(query) > 1000:
            raise UIError("英文检索词需要在 1000 字以内。")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 15:
            raise UIError("论文数量需要在 1 到 15 之间。")
        if mode not in {"live", "demo"} or execution not in EXECUTION_REQUESTS:
            raise UIError("运行模式不正确。")
        base_settings = self.settings()
        requested, resolved = self._execution_modes(execution, base_settings)
        settings = replace(base_settings, mode=mode,
                           processing="serial" if resolved == "sequential" else "concurrent")
        settings.validate()
        if top_k > settings.candidate_limit:
            raise UIError("论文数量超过当前候选上限，请调低 Top-k。")
        if mode == "live" and not settings.llm_api_key:
            raise UIError("请先打开模型设置，填写 DeepSeek API Key。", 409)
        timestamp, job_id = now(), uuid4().hex
        # New web jobs have their own output root; existing CLI runs stay untouched.
        settings = replace(settings, output_dir=self.root / "runs" / job_id)
        job = {"job_id": job_id, "session_id": session_id, "idea": idea.strip(), "query": query.strip(),
               "top_k": top_k, "mode": mode, "execution_mode": resolved,
               "requested_execution_mode": requested, "status": "pending", "delivery_status": None,
               "created_at": timestamp, "updated_at": timestamp, "run_id": None,
               "result": None, "events": [], "error": None, "artifacts": [],
               "_output_dir": str(settings.output_dir.resolve()), "_event_seq": 0}
        self.jobs[job_id] = job
        session["job_ids"].append(job_id)
        if len(session["job_ids"]) == 1 and session["title"] == "新研究":
            session["title"] = idea.strip()[:36]
        self.event(job, "status", message="研究任务已加入队列。", data={"status": "pending"})
        self.tasks[job_id] = asyncio.create_task(self._execute(job, settings))
        return self.public_job(job)

    @staticmethod
    def _public_result(run: PipelineResult) -> dict:
        value = run.model_dump(mode="json")
        # Full page text is available as a document, rather than repeated on each poll.
        for paper in value["papers"] + value["selected_papers"]:
            for page in paper["page_map"]:
                page.pop("text", None)
        return value

    @staticmethod
    def _material_papers(run: PipelineResult) -> dict[str, Paper]:
        """Merge prepared candidates with the original selection audit by stable ID.

        Prepared papers own file and reading state. The original selection remains
        the source for selection/ranking metadata when the prepared record lacks it.
        """
        merged = {paper.paper_id: paper.model_copy(deep=True) for paper in run.papers}
        prepared_fields = (
            "pdf_path", "markdown_path", "pdf_artifact_id", "markdown_artifact_id",
            "page_map", "fulltext_quality", "acquisition_status", "conversion_status",
            "reading_status",
        )
        selection_fields = (
            "title", "authors", "year", "abstract", "doi", "arxiv_id", "arxiv_version",
            "sources", "source_records", "paper_url", "pdf_url", "pdf_urls", "citation_count",
            "initial_score", "final_score", "rank", "selection_reason", "ranking_reason",
            "ranking_details",
        )
        for selected in run.selected_papers:
            current = merged.get(selected.paper_id)
            if current is None:
                merged[selected.paper_id] = selected.model_copy(deep=True)
                continue
            for field in prepared_fields:
                if _missing_paper_value(getattr(current, field, None)) and not _missing_paper_value(getattr(selected, field, None)):
                    setattr(current, field, getattr(selected, field))
            for field in selection_fields:
                if _missing_paper_value(getattr(current, field, None)) and not _missing_paper_value(getattr(selected, field, None)):
                    setattr(current, field, getattr(selected, field))
        return merged

    @staticmethod
    def _artifact_name(label: str, suffix: str) -> str:
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")[:140]
        return (base or "artifact") + suffix

    @staticmethod
    def _apply_artifact_ids(run: PipelineResult, artifacts: list[dict]) -> None:
        identifiers = {(item["kind"], item.get("paper_id")): item["artifact_id"]
                       for item in artifacts}
        if run.report is not None:
            run.report.report_artifact_id = identifiers.get(("report", None))
        for summary in run.summaries:
            summary.summary_artifact_id = identifiers.get(("summary", summary.paper_id))
        for paper in [*run.papers, *run.selected_papers]:
            paper.pdf_artifact_id = identifiers.get(("pdf", paper.paper_id))
            paper.markdown_artifact_id = identifiers.get(("markdown", paper.paper_id))
        evidence = [reference for summary in run.summaries for reference in summary.evidence]
        if run.brief is not None:
            evidence.extend(reference for finding in run.brief.findings for reference in finding.evidence)
        for reference in evidence:
            reference.pdf_artifact_id = identifiers.get(("pdf", reference.paper_id))
            reference.markdown_artifact_id = identifiers.get(("markdown", reference.paper_id))

    def _artifacts(self, job: dict, run: PipelineResult, settings: Settings) -> None:
        from hashlib import sha256

        roots = [Path(path).resolve() for path in (
            settings.data_dir, settings.output_dir, settings.pdf_dir, settings.markdown_dir,
            settings.summaries_dir, settings.reports_dir, run.output_dir,
        ) if path]
        candidates = []
        if run.report and run.report.report_path:
            candidates.append(("report", "研究报告.md", None, run.report.report_path))
        for summary in run.summaries:
            if summary.summary_path:
                candidates.append(("summary", self._artifact_name(
                    (summary.title or summary.paper_id) + "_总结", ".md"),
                    summary.paper_id, summary.summary_path))
                summary_path = Path(summary.summary_path)
                for suffix, kind, label in (
                    (".chunks.json", "chunks", "_分块证据.json"),
                    (".audit.json", "audit", "_总结审计.json"),
                ):
                    trace_path = summary_path.with_name(summary_path.stem + suffix)
                    if trace_path.is_file():
                        candidates.append((kind, self._artifact_name(
                            (summary.title or summary.paper_id) + label, ""),
                            summary.paper_id, str(trace_path)))
        # Prepared papers carry the actual post-download/conversion state; the
        # original selection is audit metadata. Merge by stable paper_id so an
        # unenriched selection record cannot erase a registered PDF or Markdown.
        for paper in self._material_papers(run).values():
            for kind, suffix, path in (("pdf", ".pdf", paper.pdf_path),
                                       ("markdown", ".md", paper.markdown_path)):
                if path:
                    candidates.append((kind, self._artifact_name(paper.title, suffix),
                                       paper.paper_id, path))
        if run.output_dir:
            candidates.append(("run", "run.json", None, str(Path(run.output_dir) / "run.json")))
        artifacts, paths, seen = [], {}, set()
        for kind, name, paper_id, filename in candidates:
            path = Path(filename).resolve()
            if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
                continue
            artifact_id = sha256(f"{kind}:{paper_id or 'report'}".encode()).hexdigest()[:20]
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            url = f"/api/jobs/{job['job_id']}/artifacts/{artifact_id}"
            artifacts.append({"artifact_id": artifact_id, "name": name, "kind": kind,
                              "paper_id": paper_id, "url": url, "download_url": url + "?download=1"})
            paths[artifact_id] = str(path)
        job["artifacts"], job["_artifact_paths"], job["_artifact_roots"] = artifacts, paths, [str(r) for r in roots]
        self._apply_artifact_ids(run, artifacts)

    def _job_settings(self, job: dict) -> Settings:
        output_dir = job.get("_output_dir")
        return replace(
            self.settings(), mode=job.get("mode", "live"),
            processing="serial" if job.get("execution_mode") == "sequential" else "concurrent",
            output_dir=Path(output_dir) if isinstance(output_dir, str) and output_dir else self.settings().output_dir,
        )

    def _refresh_brief(self, job: dict, run: PipelineResult) -> DeliveryStatus:
        """Rebuild the display brief from every real analysis material.

        This is a derived view only: it does not change execution status, source
        run files, or promote partial evidence to a formal result.
        """
        from paper_agent.agents.research_agent import build_research_brief

        brief, delivery_status = build_research_brief(
            papers=list(self._material_papers(run).values()),
            summaries=run.summaries,
            report=run.report,
        )
        run.brief = brief
        self._apply_artifact_ids(run, job.get("artifacts", []))
        registered_report = next((item["artifact_id"] for item in job.get("artifacts", [])
                                  if item["kind"] == "report"), None)
        if run.report is not None:
            run.report.report_artifact_id = registered_report
        brief.report_artifact_id = registered_report if run.report is not None else None
        return delivery_status

    def _finalize_terminal_result(self, job: dict, run: PipelineResult | None,
                                  settings: Settings, execution_status: str) -> PipelineResult:
        if run is None:
            run = PipelineResult(
                run_id=job.get("run_id") or job["job_id"], idea=job["idea"],
                query=job.get("query", ""), top_k=job.get("top_k", 5),
                mode=job.get("mode", "live"), output_dir=job.get("_output_dir"),
            )
        run.status = execution_status
        run.execution_mode = job.get("execution_mode", settings.execution_mode)
        requested = job.get("requested_execution_mode")
        if requested in {"auto", "parallel", "sequential"}:
            run.requested_execution_mode = requested
        self._artifacts(job, run, settings)

        # Agent 7 is the sole authority for evidence-based delivery classification.
        delivery_status = self._refresh_brief(job, run)
        run.delivery_status = delivery_status
        job["delivery_status"] = delivery_status
        job["result"] = self._public_result(run)
        return run

    @staticmethod
    def _terminal_message(execution_status: str, delivery_status: DeliveryStatus | None) -> str | None:
        if execution_status == "cancelled":
            return "任务已停止，已有资料已保留。"
        if delivery_status == "no_result":
            return "本次未形成可靠研究结果，请查看研究限制、推荐下一步和执行记录。"
        if delivery_status == "preliminary_notes":
            return "当前交付为初步研究笔记，证据或覆盖仍有缺口，请结合限制说明阅读。"
        if execution_status == "failed":
            return "研究未能完成，已有资料和执行记录已保留。"
        if execution_status == "partial_success":
            return "部分研究步骤未完成，请查看研究限制与执行记录。"
        return None

    def _restore_loaded_artifacts(self, job: dict) -> None:
        result = job.get("result")
        if not isinstance(result, dict):
            return
        try:
            run = PipelineResult.model_validate(result)
            self._artifacts(job, run, self._job_settings(job))
            if job.get("status") not in ACTIVE:
                self._refresh_brief(job, run)
            job["result"] = self._public_result(run)
            if job.get("status") not in ACTIVE and job.get("delivery_status") is None:
                # Copy an existing authoritative value only; legacy completed
                # jobs without one deliberately remain unclassified.
                job["delivery_status"] = run.delivery_status
        except (OSError, ValueError, TypeError, RuntimeError):
            # A damaged or unavailable old artifact must not hide the whole job.
            pass

    async def _sync_run(self, job: dict, settings: Settings) -> None:
        directory = settings.output_dir
        if job["run_id"]:
            directory = directory / job["run_id"]
        else:
            matches = list(directory.glob("*/run.json")) or list(directory.glob("*/trace.jsonl"))
            if not matches:
                return
            directory = matches[0].parent
            job["run_id"] = directory.name
        path = directory / "run.json"
        try:
            changed = path.stat().st_mtime_ns
            if changed != job.get("_run_mtime"):
                text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                run = PipelineResult.model_validate_json(text)
                job["result"] = self._public_result(run)
                job["_run_mtime"] = changed
                self._artifacts(job, run, settings)
                self.persist_job(job)
        except (OSError, ValueError):
            # An atomic writer may have just replaced the snapshot; next tick retries.
            pass
        trace = directory / "trace.jsonl"
        if not trace.is_file():
            return
        try:
            lines = (await asyncio.to_thread(trace.read_text, encoding="utf-8")).splitlines()
        except OSError:
            return
        count = job.get("_trace_lines", 0)
        for line in lines[count:]:
            try:
                item = json.loads(line)
            except ValueError:
                break
            message = item.get("message") if isinstance(item.get("message"), str) else ""
            # Persist the cursor in the same job snapshot as the event. A restart
            # therefore cannot replay an already committed trace line.
            count += 1
            job["_trace_lines"] = count
            self.event(job, "trace", stage=item.get("stage", ""), message=message, data=item)

    async def _watch(self, job: dict, settings: Settings) -> None:
        while True:
            await self._sync_run(job, settings)
            await asyncio.sleep(0.8)

    async def _execute(self, job: dict, settings: Settings) -> None:
        watcher = None
        final: str | None = None
        completed_run: PipelineResult | None = None

        def safe_event(kind: str, *, message: str = "", data=None) -> None:
            # Progress and error events must never abort a paid run or mask its
            # real outcome when the local snapshot is momentarily unwritable.
            with suppress(OSError):
                self.event(job, kind, message=message, data=data)

        try:
            async with self.queue:
                job["status"] = "running"
                safe_event("status", message="正在准备研究工作区。", data={"status": "running"})
                watcher = asyncio.create_task(self._watch(job, settings))

                def progress(message: str) -> None:
                    match = RUN_ID.search(message)
                    if match:
                        job["run_id"] = match.group(1)
                    safe_event("progress", message=message)

                from paper_agent.main_agent import run_research

                async with asyncio.timeout(7200):
                    if self.research_runner is None:
                        raw = await run_research(job["idea"], settings, job["top_k"], job["query"], progress)
                    else:
                        raw = await self.research_runner(job, settings, progress)
                completed_run = PipelineResult.model_validate(raw)
                job["run_id"] = completed_run.run_id
                final = ("completed" if completed_run.status in {"completed", "demo_completed"}
                         else "partial_success" if completed_run.status in {"partial", "partial_success"}
                         else completed_run.status)
                if final not in {"completed", "partial_success", "failed", "cancelled"}:
                    final = "partial_success"
        except asyncio.CancelledError:
            final = "cancelled"
        except TimeoutError:
            final = "failed"
            safe_event("error", message="任务超过两小时，已停止并保留已有资料。",
                       data={"exception_type": "TimeoutError"})
        except LLMError as error:
            final = "failed"
            # Provider details stay in the execution record. ChatClient errors
            # omit response bodies and credentials by contract.
            safe_event("error", message="模型请求未能完成：" + str(error),
                       data={"exception_type": type(error).__name__})
        except Exception as error:
            final = "failed"
            safe_event("error", message="模型或研究服务未能完成任务，已有资料仍可查看。",
                       data={"exception_type": type(error).__name__})
        finally:
            try:
                if watcher:
                    watcher.cancel()
                    # A watcher that died on a persistence error must not abort
                    # the terminal-status bookkeeping below.
                    with suppress(Exception, asyncio.CancelledError):
                        await watcher
                with suppress(Exception):
                    await self._sync_run(job, settings)
                # Finalize after the last snapshot sync so a run.json without the
                # new fields cannot overwrite the brief or delivery classification.
                if final is not None:
                    try:
                        run = completed_run
                        if run is None and isinstance(job.get("result"), dict):
                            run = PipelineResult.model_validate(job["result"])
                        run = self._finalize_terminal_result(job, run, settings, final)
                        job["error"] = (None if final == "cancelled" else
                                        self._terminal_message(final, run.delivery_status))
                        safe_event("execution_record", message=job.get("error") or "研究执行记录已保存。",
                                   data={"status": final, "delivery_status": run.delivery_status,
                                         "errors": [error.model_dump(mode="json") for error in run.errors]})
                    except Exception as error:
                        # A contract-integration failure remains explicit; never
                        # invent a delivery status to make the job look complete.
                        job["delivery_status"] = None
                        job["error"] = "研究结果整理失败，请查看执行记录并保留现有资料。"
                        safe_event("error", message=job["error"],
                                   data={"exception_type": type(error).__name__})
                    job["status"] = final
                # Clients read the in-memory status set above; an unwritable disk
                # reconciles the stale snapshot on the next service start.
                safe_event("status", message=job.get("error") or "任务状态已更新。",
                           data={"status": job["status"], "delivery_status": job.get("delivery_status")})
            finally:
                # A persistence failure must not leak the task slot and block
                # every later submission with "已有任务运行中".
                self.tasks.pop(job["job_id"], None)

    async def cancel(self, job: dict) -> dict:
        task = self.tasks.get(job["job_id"])
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if job["status"] in ACTIVE:
            job["status"] = "cancelled"
            self.event(job, "status", message="排队任务已取消。", data={"status": "cancelled"})
        self.tasks.pop(job["job_id"], None)
        return self.public_job(job)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def payload(request: Request, *, optional: bool = False) -> dict:
    raw = await request.body()
    if not raw and optional:
        return {}
    if len(raw) > 128 * 1024:
        raise UIError("输入过长。", 413)
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise UIError("请求格式需要为 JSON。", 415)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise UIError("请求内容不是有效 JSON。") from None
    if not isinstance(value, dict):
        raise UIError("请求内容需要是 JSON 对象。")
    return value


def state(request: Request) -> Workbench:
    return request.app.state.workbench


async def config_endpoint(request: Request) -> Response:
    manager = state(request)
    if request.method == "PUT":
        return JSONResponse(manager.save_config(await payload(request)))
    return JSONResponse(manager.public_config())


async def conversations_endpoint(request: Request) -> Response:
    return JSONResponse(state(request).list_conversations())


async def conversation_endpoint(request: Request) -> Response:
    return JSONResponse(state(request).load_conversation(request.path_params["conversation_id"]))


async def message_endpoint(request: Request) -> Response:
    return JSONResponse(state(request).submit_message(await payload(request)), status_code=202)


async def sessions_endpoint(request: Request) -> Response:
    manager = state(request)
    if request.method == "GET":
        return JSONResponse(manager.list_sessions())
    values = await payload(request, optional=True)
    title = values.get("title", "新研究")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise UIError("会话名称需要在 1 至 120 字之间。")
    return JSONResponse(manager.create_session(title.strip()), status_code=201)


async def session_endpoint(request: Request) -> Response:
    manager = state(request)
    session = manager.get_session(request.path_params["session_id"])
    if request.method == "PATCH":
        values = await payload(request)
        title = values.get("title", "")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise UIError("会话名称需要在 1 至 120 字之间。")
        session["title"], session["updated_at"] = title.strip(), now()
        manager.persist_session(session)
    elif request.method == "DELETE":
        if any(manager.jobs.get(j, {}).get("status") in ACTIVE for j in session["job_ids"]):
            raise UIError("请先停止正在运行的任务，再删除会话。", 409)
        # Remove UI history only; downloaded research artifacts remain available on disk.
        (manager.root / "sessions" / (session["session_id"] + ".json")).unlink(missing_ok=True)
        manager.sessions.pop(session["session_id"])
        return Response(status_code=204)
    return JSONResponse(manager.public_session(session))


async def jobs_endpoint(request: Request) -> Response:
    return JSONResponse(state(request).submit(request.path_params["session_id"], await payload(request)),
                        status_code=202)


async def job_endpoint(request: Request) -> Response:
    manager = state(request)
    return JSONResponse(manager.public_job(manager.get_job(request.path_params["job_id"])))


async def cancel_endpoint(request: Request) -> Response:
    manager = state(request)
    return JSONResponse(await manager.cancel(manager.get_job(request.path_params["job_id"])))


async def events_endpoint(request: Request) -> Response:
    manager = state(request)
    job = manager.get_job(request.path_params["job_id"])
    try:
        after = max(0, int(request.query_params.get("after", request.headers.get("last-event-id", "0"))))
    except ValueError:
        raise UIError("事件序号无效。") from None

    async def stream():
        last, heartbeat = after, time.monotonic()
        yield ": connected\n\n"
        while True:
            for event in list(job["events"]):
                if event["seq"] > last:
                    public_event = manager._redact_public(event)
                    yield f"id: {event['seq']}\nevent: update\ndata: {json.dumps(public_event, ensure_ascii=False)}\n\n"
                    last = event["seq"]
            if job["status"] not in ACTIVE:
                value = json.dumps(manager._redact_public(
                    {"job_id": job["job_id"], "status": job["status"],
                     "delivery_status": job.get("delivery_status")}), ensure_ascii=False)
                yield f"event: done\ndata: {value}\n\n"
                break
            if time.monotonic() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def artifact_text_endpoint(request: Request) -> Response:
    manager = state(request)
    job = manager.get_job(request.path_params["job_id"])
    try:
        value = artifact_text(job, request.path_params["artifact_id"])
    except ArtifactAccessError as error:
        raise UIError(error.message, error.status) from None
    return JSONResponse(manager._redact_public(value))


async def artifact_location_endpoint(request: Request) -> Response:
    manager = state(request)
    job = manager.get_job(request.path_params["job_id"])
    try:
        return JSONResponse(manager._redact_public(
            artifact_location(job, request.path_params["artifact_id"])))
    except ArtifactAccessError as error:
        raise UIError(error.message, error.status) from None


async def artifact_endpoint(request: Request) -> Response:
    manager = state(request)
    job = manager.get_job(request.path_params["job_id"])
    try:
        item, path = resolve_artifact(job, request.path_params["artifact_id"])
    except ArtifactAccessError as error:
        raise UIError(error.message, error.status) from None
    media = {"pdf": "application/pdf", "run": "application/json"}.get(item["kind"], "text/plain")
    download = request.query_params.get("download") == "1"
    return FileResponse(path, media_type=media, filename=item["name"] if download else None,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"X-Content-Type-Options": "nosniff"})


async def health_endpoint(request: Request) -> Response:
    return JSONResponse({"status": "ready", "provider": "deepseek", "frontend_built": FRONTEND_DIST.is_dir()})


async def frontend_endpoint(request: Request) -> Response:
    if request.path_params.get("path", "").startswith("api/"):
        return JSONResponse({"error": "接口不存在。"}, status_code=404)
    path = (FRONTEND_DIST / request.path_params.get("path", "")).resolve()
    root = FRONTEND_DIST.resolve()
    if not path.is_relative_to(root):
        return Response(status_code=404)
    if path.is_file():
        return FileResponse(path)
    index = root / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return Response("前端尚未构建。请运行 scripts/start-web.ps1，或在 frontend 目录运行 npm install 和 npm run build。",
                    media_type="text/plain", status_code=503)


class LocalOriginMiddleware:
    """Keep a local browser app's state-changing API same-origin."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            headers = dict(scope["headers"])
            raw = headers.get(b"origin")
            if raw:
                origin = urlsplit(raw.decode("latin-1"))
                authority = headers.get(b"host", b"").decode("latin-1")
                dev_origins = {"http://127.0.0.1:5173", "http://localhost:5173"}
                if origin.scheme not in {"http", "https"} or (
                    origin.netloc != authority and raw.decode("latin-1") not in dev_origins
                ):
                    await JSONResponse({"error": "请从本地研究工作区提交请求。"}, status_code=403)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> Starlette:
    @asynccontextmanager
    async def lifespan(app):
        app.state.workbench = Workbench(settings or Settings.from_env())
        try:
            yield
        finally:
            await app.state.workbench.close()

    async def ui_error(request: Request, error: UIError):
        return JSONResponse({"error": error.message}, status_code=error.status)

    return Starlette(lifespan=lifespan, exception_handlers={UIError: ui_error}, middleware=[
        Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]),
        Middleware(LocalOriginMiddleware),
    ], routes=[
        Route("/api/health", health_endpoint),
        Route("/api/config", config_endpoint, methods=["GET", "PUT"]),
        Route("/api/conversations", conversations_endpoint),
        Route("/api/conversations/messages", message_endpoint, methods=["POST"]),
        Route("/api/conversations/{conversation_id}", conversation_endpoint),
        Route("/api/sessions", sessions_endpoint, methods=["GET", "POST"]),
        Route("/api/sessions/{session_id}", session_endpoint, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/sessions/{session_id}/jobs", jobs_endpoint, methods=["POST"]),
        Route("/api/jobs/{job_id}", job_endpoint),
        Route("/api/jobs/{job_id}/cancel", cancel_endpoint, methods=["POST"]),
        Route("/api/jobs/{job_id}/events", events_endpoint),
        Route("/api/jobs/{job_id}/artifacts/{artifact_id}", artifact_endpoint),
        Route("/{path:path}", frontend_endpoint),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local DeepSeek paper research workbench")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    import uvicorn

    print(f"Paper research workbench: http://{args.host}:{args.port}", flush=True)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
