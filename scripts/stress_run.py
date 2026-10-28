"""Run a reproducible small or large research profile through the real MCP path.

Demo is offline and safe. Live calls real paper sources and the configured model;
it requires an explicit --yes flag and never prints credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paper_agent.config import Settings  # noqa: E402
from paper_agent.main_agent import run_research  # noqa: E402
from paper_agent.services.pipeline import ResearchPipeline  # noqa: E402


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a labeled Paper Agent research profile")
    parser.add_argument("--idea", default="如何利用长期记忆减少 Agent 的 Token 消耗")
    parser.add_argument("--query", default="long-term memory agent token efficiency")
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--mode", choices=["demo", "live"], default="demo")
    parser.add_argument("--source-limit", type=int, default=40)
    parser.add_argument("--candidate-limit", type=int, default=30)
    parser.add_argument("--merged-limit", type=int, default=30)
    parser.add_argument("--selector-budget", type=int, default=48000)
    parser.add_argument("--report-budget", type=int, default=64000)
    parser.add_argument("--max-fulltext-attempts", type=int, default=30)
    parser.add_argument("--max-llm-calls", type=int, default=160)
    parser.add_argument("--max-total-tokens", type=int, default=1_200_000)
    parser.add_argument("--max-chunks", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1800, help="Overall wall-clock guard in seconds")
    parser.add_argument("--download-timeout", type=float, default=180, help="Per-paper PDF acquisition budget")
    parser.add_argument("--download-candidate-timeout", type=float, default=45, help="Per-candidate PDF budget")
    parser.add_argument("--http-timeout", type=float, default=30, help="Per-request HTTP timeout")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--direct", action="store_true", help="Call the pipeline directly instead of the MCP client path")
    parser.add_argument("--live", action="store_true", help="Alias for --mode live")
    parser.add_argument("--yes", action="store_true", help="Confirm potentially billable live requests")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def build_settings(args: argparse.Namespace, output: Path) -> Settings:
    base = Settings.from_env()
    mode = "live" if args.live else args.mode
    if mode == "live" and not args.yes:
        raise SystemExit("Live mode makes real source/model requests; pass --yes after confirming API usage.")
    data = output / "data"
    settings = replace(
        base,
        mode=mode,
        output_dir=output / "runs",
        data_dir=data,
        pdf_dir=data / "pdfs",
        markdown_dir=data / "markdown",
        summaries_dir=data / "summaries",
        reports_dir=data / "reports",
        processing="concurrent",
        concurrency=args.concurrency,
        search_concurrency=min(4, args.concurrency),
        download_concurrency=min(4, args.concurrency),
        conversion_concurrency=min(2, args.concurrency),
        llm_concurrency=min(4, args.concurrency),
        source_limit=args.source_limit,
        candidate_limit=args.candidate_limit,
        merged_candidate_limit=args.merged_limit,
        selector_input_token_budget=args.selector_budget,
        report_input_token_budget=args.report_budget,
        max_fulltext_attempts=args.max_fulltext_attempts,
        llm_max_calls=args.max_llm_calls,
        llm_max_total_tokens=args.max_total_tokens,
        max_chunks=args.max_chunks,
        top_k=args.k,
        download_timeout=args.download_timeout,
        download_candidate_timeout=args.download_candidate_timeout,
        http_timeout=args.http_timeout,
    )
    settings.validate()
    if mode == "live":
        settings.require_llm()
    return settings


def settings_summary(settings: Settings) -> dict:
    return {
        "mode": settings.mode,
        "execution_mode": settings.execution_mode,
        "top_k": settings.top_k,
        "source_limit_per_query": settings.source_limit,
        "candidate_limit": settings.candidate_limit,
        "merged_candidate_limit": settings.merged_candidate_limit,
        "selector_input_budget_bytes": settings.selector_input_token_budget,
        "report_input_budget_bytes": settings.report_input_token_budget,
        "max_fulltext_attempts": settings.max_fulltext_attempts,
        "max_chunks": settings.max_chunks,
        "max_llm_calls": settings.llm_max_calls,
        "max_total_tokens": settings.llm_max_total_tokens,
        "download_timeout": settings.download_timeout,
        "download_candidate_timeout": settings.download_candidate_timeout,
        "http_timeout": settings.http_timeout,
        "concurrency": {
            "search": settings.search_concurrency,
            "download": settings.download_concurrency,
            "conversion": settings.conversion_concurrency,
            "llm": settings.llm_concurrency,
        },
        "model_configured": bool(settings.llm_api_key),
    }


async def execute(args: argparse.Namespace, settings: Settings) -> dict:
    started = time.perf_counter()
    if args.direct:
        result = await ResearchPipeline(settings).run_research_pipeline(args.idea, args.k, args.query)
        value = result.model_dump(mode="json")
    else:
        value = await run_research(args.idea, settings, args.k, args.query,
                                   progress=lambda message: print(message, flush=True))
    value["stress_elapsed_seconds"] = time.perf_counter() - started
    return value


def main() -> int:
    args = arguments()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "data" / "validation" / "v021" / "stress" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = build_settings(args, output)
    summary = settings_summary(settings)
    estimated_calls = 3 + args.k * (args.max_chunks + 1)
    summary.update(profile="large", estimated_llm_calls_upper_bound=estimated_calls,
                   output=str(output), query=args.query, idea=args.idea,
                   started_at=datetime.now(timezone.utc).isoformat())
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        (output / "settings.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    if settings.mode == "live" and estimated_calls > settings.llm_max_calls:
        raise SystemExit(
            f"Estimated {estimated_calls} model calls exceed LLM_MAX_CALLS={settings.llm_max_calls}; "
            "raise the budget explicitly or reduce --k/--max-chunks."
        )
    try:
        result = asyncio.run(asyncio.wait_for(execute(args, settings), timeout=args.timeout))
    except Exception as error:
        failure = {"profile": summary, "status": "failed", "error_type": type(error).__name__,
                   "error": str(error)[:1000], "finished_at": datetime.now(timezone.utc).isoformat()}
        (output / "stress-summary.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1
    report = {
        "profile": summary,
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "delivery_status": result.get("delivery_status"),
        "brief": result.get("brief"),
        "source_counts": result.get("source_counts"),
        "selected_count": len(result.get("selected_papers", [])),
        "analysis_count": len(result.get("metrics", {}).get("analysis_paper_ids", [])),
        "summary_count": len(result.get("summaries", [])),
        "timings": result.get("timings", []),
        "metrics": result.get("metrics", {}),
        "errors": result.get("errors", []),
        "tool_calls": [{"tool": call.get("tool"), "status": call.get("status"),
                        "duration_seconds": call.get("duration_seconds"),
                        "controller": call.get("controller")}
                       for call in result.get("tool_calls", [])],
        "output_dir": result.get("output_dir"),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "stress-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "run_id", "status", "delivery_status", "source_counts", "selected_count", "analysis_count",
        "summary_count", "errors", "output_dir")}, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"completed", "partial", "demo_completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
