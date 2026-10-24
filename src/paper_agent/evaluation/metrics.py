"""Independent success checks, without trusting a pipeline's status label."""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from paper_agent.models import Paper, PipelineResult


def _file(path: str | None) -> bool:
    try:
        return bool(path and Path(path).is_file() and Path(path).stat().st_size > 0)
    except OSError:
        return False


def _fulltext(paper: Paper) -> bool:
    return bool(
        paper.status == "converted"
        and paper.fulltext_quality.get("complete") is True
        and _file(paper.pdf_path) and _file(paper.markdown_path) and paper.page_map
    )


def evaluate_result(result: PipelineResult, *, elapsed_seconds: float | None = None) -> dict[str, Any]:
    """Check persisted artifacts and scope; a frozen run cannot pass online acceptance.

    ``processing_success`` covers a fixed Top-k's full-text analysis and report.
    ``complete_success`` additionally requires both live sources and a real selector.
    Neither can be true for demo fixtures. These checks measure workflow completion,
    not the scientific correctness of a model's findings.
    """
    synthetic = bool(result.mode == "demo" or result.metrics.get("synthetic")
                     or any(p.metadata.get("synthetic") or p.fulltext_quality.get("synthetic")
                            or any(record.get("synthetic") for record in p.source_records)
                            for p in [*result.papers, *result.selected_papers])
                     or any(s.summary_scope == "demo_fixture" for s in result.summaries))
    frozen = bool(result.metrics.get("frozen_input"))
    selected = [paper.paper_id for paper in result.selected_papers]
    analysis = list(result.metrics.get("analysis_paper_ids", selected))
    by_id = {paper.paper_id: paper for paper in result.papers}
    # Selected metadata may predate full-text preparation; use the current candidates.
    by_id = {**{paper.paper_id: paper for paper in result.selected_papers}, **by_id}
    fulltext_ids = {paper_id for paper_id in analysis if paper_id in by_id and _fulltext(by_id[paper_id])}
    summary_ids = [summary.paper_id for summary in result.summaries]
    fulltext_summary_ids = {
        summary.paper_id for summary in result.summaries
        if summary.summary_scope == "fulltext" and not summary.errors
        and summary.research_question.strip() and summary.core_idea.strip()
        and summary.paper_id in fulltext_ids and _file(summary.summary_path)
    }
    usable = {summary.paper_id for summary in result.summaries
              if summary.summary_scope != "failed" and summary.research_question.strip()}
    report = result.report
    references = [reference.paper_id for reference in report.references] if report else []
    report_valid = bool(
        report and report.overview.strip() and report.markdown.strip() and _file(report.report_path)
        and not report.errors and len(references) == len(set(references))
        and set(references) == usable and usable
    )
    exact_counts = (
        len(selected) == len(set(selected)) == result.top_k
        and len(analysis) == len(set(analysis)) == result.top_k
        and len(summary_ids) == len(set(summary_ids)) == result.top_k
    )
    workflow_complete = bool(
        exact_counts and len(fulltext_ids) == result.top_k
        and fulltext_summary_ids == set(analysis) and report_valid
    )
    real_requests = int(result.metrics.get("llm_usage", {}).get("requests", 0)) > 0
    processing_success = bool(not synthetic and workflow_complete and real_requests)
    source_success = result.metrics.get("source_success", {})
    source_requests = result.metrics.get("source_requests", {})
    both_sources_online = bool(
        not synthetic and not frozen
        and all(source_success.get(source) is True for source in ("arxiv", "openalex"))
        and all(int(source_requests.get(source, 0)) > 0 for source in ("arxiv", "openalex"))
    )
    selector_real = (not result.metrics.get("injected_selection")
                     and not result.metrics.get("selector_degraded")
                     and not result.metrics.get("query_degraded"))
    complete_success = bool(processing_success and both_sources_online and selector_real and real_requests)
    # Keep recovered errors too; success and reliability are separate measurements.
    errors = [*result.errors, *(e for p in by_id.values() for e in p.errors),
              *(e for s in result.summaries for e in s.errors), *(report.errors if report else [])]
    unique_errors = {(error.stage, error.paper_id, error.code, error.message) for error in errors}
    completed_ids = ({s.paper_id for s in result.summaries
                      if s.summary_scope == "demo_fixture" and not s.errors}
                     if synthetic else fulltext_summary_ids)
    failed_ids = set(analysis) - completed_ids
    failed_ids.update(selected_id for selected_id in selected if selected_id not in fulltext_ids)
    timings = {timing.stage: timing.duration_seconds for timing in result.timings}
    timing_requests = sum(timing.request_count for timing in result.timings if timing.stage != "total")
    http_requests = int(result.metrics.get("http_requests", {}).get("total", 0))
    llm_requests = int(result.metrics.get("llm_usage", {}).get("requests", 0))
    return {
        "run_id": result.run_id, "data_mode": result.mode,
        "execution_mode": result.execution_mode, "status": result.status,
        "synthetic": synthetic, "frozen_input": frozen,
        "injected_selection": bool(result.metrics.get("injected_selection")),
        "candidate_count": len(result.papers), "selected_count": len(selected),
        "selected_ids": selected, "analysis_ids": analysis,
        "source_counts": result.source_counts, "both_sources_online": both_sources_online,
        "fulltext_count": len(fulltext_ids), "summary_count": len(result.summaries),
        "fulltext_summary_count": len(fulltext_summary_ids),
        "failed_paper_count": len(failed_ids), "missing_fulltext_count": max(0, result.top_k - len(fulltext_ids)),
        "error_count": len(unique_errors), "report_valid": report_valid,
        "workflow_complete": workflow_complete,
        "processing_success": processing_success, "complete_success": complete_success,
        "demo_smoke_complete": bool(synthetic and exact_counts and report and report.overview.strip()
                                    and report.markdown.strip() and _file(report.report_path)
                                    and len(fulltext_ids) == result.top_k
                                    and len(references) == len(set(references)) and set(references) == set(analysis)
                                    and all(e.code == "partial_report" for e in [*report.errors, *result.errors])
                                    and all(s.summary_scope == "demo_fixture" and not s.errors for s in result.summaries)),
        "elapsed_seconds": elapsed_seconds if elapsed_seconds is not None else timings.get("total", 0.0),
        "stage_seconds": timings, "http_request_count": http_requests,
        "llm_request_count": llm_requests,
        "request_count": http_requests + llm_requests if http_requests or llm_requests else timing_requests,
        "cache_hits": result.metrics.get("cache_hits", {}),
        "errors": [{"stage": stage, "paper_id": paper_id, "code": code, "message": message}
                   for stage, paper_id, code, message in sorted(unique_errors, key=str)],
    }


