import asyncio
import csv
import json
from dataclasses import replace

import pytest

from paper_agent.config import Settings
from paper_agent.demo_fixtures import DemoLLM
from paper_agent.evaluation.benchmark import benchmark
from paper_agent.services.pipeline import ResearchPipeline


def demo_settings(tmp_path):
    return Settings(mode="demo", top_k=3, output_dir=tmp_path / "baseline",
                    data_dir=tmp_path, pdf_dir=tmp_path / "pdfs", markdown_dir=tmp_path / "markdown",
                    summaries_dir=tmp_path / "summaries", reports_dir=tmp_path / "reports")


def test_default_five_paired_runs_freeze_selection_and_keep_raw_artifacts(tmp_path):
    async def scenario():
        settings = demo_settings(tmp_path)
        run = await ResearchPipeline(settings).run_research_pipeline("Agent memory token costs", k=3)
        return await benchmark(run, settings, output=tmp_path / "benchmark")
    report = asyncio.run(scenario())
    rows = report["rows"]
    assert len(rows) == 10
    assert [row["execution_mode"] for row in rows[:4]] == ["sequential", "concurrent", "concurrent", "sequential"]
    assert len({row["input_sha256"] for row in rows}) == 1
    assert len({row["configuration_sha256"] for row in rows}) == 1
    assert len({row["processed_input_sha256"] for row in rows}) == 1
    assert all(row["inputs_match"] for row in rows)
    assert all(row["selected_ids"] == report["metadata"]["paper_ids"] for row in rows)
    assert all(row["demo_smoke_complete"] for row in rows)
    assert not any(row["complete_success"] for row in rows)
    assert report["metadata"]["real_api_validation"] == "not_run"
    assert report["summary"]["median_speedup"] is None
    with (tmp_path / "benchmark/results.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 10
    assert len(list((tmp_path / "benchmark/runs").glob("*/result.json"))) == 10


def test_one_failed_attempt_is_preserved_and_later_attempts_continue(tmp_path):
    calls = 0
    def factory(settings, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("private-provider-key-must-not-leak")
        return ResearchPipeline(settings, **kwargs)
    async def scenario():
        settings = demo_settings(tmp_path)
        run = await ResearchPipeline(settings).run_research_pipeline("Agent memory token costs", k=3)
        return await benchmark(run, settings, output=tmp_path / "benchmark", pipeline_factory=factory)
    report = asyncio.run(scenario())
    assert len(report["rows"]) == 10
    assert report["rows"][1]["status"] == "failed"
    assert report["rows"][-1]["demo_smoke_complete"] is True
    raw = (tmp_path / "benchmark/results.json").read_text(encoding="utf-8")
    assert "private-provider-key" not in raw
    assert json.loads(raw)["summary"]["concurrent"]["attempts"] == 5
    assert report["summary"]["concurrent"]["errors"] >= 1


def test_live_benchmark_rejects_synthetic_inputs_and_models(tmp_path):
    async def scenario():
        settings = demo_settings(tmp_path)
        run = await ResearchPipeline(settings).run_research_pipeline("Agent memory token costs", k=3)
        run.mode = "live"
        live = replace(settings, mode="live", llm_base_url="https://fake.invalid/v1", llm_model="fake", llm_api_key="fake")
        with pytest.raises(ValueError, match="synthetic"):
            await benchmark(run, live, output=tmp_path / "rejected", pipeline_kwargs={"llm": DemoLLM()})
    asyncio.run(scenario())


def test_cold_policy_reconverts_frozen_pdfs_in_isolated_directories(tmp_path, monkeypatch):
    from paper_agent.services import pdf_converter
    original = pdf_converter.convert_pdf
    conversion_settings = []
    async def observed_conversion(paper, *, settings=None):
        conversion_settings.append(settings)
        return await original(paper, settings=settings)
    monkeypatch.setattr(pdf_converter, "convert_pdf", observed_conversion)
    async def scenario():
        settings = demo_settings(tmp_path)
        run = await ResearchPipeline(settings).run_research_pipeline("Agent memory token costs", k=3)
        return await benchmark(run, settings, repeats=1, cache_policy="cold", output=tmp_path / "cold")
    report = asyncio.run(scenario())
    assert len(conversion_settings) == 6
    assert all(not settings.cache_enabled and settings.cache_mode == "off" for settings in conversion_settings)
    assert len({settings.markdown_dir for settings in conversion_settings}) == 2
    assert all(row["inputs_match"] for row in report["rows"])
    assert report["metadata"]["measurement_scope"] == "frozen_pdf_conversion_and_analysis"
    assert report["summary"]["median_speedup"] is None


def test_serial_await_and_bounded_concurrency_are_real_properties(tmp_path):
    # Controlled delay belongs only to this test, never benchmark/report data.
    async def scenario(mode):
        settings = replace(demo_settings(tmp_path / mode), processing=mode, llm_concurrency=2)
        active, peak = 0, 0
        async def delayed_summary(idea, paper, *, settings=None, llm=None):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.01)
            active -= 1
            from paper_agent.models import PaperSummary
            return PaperSummary(paper_id=paper.paper_id, summary_scope="failed")
        await ResearchPipeline(settings, summarize_fn=delayed_summary).run_research_pipeline("Agent memory costs", k=3)
        return peak
    assert asyncio.run(scenario("serial")) == 1
    assert asyncio.run(scenario("concurrent")) == 2
