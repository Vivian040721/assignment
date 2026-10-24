"""Conversation persistence and discussion boundaries; all model calls are injected."""

import asyncio
from pathlib import Path

import pytest

from paper_agent.config import Settings
from paper_agent.conversations import ConversationStore
from paper_agent.llm_client import LLMUsage
from paper_agent.models import PipelineResult, ResearchReport
from paper_agent.web import UIError, Workbench


def bench_at(tmp_path, **kwargs):
    return Workbench(Settings(data_dir=tmp_path, llm_api_key="test-only-placeholder"),
                     root=tmp_path / "service", **kwargs)


def test_conversation_persist_survives_transient_windows_file_lock(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path / "conversations")
    item = store.create("agent memory")
    original = Path.replace
    attempts = {"count": 0}

    def flaky(self, target):
        if self.name.endswith(".tmp") and attempts["count"] < 3:
            attempts["count"] += 1
            raise PermissionError(13, "scanner still holds the file")
        return original(self, target)

    monkeypatch.setattr("paper_agent.conversations.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(Path, "replace", flaky)
    store.append(item, "user", "hello")
    assert attempts["count"] == 3
    reopened = ConversationStore(tmp_path / "conversations")
    assert reopened.items[item["conversation_id"]]["messages"][-1]["content"] == "hello"


def test_conversation_persist_reports_a_persistent_file_lock(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path / "conversations")
    item = store.create("agent memory")

    def blocked(self, target):
        raise PermissionError(13, "held by another process")

    monkeypatch.setattr("paper_agent.conversations.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(Path, "replace", blocked)
    with pytest.raises(PermissionError):
        store.append(item, "user", "hello")
    assert not list((tmp_path / "conversations").glob("*.tmp"))


async def research_runner(job, settings, progress):
    return PipelineResult(idea=job["idea"], mode="demo", status="demo_completed",
                          report=ResearchReport(idea=job["idea"], markdown="A bounded example report.")).model_dump(mode="json")


def test_discussion_reuses_evidence_without_research_and_reopens_without_duplicates(tmp_path, monkeypatch):
    calls = []

    class Chat:
        def __init__(self, settings):
            self.usage = LLMUsage()
            self.call_log = []

        async def chat(self, messages):
            calls.append(messages)
            self.usage.logical_calls = self.usage.requests = self.usage.successful_requests = 1
            self.call_log.append({"request_number": 1, "attempt": 1, "status": "success",
                                  "http_status": 200, "prompt": "must never persist this field"})
            return {"content": "This is a discussion grounded in the saved report."}

    monkeypatch.setattr("paper_agent.web.ChatClient", Chat)

    async def scenario():
        bench = bench_at(tmp_path, research_runner=research_runner)
        first = bench.submit_message({"text": "agent memory", "action": "research", "mode": "demo"})
        await bench.tasks[first["job_id"]]
        cid = first["conversation_id"]

        async def forbidden(*args):
            raise AssertionError("discussion must never execute retrieval or research")

        bench.research_runner = forbidden
        second = bench.submit_message({"text": "What remains uncertain?", "action": "chat", "conversation_id": cid})
        await bench.tasks[second["job_id"]]
        bench._sync_conversation(bench.jobs[second["job_id"]])
        return cid, first["job_id"], second["job_id"], bench.load_conversation(cid)

    cid, research_id, chat_id, before = asyncio.run(scenario())
    reopened = bench_at(tmp_path)
    after = reopened.load_conversation(cid)
    assert [message["content"] for message in after["messages"]] == [message["content"] for message in before["messages"]]
    assert len(after["messages"]) == 4
    assert after["job_id"] == chat_id
    assert after["result_job_id"] == research_id
    assert after["result"]["report"]["markdown"] == "A bounded example report."
    assert "A bounded example report." not in calls[0][0]["content"]
    assert "A bounded example report." in calls[0][1]["content"]
    assert calls[0][1]["role"] == "user"
    assert "untrusted_research_documents" in calls[0][1]["content"]
    assert "demo" in calls[0][1]["content"]
    assert calls[0][-1] == {"role": "user", "content": "What remains uncertain?"}
    assert "test-only-placeholder" not in str(after)
    chat_job = reopened.public_job(reopened.jobs[chat_id])
    assert chat_job["llm_usage"]["requests"] == 1
    assert chat_job["llm_calls"] == [{"request_number": 1, "attempt": 1, "status": "success", "http_status": 200}]


def test_cancel_discussion_keeps_user_message_and_one_terminal_status(tmp_path, monkeypatch):
    started = asyncio.Event()

    class Chat:
        def __init__(self, settings):
            pass

        async def chat(self, messages):
            started.set()
            await asyncio.Future()

    monkeypatch.setattr("paper_agent.web.ChatClient", Chat)

    async def scenario():
        bench = bench_at(tmp_path)
        job = bench.submit_message({"text": "Discuss a research question", "action": "chat"})
        await started.wait()
        await bench.cancel(bench.jobs[job["job_id"]])
        return job["conversation_id"]

    cid = asyncio.run(scenario())
    conversation = bench_at(tmp_path).load_conversation(cid)
    assert conversation["status"] == "cancelled"
    assert conversation["result"] is None
    assert [message["role"] for message in conversation["messages"]] == ["user", "status"]


def test_missing_configuration_does_not_persist_a_paid_job_or_empty_conversation(tmp_path):
    bench = Workbench(Settings(data_dir=tmp_path), root=tmp_path / "service")
    with pytest.raises(UIError, match="API Key"):
        bench.submit_message({"text": "Discuss this question", "action": "chat"})
    assert not bench.jobs
    assert not bench.list_conversations()


def test_preferences_apply_to_pipeline_settings_and_public_return_hides_key(tmp_path):
    bench = bench_at(tmp_path)
    public = bench.save_config({"default_top_k": 7, "concurrency": 4, "llm_timeout": 45,
                                "http_timeout": 20, "api_key": "test-config-placeholder"})
    actual = bench.settings()
    assert (actual.top_k, actual.concurrency, actual.download_concurrency, actual.llm_concurrency) == (7, 4, 4, 4)
    assert (actual.llm_timeout, actual.http_timeout) == (45, 20)
    assert public["default_top_k"] == 7
    assert "api_key" not in public
    with pytest.raises(UIError):
        bench.save_config({"llm_timeout": float("nan")})


def test_openalex_key_is_persistent_private_and_only_explicitly_cleared(tmp_path):
    bench = bench_at(tmp_path)
    public = bench.save_config({"openalex_api_key": "openalex-test-only-placeholder"})
    assert public["openalex_api_key_configured"] is True
    assert "openalex_api_key" not in public
    assert "openalex-test-only-placeholder" not in str(public)
    reopened = bench_at(tmp_path)
    assert reopened.settings().openalex_api_key == "openalex-test-only-placeholder"
    reopened.save_config({"openalex_api_key": "  "})
    assert reopened.settings().openalex_api_key == "openalex-test-only-placeholder"
    with pytest.raises(UIError):
        reopened.save_config({"clear_openalex_api_key": "yes"})
    assert reopened.settings().openalex_api_key == "openalex-test-only-placeholder"
    cleared = reopened.save_config({"clear_openalex_api_key": True})
    assert cleared["openalex_api_key_configured"] is False
    assert bench_at(tmp_path).settings().openalex_api_key == ""


def test_legacy_preferences_fall_back_to_environment_openalex_key(tmp_path):
    initial = bench_at(tmp_path)
    initial.save_config({"model": "deepseek-flash"})
    bench = Workbench(Settings(data_dir=tmp_path, openalex_api_key="env-test-only-placeholder"),
                      root=tmp_path / "service")
    assert bench.settings().openalex_api_key == "env-test-only-placeholder"
    assert bench.public_config()["openalex_api_key_configured"] is True
    bench.save_config({"openalex_api_key": ""})
    assert bench.settings().openalex_api_key == "env-test-only-placeholder"
    bench.save_config({"clear_openalex_api_key": True})
    assert bench.settings().openalex_api_key == ""
