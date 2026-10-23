import asyncio
from dataclasses import replace

import pytest

from paper_agent.agents import reporter, selector
from paper_agent.config import Settings
from paper_agent.schemas import PaperRecord


def test_live_selector_retries_invalid_candidate_then_validates(monkeypatch):
    calls = []

    class FakeChat:
        def __init__(self, settings):
            pass

        async def json(self, system, user):
            calls.append(user)
            return {
                "papers": [
                    {
                        "paper_id": "invented" if len(calls) == 1 else "real-id",
                        "rank": 1,
                        "selection_reason": "title evidence",
                    }
                ]
            }

    monkeypatch.setattr(selector, "ChatClient", FakeChat)
    agent = selector.SelectorAgent(replace(Settings(), mode="live"))
    result = asyncio.run(
        agent.select("agent memory", [PaperRecord(paper_id="real-id", title="Agent Memory")], 1)
    )
    assert len(calls) == 2
    assert result[0].paper_id == "real-id"
    assert result[0].selection_basis == "title_abstract"


def test_partial_fulltext_coverage_is_reported_not_hidden(monkeypatch):
    calls = []

    class FakeChat:
        def __init__(self, settings):
            pass

        async def text(self, system, user):
            calls.append(user)
            return "有依据的测试总结，不代表真实模型输出。"

    monkeypatch.setattr(reporter, "ChatClient", FakeChat)
    settings = replace(Settings(), mode="live", chunk_chars=1000, max_chunks=1)
    result, coverage = asyncio.run(
        reporter.ReporterAgent(settings).summarize(
            PaperRecord(paper_id="p1", title="Paper"), "x" * 2500, "research idea"
        )
    )
    assert "chunks 1/3" in coverage
    assert "partial_fulltext" in result
    assert len(calls) == 2  # One read chunk plus one merge; hidden truncation is not allowed.


def test_live_missing_configuration_fails_before_any_request():
    from paper_agent.llm import ChatClient

    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        ChatClient(Settings(mode="live"))
