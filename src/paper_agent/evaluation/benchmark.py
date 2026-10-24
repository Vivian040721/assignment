"""Paired measurements on frozen Top-k documents, never a live-search speed claim."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_agent.config import Settings
from paper_agent.evaluation.metrics import evaluate_result, summarize_rows
from paper_agent.models import ErrorInfo, Paper, PipelineResult


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configuration(settings: Settings) -> dict[str, Any]:
    # Keys, proxy credentials and endpoint strings never enter experiment artifacts.
    return {
        "model": settings.llm_model or "none-demo",
        "thinking_enabled": settings.llm_thinking_enabled,
        "endpoint_sha256": hashlib.sha256(settings.llm_base_url.encode()).hexdigest(),
        "llm_timeout": settings.llm_timeout, "chunk_chars": settings.chunk_chars,
        "max_chunks": settings.max_chunks,
        "llm_max_output_tokens": settings.llm_max_output_tokens,
        "llm_input_token_budget": settings.llm_input_token_budget,
        "llm_max_calls": settings.llm_max_calls,
        "llm_max_total_tokens": settings.llm_max_total_tokens,
        "llm_max_retries": settings.llm_max_retries,
        "download_concurrency": settings.download_concurrency,
        "conversion_concurrency": settings.conversion_concurrency,
        "llm_concurrency": settings.llm_concurrency,
        "sampling": "No sampling override; identical provider defaults in both modes.",
    }


def _prompt_hashes() -> dict[str, str]:
    agents = Path(__file__).resolve().parents[1] / "agents"
    return {name: _hash_file(agents / name)
            for name in ("selector_agent.py", "research_agent.py")}


def _freeze(run: PipelineResult, output: Path) -> tuple[list[Paper], dict[str, Any]]:
    selected_ids = [paper.paper_id for paper in run.selected_papers]
    if len(selected_ids) != run.top_k or len(set(selected_ids)) != run.top_k:
        raise ValueError("Benchmark requires exactly the original unique Top-k selection")
    by_id = {**{paper.paper_id: paper for paper in run.selected_papers},
             **{paper.paper_id: paper for paper in run.papers}}
    frozen: list[Paper] = []
    records = []
    snapshot_dir = output / "inputs"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for index, paper_id in enumerate(selected_ids):
        original = by_id[paper_id]
        paper = original.model_copy(deep=True)
        files: dict[str, dict[str, str] | None] = {}
        for field, suffix in (("pdf_path", ".pdf"), ("markdown_path", ".md")):
            source = getattr(original, field)
            target = snapshot_dir / f"{index + 1:03d}{suffix}"
            if source and Path(source).is_file():
                target.write_bytes(Path(source).read_bytes())
                files[field] = {"path": str(target.resolve()), "sha256": _hash_file(target)}
                setattr(paper, field, str(target.resolve()))
            else:
                files[field] = None
                setattr(paper, field, None)
        # The original error codes stay in the manifest, not in each new attempt.
        paper.errors, paper.error = [], None
        paper.metadata = {key: value for key, value in paper.metadata.items()
                          if key not in {"downloads", "conversion"}}
        paper.metadata["benchmark_snapshot"] = True
        frozen.append(paper)
        canonical = paper.model_dump(mode="json", exclude={"pdf_path", "markdown_path", "errors", "error"})
        records.append({"paper_id": paper_id, "paper": canonical, "files": files,
                        "original_status": original.status,
                        "original_error_codes": [error.code for error in original.errors]})
    manifest = {"paper_ids": selected_ids, "records": records}
    # Content identity must not depend on the output directory's absolute path.
    manifest["input_sha256"] = _digest({"paper_ids": selected_ids, "records": [
        {**record, "files": {key: file["sha256"] if file else None
                              for key, file in record["files"].items()}} for record in records]})
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return frozen, manifest


def _snapshot_unchanged(manifest: dict[str, Any]) -> bool:
    try:
        return all(_hash_file(Path(file["path"])) == file["sha256"]
                   for record in manifest["records"] for file in record["files"].values() if file)
    except OSError:
        return False


def _write_rows(output: Path, rows: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    if not metadata["synthetic"]:
        metadata["real_api_validation"] = ("measured_processing_stage_only"
                                           if any(row.get("llm_request_count", 0) > 0 for row in rows)
                                           else "attempted_but_no_observed_api_requests")
    summary = summarize_rows(rows, synthetic=metadata["synthetic"])
    report = {"metadata": metadata, "rows": rows, "summary": summary}
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                              for key, value in row.items()} for row in rows)
    return report


async def benchmark(
    run: PipelineResult, settings: Settings, repeats: int = 5,
    output: Path | str | None = None, *, cache_policy: str = "warm",
    pipeline_factory: Callable[..., Any] | None = None,
    pipeline_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare fixed Top-k using AB/BA order; retain every failed attempt.

    Warm reuses frozen PDF/Markdown; cold additionally repeats PDF conversion.
    Neither measures retrieval, online download or LLM selection. No artificial
    delays are added. Injected dependencies suppress performance validation.
    """
    if not 1 <= repeats <= 100:
        raise ValueError("repeats must be between 1 and 100 (use at least 5 for performance)")
    if cache_policy not in {"cold", "warm"}:
        raise ValueError("cache_policy must be cold or warm")
    settings = replace(settings, mode=run.mode)
    settings.validate()
    injected = dict(pipeline_kwargs or {})
    if set(injected) & {"selector_fn", "download_fn", "convert_fn"}:
        raise ValueError("Benchmark owns fixed selection and document input dependencies")
    if run.mode == "live":
        settings.require_llm()
        if (run.metrics.get("synthetic")
                or any(p.metadata.get("synthetic") or p.fulltext_quality.get("synthetic")
                       or any(record.get("synthetic") for record in p.source_records)
                       for p in [*run.papers, *run.selected_papers])
                or any(s.summary_scope == "demo_fixture" for s in run.summaries)
                or getattr(injected.get("llm"), "synthetic", False)):
            raise ValueError("Live benchmark rejects synthetic documents or model dependencies")
    output = Path(output or Path("data/benchmarks") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).resolve()
    if (output / "results.json").exists() or (output / "inputs/manifest.json").exists():
        raise ValueError("Use a new output directory to preserve previous measurements")
    output.mkdir(parents=True, exist_ok=True)
    frozen, manifest = _freeze(run, output)
    frozen_ids = [paper.paper_id for paper in frozen]
    config, prompt_hashes = _configuration(settings), _prompt_hashes()
    dependency_keys = sorted(injected)
    synthetic = run.mode == "demo" or pipeline_factory is not None or bool(dependency_keys)
    metadata: dict[str, Any] = {
        "source_run_id": run.run_id, "data_mode": run.mode, "synthetic": synthetic,
        "measurement_scope": "frozen_markdown_analysis" if cache_policy == "warm" else "frozen_pdf_conversion_and_analysis",
        "excluded_stages": ["online_search", "online_download", "llm_selection"],
        "input_sha256": manifest["input_sha256"], "paper_ids": frozen_ids,
        "model_configuration": config, "configuration_sha256": _digest(config),
        "prompt_source_sha256": prompt_hashes, "repeats_per_mode": repeats,
        "order": "AB/BA alternating; A=sequential, B=concurrent",
        "injected_selection": True, "injected_dependencies": dependency_keys,
        "cache_policy": cache_policy,
        "local_cache_rule": "Identical read-only frozen PDF/Markdown inputs; no model-result cache."
            if cache_policy == "warm" else "Identical frozen PDF inputs; conversion repeated; local caches disabled.",
        "provider_cache": "Uncontrolled provider-side caching may exist; not a controlled variable.",
        "complete_success_definition": "Live two-source retrieval, real selector, k complete fulltexts, k fulltext summaries and valid persisted report; frozen runs cannot pass.",
        "processing_success_definition": "k fixed complete PDF/Markdown documents, k fulltext summaries and valid persisted report; synthetic data never qualifies.",
        "real_api_validation": "not_run" if synthetic else "measured_processing_stage_only",
        "note": "Synthetic fixture timings are smoke only, never optimization evidence." if synthetic else "Completion checks do not replace human evidence and scientific-quality review.",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    async def fixed_selection(idea, papers, k, *, settings=None, llm=None):
        return [paper.model_copy(deep=True, update={"rank": index + 1}) for index, paper in enumerate(frozen[:k])]

    async def frozen_pdf(paper, *, settings=None, client=None):
        result = paper.model_copy(deep=True)
        result.metadata["downloads"] = {"request_count": 0, "cache_hit": True, "source": "frozen_input"}
        if not result.pdf_path or not Path(result.pdf_path).is_file():
            result.status, result.error = "no_open_access", "No PDF in the fixed benchmark input"
            result.errors.append(ErrorInfo(code="benchmark_missing_pdf", message=result.error,
                                           stage="download", paper_id=result.paper_id))
        else:
            result.status = "downloaded"
        return result

    async def frozen_markdown(paper, *, settings=None):
        result = paper.model_copy(deep=True)
        if result.markdown_path and Path(result.markdown_path).is_file() and result.page_map:
            result.status = "converted" if result.fulltext_quality.get("complete") else "partial_fulltext"
            result.fulltext_quality["cache_hit"] = True
        else:
            result.status, result.error = "conversion_failed", "No mapped Markdown in the fixed benchmark input"
            result.errors.append(ErrorInfo(code="benchmark_missing_markdown", message=result.error,
                                           stage="conversion", paper_id=result.paper_id))
        return result

    from paper_agent.services.pdf_converter import convert_pdf
    from paper_agent.services.pipeline import ResearchPipeline

    factory = pipeline_factory or ResearchPipeline
    rows: list[dict[str, Any]] = []
    processed_reference: str | None = None
    for repeat in range(repeats):
        order = ("sequential", "concurrent") if repeat % 2 == 0 else ("concurrent", "sequential")
        for position, mode in enumerate(order):
            attempt_dir = output / "runs" / f"{repeat + 1:02d}-{mode}"
            attempt_settings = replace(
                settings, processing="serial" if mode == "sequential" else "concurrent",
                output_dir=attempt_dir, data_dir=attempt_dir,
                pdf_dir=attempt_dir / "pdfs", markdown_dir=attempt_dir / "markdown",
                summaries_dir=attempt_dir / "summaries", reports_dir=attempt_dir / "reports",
                cache_enabled=cache_policy == "warm", cache_mode="read_only" if cache_policy == "warm" else "off",
            )
            inputs_match = _snapshot_unchanged(manifest) and prompt_hashes == _prompt_hashes()
            pipeline = None
            started = time.perf_counter()
            try:
                if not inputs_match:
                    raise ValueError("Frozen benchmark inputs or prompts changed")
                pipeline = factory(attempt_settings, selector_fn=fixed_selection, download_fn=frozen_pdf,
                                   convert_fn=frozen_markdown if cache_policy == "warm" else convert_pdf, **injected)
                result = await pipeline.run_research_pipeline(
                    run.idea, k=run.top_k, query=run.query, papers=[paper.model_copy(deep=True) for paper in frozen],
                )
                elapsed = time.perf_counter() - started
                row = evaluate_result(result, elapsed_seconds=elapsed)
                (attempt_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
                documents = {paper.paper_id: paper for paper in result.papers}
                processed_sha = _digest([
                    {"paper_id": paper_id,
                     "markdown_sha256": _hash_file(Path(documents[paper_id].markdown_path))
                         if paper_id in documents and documents[paper_id].markdown_path
                         and Path(documents[paper_id].markdown_path).is_file() else None,
                     "page_map": [page.model_dump() for page in documents[paper_id].page_map]
                         if paper_id in documents else []}
                    for paper_id in frozen_ids])
                if processed_reference is None:
                    processed_reference = processed_sha
                row["processed_input_sha256"] = processed_sha
                inputs_match = (inputs_match and _snapshot_unchanged(manifest)
                                and row["selected_ids"] == frozen_ids
                                and row["analysis_ids"] in (frozen_ids, [])
                                and processed_sha == processed_reference
                                and _prompt_hashes() == prompt_hashes)
            except Exception as error:
                elapsed = time.perf_counter() - started
                row = {"run_id": None, "data_mode": run.mode, "execution_mode": mode,
                       "status": "failed", "synthetic": synthetic, "frozen_input": True,
                       "injected_selection": True, "candidate_count": len(frozen),
                       "selected_count": len(frozen), "selected_ids": frozen_ids, "analysis_ids": [],
                       "source_counts": {}, "both_sources_online": False, "fulltext_count": 0,
                       "summary_count": 0, "fulltext_summary_count": 0,
                       "failed_paper_count": run.top_k, "missing_fulltext_count": run.top_k,
                       "error_count": 1, "report_valid": False, "processing_success": False,
                       "complete_success": False, "workflow_complete": False, "demo_smoke_complete": False,
                       "elapsed_seconds": elapsed, "stage_seconds": {}, "http_request_count": 0,
                       "llm_request_count": 0, "request_count": 0, "cache_hits": {},
                       "errors": [{"stage": "benchmark", "paper_id": None,
                                   "code": type(error).__name__, "message": "Benchmark attempt failed"}]}
                # A crash after an API call must not erase that call's cost or
                # already persisted partial state. Keep only sanitized errors.
                partial_files = list(attempt_dir.glob("*/run.json"))
                if partial_files:
                    try:
                        partial = PipelineResult.model_validate_json(partial_files[-1].read_text(encoding="utf-8"))
                        recovered = evaluate_result(partial, elapsed_seconds=elapsed)
                        recovered["errors"].extend(row["errors"])
                        recovered["error_count"] += 1
                        recovered.update(status="failed", processing_success=False, complete_success=False,
                                         demo_smoke_complete=False)
                        row = recovered
                    except (OSError, ValueError):
                        pass
                usage = getattr(getattr(pipeline, "llm", None), "usage", None)
                if usage and hasattr(usage, "to_dict"):
                    row["llm_request_count"] = max(row["llm_request_count"], int(usage.to_dict().get("requests", 0)))
                    row["request_count"] = row["http_request_count"] + row["llm_request_count"]
                row["processed_input_sha256"] = None
            row.update(repeat=repeat + 1, order_in_pair=position + 1,
                       inputs_match=inputs_match, input_sha256=manifest["input_sha256"],
                       configuration_sha256=metadata["configuration_sha256"], cache_policy=cache_policy,
                       measurement_scope=metadata["measurement_scope"])
            if not inputs_match:
                row["processing_success"] = row["complete_success"] = False
            rows.append(row)
            _write_rows(output, rows, metadata)
    return _write_rows(output, rows, metadata)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-json", type=Path, required=True, help="PipelineResult JSON")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, help="New directory; default data/benchmarks/<UTC timestamp>")
    parser.add_argument("--cache-policy", choices=["cold", "warm"], default="warm")
    args = parser.parse_args()
    run = PipelineResult.model_validate_json(args.run_json.read_text(encoding="utf-8"))
    try:
        report = asyncio.run(benchmark(run, Settings.from_env(), args.repeats, args.output, cache_policy=args.cache_policy))
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"metadata": report["metadata"], "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
