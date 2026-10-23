import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from paper_agent.config import Settings
from paper_agent.pipeline.parallel import process_items
from paper_agent.service import ResearchService
from paper_agent.storage import RunStore


def test_demo_service_persists_full_lifecycle_and_rejects_early_report(tmp_path):
    async def scenario():
        service = ResearchService(Settings(output_dir=tmp_path))
        selected = await service.search_and_select("agent memory", 3)
        assert selected["merged_count"] == 6
        assert selected["dedup_count"] == 4
        with pytest.raises(ValueError, match="prepare_fulltexts"):
            await service.generate_report(selected["run_id"])
        await service.prepare_fulltexts(selected["run_id"])
        # A fresh service instance can resume the run using its persisted state.
        result = await ResearchService(Settings(output_dir=tmp_path)).generate_report(
            selected["run_id"]
        )
        assert result["status"] == "demo_completed"
        assert len(result["artifacts"]) == 3
        assert all(a["summary_path"] for a in result["artifacts"])
        assert "模拟" in Path(result["report_path"]).read_text(encoding="utf-8")
        assert await service.generate_report(selected["run_id"]) == result

    asyncio.run(scenario())


def test_fulltext_failure_is_partial_not_silent_abstract_fallback(tmp_path):
    async def scenario():
        service = ResearchService(Settings(output_dir=tmp_path))
        selected = await service.search_and_select("agent memory", 3)
        original = service.fulltext.prepare
        failed_id = selected["selected_papers"][0]["paper_id"]

        async def prepare(paper, directory):
            if paper.paper_id == failed_id:
                raise ValueError("No public PDF")
            return await original(paper, directory)

        service.fulltext.prepare = prepare
        await service.prepare_fulltexts(selected["run_id"])
        result = await service.generate_report(selected["run_id"])
        assert result["status"] == "partial"
        failed = next(a for a in result["artifacts"] if a["paper_id"] == failed_id)
        assert failed["summary_path"] is None
        assert failed["fulltext_status"] == "failed"
        assert "PARTIAL" in Path(result["report_path"]).read_text(encoding="utf-8")

    asyncio.run(scenario())


@pytest.mark.parametrize("mode,expected_peak", [("serial", 1), ("concurrent", 2)])
def test_bounded_processing_preserves_order_and_item_failures(mode, expected_peak):
    async def scenario():
        active = peak = 0

        async def operation(item):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0)
                if item == 2:
                    raise ValueError("bad input")
                return item * 2
            finally:
                active -= 1

        result = await process_items([0, 1, 2, 3], operation, mode, 2)
        assert result[:2] == [0, 2]
        assert isinstance(result[2], ValueError)
        assert result[3] == 6
        assert peak == expected_peak

    asyncio.run(scenario())


def test_store_rejects_path_traversal_and_cross_mode_resume(tmp_path):
    with pytest.raises(ValueError):
        RunStore(tmp_path).directory("../../outside")

    async def scenario():
        demo = ResearchService(Settings(output_dir=tmp_path))
        result = await demo.search_and_select("agent memory", 1)
        live = ResearchService(replace(demo.settings, mode="live"))
        with pytest.raises(ValueError, match="mode"):
            await live.prepare_fulltexts(result["run_id"])

    asyncio.run(scenario())


def test_real_mcp_stdio_subprocess_end_to_end(tmp_path):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    command = [
        sys.executable,
        "-m",
        "paper_agent.cli",
        "agent memory",
        "--mode",
        "demo",
        "--processing",
        "concurrent",
        "--output-dir",
        str(tmp_path),
        "--json",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", env=env, timeout=90
    )
    assert result.returncode == 0, result.stderr
    run = json.loads(result.stdout)
    assert run["status"] == "demo_completed"
    assert run["source_counts"] == {"arxiv": 3, "openalex": 3}
    trace = (tmp_path / run["run_id"] / "trace.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in trace.splitlines()]
    assert [event["tool"] for event in events if event["stage"] == "main_agent_tool_call"] == [
        "search_and_select",
        "prepare_fulltexts",
        "generate_report",
    ]
    assert all(
        event["controller"] == "scripted_demo"
        for event in events
        if event["stage"] == "main_agent_tool_call"
    )