def summarize_rows(rows: list[dict[str, Any]], *, synthetic: bool = False) -> dict[str, Any]:
    """Use every attempt, including failures; report paired completeness separately."""
    synthetic = synthetic or any(row.get("synthetic") for row in rows)
    summary: dict[str, Any] = {}
    for mode in ("sequential", "concurrent"):
        attempts = [row for row in rows if row["execution_mode"] == mode]
        values = [float(row["elapsed_seconds"]) for row in attempts]
        stages = sorted({stage for row in attempts for stage in row.get("stage_seconds", {})})
        summary[mode] = {
            "attempts": len(attempts), "mean_seconds": statistics.mean(values) if values else None,
            "median_seconds": statistics.median(values) if values else None,
            "processing_success_rate": sum(bool(row.get("processing_success")) for row in attempts) / len(attempts) if attempts else 0.0,
            "complete_success_rate": sum(bool(row.get("complete_success")) for row in attempts) / len(attempts) if attempts else 0.0,
            "demo_smoke_success_rate": sum(bool(row.get("demo_smoke_complete")) for row in attempts) / len(attempts) if attempts else 0.0,
            "errors": sum(int(row.get("error_count", 0)) for row in attempts),
            "failed_papers": sum(int(row.get("failed_paper_count", 0)) for row in attempts),
            "requests": sum(int(row.get("request_count", 0)) for row in attempts),
            "mean_fulltext_count": statistics.mean(int(row.get("fulltext_count", 0)) for row in attempts) if attempts else 0,
            "mean_summary_count": statistics.mean(int(row.get("summary_count", 0)) for row in attempts) if attempts else 0,
            "mean_fulltext_summary_count": statistics.mean(int(row.get("fulltext_summary_count", 0)) for row in attempts) if attempts else 0,
            "mean_candidate_count": statistics.mean(int(row.get("candidate_count", 0)) for row in attempts) if attempts else 0,
            "stage_mean_seconds": {stage: statistics.mean(float(row.get("stage_seconds", {}).get(stage, 0)) for row in attempts) for stage in stages},
            "stage_median_seconds": {stage: statistics.median(float(row.get("stage_seconds", {}).get(stage, 0)) for row in attempts) for stage in stages},
        }
    sequential, concurrent = summary["sequential"], summary["concurrent"]
    all_complete = bool(rows and all(row.get("processing_success") and row.get("inputs_match", True)
                                    and int(row.get("llm_request_count", 0)) > 0 for row in rows))
    enough = min(sequential["attempts"], concurrent["attempts"]) >= 5
    comparable = bool(not synthetic and all_complete and enough)
    median = concurrent["median_seconds"]
    summary["median_speedup"] = sequential["median_seconds"] / median if comparable and median else None
    mean = concurrent["mean_seconds"]
    summary["mean_speedup"] = sequential["mean_seconds"] / mean if comparable and mean else None
    summary["performance_validated"] = comparable
    summary["notice"] = (
        "Synthetic smoke only; timings are not real API performance evidence."
        if synthetic else "All attempts retained; speedup requires >=5 complete, identical-input attempts per mode."
    )
    return summary
