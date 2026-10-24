"""Deterministic orchestration checks; fixtures are not live model validation."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from paper_agent.config import Settings
from paper_agent.demo_fixtures import DemoLLM, demo_papers, prepare_demo_paper
from paper_agent.models import ErrorInfo
from paper_agent.services.pipeline import ResearchPipeline


def settings_for(tmp_path, **kwargs):
    return Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "files", **kwargs)


def test_demo_runs_both_task_agents_and_persists_scopes(tmp_path):
    settings = settings_for(tmp_path)
    result = asyncio.run(ResearchPipeline(settings).run_research_pipeline("agent memory token cost", 3))
    assert result.status == "demo_completed"
    assert result.source_counts == {"arxiv": 12, "openalex": 12}
    assert len(result.papers) == 18
    assert all(summary.summary_scope == "demo_fixture" for summary in result.summaries)
    assert result.metrics["physical_llm_requests"] == 0
    assert result.metrics["online_complete"] is False
    assert result.metrics["processing_complete"] is True
    trace = [json.loads(line) for line in (Path(result.output_dir) / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {"selector", "research_summarize", "research_report"} <= {event["stage"] for event in trace}
    assert {timing.stage for timing in result.timings} == {"search", "selection", "fulltexts", "summarize", "report", "total"}


def test_original_selection_survives_ordered_replacement(tmp_path):
    async def fail_first(paper, *, settings):
        if paper.paper_id == "demo:001":
            failed = paper.model_copy(deep=True)
            failed.status = "download_failed"
            failed.errors.append(ErrorInfo(code="fixture_download_failure", message="Explicit test failure", stage="download", paper_id=paper.paper_id))
            return failed
        return await prepare_demo_paper(paper, settings=settings)

    result = asyncio.run(ResearchPipeline(settings_for(tmp_path), download_fn=fail_first).run_research_pipeline("agent memory token cost", 3))
    assert [paper.paper_id for paper in result.selected_papers] == ["demo:001", "demo:002", "demo:003"]
    assert result.metrics["analysis_paper_ids"] == ["demo:002", "demo:003", "demo:004"]
    assert result.metrics["replacements"] == [{"original_paper_id": "demo:001", "replacement_paper_id": "demo:004"}]
    fulltext_timing = next(timing for timing in result.timings if timing.stage == "fulltexts")
    assert fulltext_timing.error_count == 1
    assert result.status == "demo_completed"


def test_one_source_failure_cannot_complete(tmp_path):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("explicit source fixture failure")

    result = asyncio.run(ResearchPipeline(settings_for(tmp_path), search_arxiv_fn=unavailable).run_research_pipeline("agent memory token cost", 3))
    assert result.status == "partial"
    assert result.metrics["source_success"] == {"arxiv": False, "openalex": True}
    assert result.metrics["processing_complete"] is True
    assert next(t for t in result.timings if t.stage == "search").error_count == 1


def test_explicit_query_is_used_verbatim_without_planner(tmp_path):
    async def forbidden(*args, **kwargs):
        pytest.fail("An explicit English query must not invoke the planner")

    async def source(query, limit, *, settings=None, client=None):
        return demo_papers("arxiv", 4)

    async def scenario():
        pipeline = ResearchPipeline(settings_for(tmp_path), query_planner_fn=forbidden,
                                    search_arxiv_fn=source, search_openalex_fn=source)
        return await pipeline.search_papers("agent memory token cost", 3, "exact memory query")

    result = asyncio.run(scenario())
    assert result.query == "exact memory query"
    assert result.queries == ["exact memory query"]
    assert result.metrics["query_plan"]["mode"] == "explicit"
    assert result.metrics["source_requests"] == {"arxiv": 0, "openalex": 0}
    assert result.source_counts == {"arxiv": 4, "openalex": 4}


def test_untranslated_idea_never_hits_sources(tmp_path):
    async def untranslated(*args, **kwargs):
        return {"queries": [], "ranking_query": "", "mode": "needs_translation", "warnings": [], "errors": []}

    async def forbidden(*args, **kwargs):
        pytest.fail("A source request must not occur without an English query")

    result = asyncio.run(ResearchPipeline(settings_for(tmp_path), query_planner_fn=untranslated,
                                         search_arxiv_fn=forbidden, search_openalex_fn=forbidden).run_research_pipeline("长期记忆与经验复用", 3))
    assert result.status == "failed"
    assert result.errors[0].code == "translation_required"
    assert not result.papers


def test_stage_dependencies_run_id_and_resume_configuration(tmp_path):
    async def scenario():
        pipeline = ResearchPipeline(settings_for(tmp_path))
        run = await pipeline.search_papers("agent memory", 3)
        with pytest.raises(ValueError, match="selection"):
            await pipeline.prepare_fulltexts(run.run_id)
        with pytest.raises(ValueError, match="run_id"):
            await pipeline.select_top_k_papers("../secret")
        concurrent = ResearchPipeline(replace(pipeline.settings, processing="concurrent"))
        with pytest.raises(ValueError, match="原始模式"):
            await concurrent.select_top_k_papers(run.run_id)
        live = ResearchPipeline(replace(pipeline.settings, mode="live"))
        with pytest.raises(ValueError, match="原始模式"):
            await live.select_top_k_papers(run.run_id)
    asyncio.run(scenario())


def test_frozen_input_does_not_invent_online_source_success(tmp_path):
    result = asyncio.run(ResearchPipeline(settings_for(tmp_path)).run_research_pipeline(
        "agent memory", 3, papers=demo_papers("arxiv", 6)))
    assert result.metrics["frozen_input"] is True
    assert result.metrics["source_success"] == {"arxiv": False, "openalex": False}
    assert result.metrics["processing_complete"] is True
    assert result.metrics["online_complete"] is False
    assert result.status == "partial"


def test_partial_documents_do_not_complete(tmp_path):
    async def partial(paper, *, settings):
        value = paper.model_copy(deep=True)
        value.status = "partial_fulltext"
        value.fulltext_quality["complete"] = False
        return value

    result = asyncio.run(ResearchPipeline(settings_for(tmp_path), convert_fn=partial).run_research_pipeline("agent memory", 3))
    assert result.status == "partial"
    assert result.metrics["fulltext_count"] == 0
    assert result.metrics["processing_complete"] is False
    assert len(result.metrics["attempted_paper_ids"]) == 18


def test_concurrency_is_bounded_and_candidate_order_stable(tmp_path):
    async def scenario():
        active, peak = 0, 0
        async def measured_download(paper, *, settings):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            # Controlled scheduling fixture; not a performance measurement.
            await asyncio.sleep(.002)
            result = await prepare_demo_paper(paper, settings=settings)
            active -= 1
            return result
        config = settings_for(tmp_path, processing="concurrent", download_concurrency=2)
        result = await ResearchPipeline(config, download_fn=measured_download).run_research_pipeline("agent memory", 5)
        assert peak == 2
        assert result.metrics["analysis_paper_ids"] == [f"demo:{i:03d}" for i in range(1, 6)]
        assert result.metrics["attempted_paper_ids"] == result.metrics["analysis_paper_ids"]
        assert result.status == "demo_completed"
    asyncio.run(scenario())


def test_live_rejects_synthetic_model(tmp_path):
    with pytest.raises(ValueError, match="合成"):
        ResearchPipeline(settings_for(tmp_path, mode="live"), llm=DemoLLM())
