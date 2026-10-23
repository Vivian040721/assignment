"""Owner 10: thin demo entry point; all workflow logic lives in the service."""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from paper_agent.config import Settings
from paper_agent.main_agent import run_research


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP paper-agent scaffold; no GPU required")
    parser.add_argument("idea", nargs="?", default="研究 Agent memory 与可靠工具调用的方法")
    parser.add_argument("--mode", choices=["demo", "live"])
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--query", default="", help="Optional concise English paper search query")
    parser.add_argument("--processing", choices=["serial", "concurrent"])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--json", action="store_true", help="Print full result JSON on stdout")
    args = parser.parse_args()
    settings = Settings.from_env()
    overrides = {
        name: getattr(args, name)
        for name in ["mode", "processing", "output_dir"]
        if getattr(args, name) is not None
    }
    settings = replace(settings, **overrides)
    started = time.perf_counter()
    try:
        result = asyncio.run(
            run_research(
                args.idea,
                settings,
                args.k,
                args.query,
                progress=lambda text: print(text, file=sys.stderr),
            )
        )
    except Exception as error:
        print(f"Run failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"mode={result['mode']} | status={result['status']} | run_id={result['run_id']}")
        for paper in result["selected_papers"]:
            print(f"{paper['rank']}. {paper['paper_id']} | {paper['selection_reason']}")
        print(f"Report: {result['report_path'] or 'not generated'}")
        print(f"Client wall time: {time.perf_counter() - started:.3f}s")
        print("Errors: " + json.dumps(result["errors"], ensure_ascii=False))
    if result["status"] == "partial":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
