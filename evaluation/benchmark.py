"""Owner 09: paired serial/concurrent summary-stage measurement on fixed Markdown.

Usage: python -m evaluation.benchmark --run-json outputs/<run_id>/run.json
Demo runs are wiring smoke checks only, never performance evidence.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import statistics
import time
from dataclasses import replace
from pathlib import Path

from paper_agent.agents.reporter import ReporterAgent
from paper_agent.config import Settings
from paper_agent.pipeline.parallel import process_items
from paper_agent.schemas import RunResult


async def benchmark(run: RunResult, settings: Settings, repeats: int, output: Path) -> list[dict]:
    papers = {paper.paper_id: paper for paper in run.candidates}
    inputs = [
        (papers[a.paper_id], Path(a.markdown_path).read_text(encoding="utf-8"))
        for a in run.artifacts
        if a.markdown_path and a.fulltext_status != "failed"
    ]
    if not inputs:
        raise ValueError("No converted fulltexts in supplied run")
    if run.mode == "live":
        settings.require_llm()
    reporter = ReporterAgent(replace(settings, mode=run.mode))
    fingerprint = hashlib.sha256(
        json.dumps([(p.paper_id, text) for p, text in inputs], ensure_ascii=False).encode()
    ).hexdigest()
    rows = []
    output.mkdir(parents=True, exist_ok=True)
    for repeat in range(repeats):
        order = ["serial", "concurrent"] if repeat % 2 == 0 else ["concurrent", "serial"]
        for mode in order:

            async def operation(item):
                paper, text = item
                return await reporter.summarize(paper, text, run.input_idea)

            started = time.perf_counter()
            results = await process_items(inputs, operation, mode, settings.concurrency)
            seconds = time.perf_counter() - started
            errors = [
                f"{type(item).__name__}: {item}" for item in results if isinstance(item, Exception)
            ]
            for index, result in enumerate(results):
                if not isinstance(result, Exception):
                    (output / f"{repeat}_{mode}_{index}.md").write_text(result[0], encoding="utf-8")
            rows.append(
                {
                    "run_id": run.run_id,
                    "data_mode": run.mode,
                    "repeat": repeat + 1,
                    "processing": mode,
                    "seconds": round(seconds, 6),
                    "papers": len(inputs),
                    "success": not errors,
                    "input_sha256": fingerprint,
                    "model": settings.llm_model or "none-demo",
                    "concurrency": 1 if mode == "serial" else settings.concurrency,
                    "errors": json.dumps(errors, ensure_ascii=False),
                }
            )
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metadata = {
        "stage": "single_paper_summarization_only",
        "run_id": run.run_id,
        "data_mode": run.mode,
        "input_sha256": fingerprint,
        "cache": "no summary cache",
        "raw_rows": len(rows),
        "notice": "Demo data is smoke only; live timing still requires evidence/quality audit.",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-json", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("outputs/benchmark"))
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("repeats must be between 1 and 10")
    run = RunResult.model_validate_json(args.run_json.read_text(encoding="utf-8"))
    rows = asyncio.run(benchmark(run, Settings.from_env(), args.repeats, args.output))
    print(f"Saved {len(rows)} rows: {args.output / 'results.csv'}")
    if run.mode == "demo":
        print("DEMO SMOKE ONLY: template timings are not optimization evidence.")
    else:
        for mode in ["serial", "concurrent"]:
            successes = [
                row["seconds"] for row in rows if row["processing"] == mode and row["success"]
            ]
            print(
                mode,
                "successful median seconds:",
                statistics.median(successes) if successes else None,
            )


if __name__ == "__main__":
    main()
