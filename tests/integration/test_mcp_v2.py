"""Real SDK v2 transports with offline business dependencies and model mocks."""

import asyncio
import json
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from paper_agent.config import Settings
from paper_agent.demo_fixtures import DemoLLM
from paper_agent.main_agent import (
    ALL_RESEARCH_TOOLS,
    STAGE_TOOLS,
    run_connected_research,
    run_research,
    server_environment,
    unpack_result,
)
from paper_agent.models import PipelineResult
from paper_agent.server import create_server
from paper_agent.services.pipeline import ResearchPipeline


def test_six_tools_via_real_in_memory_mcp(tmp_path):
    async def scenario():
        settings = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "files")
        async with Client(create_server(ResearchPipeline(settings))) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == ALL_RESEARCH_TOOLS
            assert all(isinstance(tool.input_schema, dict) for tool in tools)
            result = unpack_result(await client.call_tool("search_papers", {"idea": "agent memory token costs", "k": 3}))
            early = await client.call_tool("generate_research_report", {"run_id": result["run_id"]})
            assert early.is_error
            invalid = await client.call_tool("prepare_fulltexts", {"run_id": "../outside"})
            assert invalid.is_error
            for name in STAGE_TOOLS[1:]:
                result = unpack_result(await client.call_tool(name, {"run_id": result["run_id"]}))
            assert result["status"] == "demo_completed"
            assert len(result["summaries"]) == 3
            direct = unpack_result(await client.call_tool("run_research_pipeline", {"idea": "agent memory", "k": 3}))
            assert direct["status"] == "demo_completed"
    asyncio.run(scenario())


def test_stdio_client_runs_scripted_demo_and_records_calls(tmp_path):
    settings = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "files")
    result = asyncio.run(run_research("如何利用长期记忆减少 Agent 的 Token 消耗", settings, 3))
    assert result["status"] == "demo_completed"
    events = [json.loads(line) for line in (Path(result["output_dir"]) / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = [event for event in events if event["stage"] == "main_agent_tool_call"]
    assert [call["tool"] for call in calls] == list(STAGE_TOOLS)
    assert all(call["controller"] == "scripted_demo" for call in calls)
    assert [call["tool"] for call in result["tool_calls"]] == list(STAGE_TOOLS)
    assert all(call["duration_seconds"] >= 0 for call in result["tool_calls"])
    assert {"selector", "research_summarize", "research_report"} <= {event["stage"] for event in events}
    assert "total" in {timing["stage"] for timing in result["timings"]}
    assert result["metrics"]["physical_llm_requests"] == 0
    persisted = json.loads((Path(result["output_dir"]) / "run.json").read_text(encoding="utf-8"))
    assert persisted["metrics"]["main_agent_wall_seconds"] > 0
    assert persisted["metrics"] == result["metrics"]


def test_stdio_v2_discovery_is_real(tmp_path):
    import sys
    async def scenario():
        config = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "files")
        params = StdioServerParameters(command=sys.executable, args=["-m", "paper_agent.server"],
                                       env=server_environment(config))
        async with Client(params, read_timeout_seconds=60) as client:
            assert {tool.name for tool in (await client.list_tools()).tools} == ALL_RESEARCH_TOOLS
    asyncio.run(scenario())


class TerminalMockPipeline:
    """Protocol-only fake: it deliberately reports failed rather than live success."""
    async def run_research_pipeline(self, idea, k=5, query=""):
        return PipelineResult(idea=idea, top_k=k, query=query, mode="live", status="failed")


class ToolChoiceMock:
    async def chat(self, messages, tools=None):
        request = json.loads(messages[1]["content"])
        return {"role": "assistant", "tool_calls": [{"id": "mock-call", "type": "function",
                "function": {"name": "run_research_pipeline", "arguments": json.dumps(request)}}]}


def test_main_agent_accepts_model_selected_full_pipeline_tool(tmp_path):
    async def scenario():
        config = Settings(mode="live", output_dir=tmp_path / "runs", data_dir=tmp_path / "files")
        async with Client(create_server(TerminalMockPipeline())) as client:
            result = await run_connected_research(client, "agent memory", config, 3, llm=ToolChoiceMock())
            assert result["status"] == "failed"
            events = (config.output_dir / result["run_id"] / "trace.jsonl").read_text(encoding="utf-8")
            assert '"controller": "model_tool_call"' in events
    asyncio.run(scenario())


def test_main_agent_recovers_from_tool_error_feedback(tmp_path):
    class RecoveringMock:
        def __init__(self):
            self.calls = 0
            self.tool_feedback = []

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return {"role": "assistant", "tool_calls": [{
                    "id": "bad-call", "type": "function",
                    "function": {"name": "generate_research_report",
                                 "arguments": json.dumps({"run_id": "missing"})},
                }]}
            if self.calls == 2:
                for message in messages:
                    if message.get("role") == "tool":
                        self.tool_feedback.append(message.get("content", ""))
                request = json.loads(messages[1]["content"])
                return {"role": "assistant", "tool_calls": [{
                    "id": "full-call", "type": "function",
                    "function": {"name": "run_research_pipeline", "arguments": json.dumps(request)},
                }]}
            return {"role": "assistant", "content": "terminal"}

    async def scenario():
        config = Settings(mode="live", output_dir=tmp_path / "runs", data_dir=tmp_path / "files")
        model = RecoveringMock()
        async with Client(create_server(TerminalMockPipeline())) as client:
            result = await run_connected_research(client, "agent memory", config, 3, llm=model)
        return result, model

    result, model = asyncio.run(scenario())
    assert result["status"] == "failed"
    assert any('"code": "tool_call_rejected"' in item or '"code":"tool_call_rejected"' in item
               for item in model.tool_feedback)
    assert model.calls == 2


def test_main_agent_rejects_premature_text_completion(tmp_path):
    class NoTools:
        async def chat(self, messages, tools=None):
            return {"role": "assistant", "content": "Finished."}
    async def scenario():
        config = Settings(mode="live", output_dir=tmp_path / "runs", data_dir=tmp_path / "files")
        async with Client(create_server(TerminalMockPipeline())) as client:
            with pytest.raises(RuntimeError, match="终态"):
                await run_connected_research(client, "agent memory", config, 3, llm=NoTools())
            with pytest.raises(ValueError, match="合成"):
                await run_connected_research(client, "agent memory", config, 3, llm=DemoLLM())
    asyncio.run(scenario())


def test_environment_forwards_budgets_and_cache_policy(tmp_path):
    config = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "files", cache_mode="off",
                      llm_max_calls=7, processing="concurrent", download_concurrency=2,
                      merged_candidate_limit=30, selector_input_token_budget=48000,
                      report_input_token_budget=64000, max_fulltext_attempts=30)
    env = server_environment(config)
    assert env["LLM_MAX_CALLS"] == "7"
    assert env["PAPER_AGENT_CACHE_MODE"] == "off"
    assert env["PAPER_AGENT_PROCESSING"] == "concurrent"
    assert env["PAPER_AGENT_DOWNLOAD_CONCURRENCY"] == "2"
    assert env["PAPER_AGENT_MERGED_CANDIDATE_LIMIT"] == "30"
    assert env["PAPER_AGENT_SELECTOR_INPUT_TOKEN_BUDGET"] == "48000"
    assert env["PAPER_AGENT_REPORT_INPUT_TOKEN_BUDGET"] == "64000"
    assert env["PAPER_AGENT_MAX_FULLTEXT_ATTEMPTS"] == "30"
    assert env["PAPER_AGENT_DOWNLOAD_CANDIDATE_TIMEOUT"] == "45.0"
