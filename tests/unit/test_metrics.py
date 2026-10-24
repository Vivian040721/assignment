from pathlib import Path

import pytest

from paper_agent.evaluation.metrics import evaluate_result, summarize_rows
from paper_agent.models import (
    PageMapping,
    Paper,
    PaperReference,
    PaperSummary,
    PipelineResult,
    ResearchReport,
    StageTiming,
)


def complete_run(tmp_path: Path) -> PipelineResult:
    papers, summaries = [], []
    for number in range(3):
        paper_id = f"p{number}"
        pdf, markdown, summary = [tmp_path / f"{paper_id}.{suffix}" for suffix in ("pdf", "md", "summary")]
        pdf.write_bytes(b"%PDF-test")
        markdown.write_text("Methods and findings", encoding="utf-8")
        summary.write_text("A persisted summary", encoding="utf-8")
        papers.append(Paper(paper_id=paper_id, title=paper_id, pdf_path=str(pdf), markdown_path=str(markdown),
                            status="converted", fulltext_quality={"complete": True},
                            page_map=[PageMapping(page_number=1, text="Methods and findings", end_char=20)]))
        summaries.append(PaperSummary(paper_id=paper_id, title=paper_id, summary_scope="fulltext",
                                      research_question="Question", core_idea="Method", summary_path=str(summary)))
    report_path = tmp_path / "report.md"
    report_path.write_text("A report", encoding="utf-8")
    report = ResearchReport(idea="Agent memory", overview="Overview", markdown="A report",
                            report_path=str(report_path),
                            references=[PaperReference(paper_id=p.paper_id, title=p.title) for p in papers])
    return PipelineResult(idea="Agent memory", mode="live", top_k=3, papers=papers,
                          selected_papers=papers, summaries=summaries, report=report, status="completed",
                          metrics={"source_success": {"arxiv": True, "openalex": True},
                                   "source_requests": {"arxiv": 1, "openalex": 1},
                                   "llm_usage": {"requests": 4}, "http_requests": {"total": 2}})


def test_completed_label_without_fulltexts_is_not_success():
    row = evaluate_result(PipelineResult(idea="Agent memory", status="completed"))
    assert row["complete_success"] is False
    assert row["processing_success"] is False
    assert row["missing_fulltext_count"] == 5


def test_complete_success_requires_online_sources_and_physical_model_requests(tmp_path):
    run = complete_run(tmp_path)
    assert evaluate_result(run)["complete_success"] is True
    run.metrics["llm_usage"]["requests"] = 0
    row = evaluate_result(run)
    assert row["complete_success"] is False
    assert row["processing_success"] is False
    assert row["workflow_complete"] is True
    run.metrics["llm_usage"]["requests"] = 4
    run.metrics["source_success"]["openalex"] = False
    assert evaluate_result(run)["complete_success"] is False


def test_source_success_flag_alone_is_not_online_verification(tmp_path):
    run = complete_run(tmp_path)
    run.metrics["source_requests"]["openalex"] = 0
    assert evaluate_result(run)["both_sources_online"] is False


def test_query_fallback_does_not_pass_online_acceptance(tmp_path):
    run = complete_run(tmp_path)
    run.metrics["query_degraded"] = True
    row = evaluate_result(run)
    assert row["processing_success"] is True
    assert row["complete_success"] is False


def test_frozen_input_passes_processing_but_never_online_acceptance(tmp_path):
    run = complete_run(tmp_path)
    run.metrics.update(frozen_input=True, injected_selection=True)
    row = evaluate_result(run)
    assert row["processing_success"] is True
    assert row["complete_success"] is False
    assert row["both_sources_online"] is False


@pytest.mark.parametrize("scope", ["abstract_only", "partial_fulltext", "failed", "demo_fixture"])
def test_summary_scope_cannot_be_upgraded_by_status(tmp_path, scope):
    run = complete_run(tmp_path)
    run.summaries[0].summary_scope = scope
    row = evaluate_result(run)
    assert row["processing_success"] is False
    assert row["complete_success"] is False


def test_missing_file_and_invalid_report_references_fail(tmp_path):
    run = complete_run(tmp_path)
    Path(run.papers[0].pdf_path).unlink()
    assert evaluate_result(run)["fulltext_count"] == 2
    run = complete_run(tmp_path)
    run.report.references[0].paper_id = "invented"
    assert evaluate_result(run)["report_valid"] is False


def test_request_counts_do_not_double_count_total_timing(tmp_path):
    run = complete_run(tmp_path)
    run.timings = [StageTiming(stage="selection", duration_seconds=1, request_count=4),
                   StageTiming(stage="total", duration_seconds=2, request_count=4)]
    assert evaluate_result(run)["request_count"] == 6


def test_failed_fast_attempts_stay_in_all_sample_statistics():
    rows = [{"execution_mode": mode, "elapsed_seconds": value,
             "processing_success": index != 0, "complete_success": False,
             "error_count": int(index == 0), "failed_paper_count": int(index == 0)}
            for mode in ("sequential", "concurrent") for index, value in enumerate((1, 2, 3, 4, 5))]
    summary = summarize_rows(rows)
    assert summary["sequential"]["mean_seconds"] == 3
    assert summary["sequential"]["processing_success_rate"] == .8
    assert summary["sequential"]["attempts"] == 5
    assert summary["median_speedup"] is None


def test_demo_never_gets_a_performance_speedup():
    rows = [{"execution_mode": mode, "elapsed_seconds": 2 if mode == "sequential" else 1,
             "processing_success": True, "complete_success": False, "inputs_match": True,
             "llm_request_count": 1}
            for mode in ("sequential", "concurrent") for _ in range(5)]
    assert summarize_rows(rows)["median_speedup"] == 2
    assert summarize_rows(rows, synthetic=True)["median_speedup"] is None


def test_zero_request_fake_cannot_validate_performance():
    rows = [{"execution_mode": mode, "elapsed_seconds": 1, "processing_success": True,
             "complete_success": False, "inputs_match": True, "llm_request_count": 0}
            for mode in ("sequential", "concurrent") for _ in range(5)]
    assert summarize_rows(rows)["performance_validated"] is False
