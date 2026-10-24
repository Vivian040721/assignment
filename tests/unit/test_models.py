from dataclasses import replace

import pytest
from pydantic import ValidationError

from paper_agent.config import Settings
from paper_agent.models import (
    ErrorInfo,
    PageMapping,
    Paper,
    PaperReference,
    PaperSummary,
    PipelineResult,
    ResearchReport,
    StageTiming,
    ToolCallRecord,
)


def test_pipeline_contract_roundtrip_and_independent_defaults():
    first = Paper(paper_id="arxiv:1234.56789", title="Agent memory", arxiv_version="v2")
    second = Paper(paper_id="oa:W2", title="Another paper")
    first.sources.append("arxiv")
    assert second.sources == []
    summary = PaperSummary(
        paper_id=first.paper_id, summary_scope="partial_fulltext", warnings=["One unread page"]
    )
    report = ResearchReport(
        idea="Memory", references=[PaperReference(paper_id=first.paper_id, title=first.title)]
    )
    result = PipelineResult(
        idea="Memory", papers=[first, second], selected_papers=[first], summaries=[summary],
        report=report, timings=[StageTiming(stage="search", duration_seconds=1.2)],
        errors=[ErrorInfo(code="PARTIAL", message="One unread page", stage="conversion")],
    )
    restored = PipelineResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.top_k == 5
    assert restored.summaries[0].summary_scope == "partial_fulltext"


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan")])
def test_scores_are_normalized(score):
    with pytest.raises(ValidationError):
        Paper(paper_id="p", title="P", initial_score=score)


def test_invalid_scope_offsets_and_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        PaperSummary(paper_id="p", summary_scope="pretend_fulltext")
    with pytest.raises(ValidationError):
        PageMapping(page_number=1, start_char=10, end_char=5)
    with pytest.raises(ValidationError):
        Paper(paper_id="p", title="P", silently_ignored=True)


def test_tool_call_records_accumulate_in_pipeline_result():
    result = PipelineResult(idea="Memory")
    assert result.tool_calls == []
    result.tool_calls.append(
        ToolCallRecord(tool="search_papers", arguments={"idea": "Memory", "k": 3},
                       duration_seconds=0.5, status="demo_completed")
    )
    result.tool_calls.append(
        ToolCallRecord(tool="select_top_k_papers", arguments={"run_id": result.run_id},
                       duration_seconds=0.2, status="demo_completed", controller="model_tool_call")
    )
    restored = PipelineResult.model_validate_json(result.model_dump_json())
    assert [call.tool for call in restored.tool_calls] == ["search_papers", "select_top_k_papers"]
    assert restored.tool_calls[0].controller == "model_tool_call"
    with pytest.raises(ValidationError):
        ToolCallRecord(tool="search_papers", duration_seconds=-1)
    with pytest.raises(ValidationError):
        ToolCallRecord(tool="search_papers", controller="hardcoded_chain")


def test_settings_compatible_and_secret_repr_safe(tmp_path):
    settings = Settings(data_dir=tmp_path, llm_api_key="secret-value", openalex_api_key="key")
    settings.validate()
    assert settings.pdf_dir == tmp_path / "pdfs"
    assert settings.markdown_dir == tmp_path / "markdown"
    assert settings.execution_mode == "sequential"
    assert replace(settings, processing="concurrent").execution_mode == "concurrent"
    assert "secret-value" not in repr(settings)
    assert "openalex_api_key" not in repr(settings)
    assert not (tmp_path / "pdfs").exists()


def test_environment_configuration_and_invalid_boolean(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_AGENT_TOP_K", "7")
    monkeypatch.setenv("PAPER_AGENT_CACHE_ENABLED", "false")
    settings = Settings.from_env()
    assert settings.top_k == 7
    assert settings.cache_enabled is False
    assert settings.pdf_dir == tmp_path / "pdfs"
    monkeypatch.setenv("PAPER_AGENT_CACHE_ENABLED", "maybe")
    with pytest.raises(ValueError, match="boolean"):
        Settings.from_env()
