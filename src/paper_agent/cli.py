"""Thin CLI: all research work crosses the real MCP v2 client/server boundary."""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from paper_agent.config import Settings
from paper_agent.evaluation.metrics import evaluate_result
from paper_agent.models import PipelineResult

DEFAULT_IDEA = "如何利用长期记忆与历史经验复用，减少 LLM Agent 在多步骤任务中的 Token 消耗，同时保持任务成功率？"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MCP v2 multi-source paper research; no local GPU required")
    parser.add_argument("idea", nargs="?", default=DEFAULT_IDEA)
    parser.add_argument("--mode", choices=["demo", "live"])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--query", default="", help="Optional concise English search query")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--execution-mode", choices=["sequential", "concurrent"])
    execution.add_argument("--processing", choices=["serial", "concurrent"], help="Compatibility alias for execution mode")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json", action="store_true", help="Print full PipelineResult JSON on stdout")
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        overrides = {name: getattr(args, name) for name in ("mode", "output_dir") if getattr(args, name) is not None}
        if args.execution_mode:
            overrides["processing"] = "serial" if args.execution_mode == "sequential" else "concurrent"
        elif args.processing:
            overrides["processing"] = args.processing
        settings = replace(settings, **overrides)
        settings.validate()
        if not 1 <= args.k <= 100:
            parser.error("k must be between 1 and 100")
        from paper_agent.main_agent import run_research

        started = time.perf_counter()
        raw = asyncio.run(run_research(args.idea, settings, args.k, args.query,
                                      progress=lambda text: print(text, file=sys.stderr)))
        result = PipelineResult.model_validate(raw)
        metrics = evaluate_result(result, elapsed_seconds=time.perf_counter() - started)
    except Exception as error:
        print(f"Run failed ({type(error).__name__}); inspect the structured run errors.", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(f"mode={result.mode} | execution_mode={result.execution_mode} | status={result.status} | run_id={result.run_id}")
        if result.mode == "demo":
            print("DEMO ONLY: synthetic documents, injected model responses and scripted main-agent tools; real API quality and performance are not validated.")
        print("Sources: " + json.dumps(result.source_counts, ensure_ascii=False))
        print(f"Merged candidates: {len(result.papers)}; original Top-{result.top_k}:")
        for paper in result.selected_papers:
            print(f"{paper.rank or '?'}. {paper.paper_id} | {paper.title} | {paper.selection_reason or ''}")
        by_id = {paper.paper_id: paper for paper in result.papers}
        for paper_id in result.metrics.get("analysis_paper_ids", []):
            paper = by_id[paper_id]
            print(f"Artifact {paper_id}: PDF={paper.pdf_path}; Markdown={paper.markdown_path}")
        print(f"Report: {result.report.report_path if result.report else 'not generated'}")
        print(f"Fulltexts={metrics['fulltext_count']} | fulltext summaries={metrics['fulltext_summary_count']} | complete success={metrics['complete_success']} | demo smoke={metrics['demo_smoke_complete']}")
        print(f"Client wall time: {metrics['elapsed_seconds']:.3f}s | requests={metrics['request_count']} | errors={metrics['error_count']}")
        print("Run output: " + (result.output_dir or "not persisted"))
    if result.status in {"partial", "failed"}:
        raise SystemExit(2 if result.status == "partial" else 1)


if __name__ == "__main__":
    main()
