"""Owner 02: MCP-side pipeline; task agents are instantiated and invoked here."""

import asyncio
import time
from pathlib import Path

import httpx

from paper_agent.agents.reporter import ReporterAgent
from paper_agent.agents.selector import SelectorAgent
from paper_agent.config import Settings
from paper_agent.pipeline.fulltext import FulltextProcessor, paper_slug
from paper_agent.pipeline.parallel import process_items
from paper_agent.pipeline.ranking import deduplicate, rank
from paper_agent.schemas import PaperArtifact, RunResult
from paper_agent.sources.arxiv import ArxivSource
from paper_agent.sources.demo import DemoSource
from paper_agent.sources.openalex import OpenAlexSource
from paper_agent.storage import RunStore, new_run_id


class ResearchService:
    def __init__(self, settings: Settings):
        settings.validate()
        self.settings = settings
        self.store = RunStore(settings.output_dir)
        self.selector = SelectorAgent(settings)
        self.reporter = ReporterAgent(settings)
        self.fulltext = FulltextProcessor(settings)
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._search_lock = asyncio.Lock()
        self._last_arxiv_search = 0.0

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._run_locks.setdefault(run_id, asyncio.Lock())

    def _load(self, run_id: str) -> RunResult:
        run = self.store.load(run_id)
        if run.mode != self.settings.mode:
            raise ValueError("Run mode does not match current service mode")
        return run

    async def search_and_select(self, idea: str, k: int = 3, query: str = "") -> dict:
        idea, query = idea.strip(), query.strip() or idea.strip()
        if not 3 <= len(idea) <= 4000 or not 1 <= k <= 10 or len(query) > 1000:
            raise ValueError("Require idea length 3..4000, query <=1000, k 1..10")
        started = time.perf_counter()
        run = RunResult(
            run_id=new_run_id(),
            mode=self.settings.mode,
            input_idea=idea,
            query=query,
            top_k=k,
            processing=self.settings.processing,
            status="selected",
        )
        self.store.save(run)
        self.store.event(run.run_id, "search_started", mode=run.mode, query=query)
        records = []
        async with self._search_lock:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout, proxy=self.settings.http_proxy or None
            ) as client:
                sources = (
                    [DemoSource("arxiv"), DemoSource("openalex")]
                    if run.mode == "demo"
                    else [
                        ArxivSource(client),
                        OpenAlexSource(client, self.settings.openalex_api_key),
                    ]
                )
                for source in sources:
                    try:
                        if run.mode == "live" and source.name == "arxiv":
                            await asyncio.sleep(
                                max(0, 3.1 - (time.monotonic() - self._last_arxiv_search))
                            )
                            self._last_arxiv_search = time.monotonic()
                        papers = await source.search(query, self.settings.source_limit)
                        records.extend(papers)
                        run.source_counts[source.name] = len(papers)
                        self.store.write_json(
                            run.run_id,
                            f"source_{source.name}.json",
                            [p.model_dump() for p in papers],
                        )
                        self.store.event(
                            run.run_id,
                            "source_finished",
                            source=source.name,
                            count=len(papers),
                            synthetic=run.mode == "demo",
                        )
                    except Exception as error:
                        run.source_counts[source.name] = 0
                        run.errors.append(f"{source.name}: {type(error).__name__}: {error}")
        run.merged_count = len(records)
        unique = deduplicate(records)
        run.dedup_count = len(unique)
        ranked = await asyncio.to_thread(rank, unique, query, self.settings.ranker)
        run.candidates = ranked[: max(k, self.settings.candidate_limit)]
        run.candidate_count = len(run.candidates)
        try:
            run.selected_papers = await self.selector.select(idea, run.candidates, k)
        except Exception as error:
            run.errors.append(f"selector: {type(error).__name__}: {error}")
        if len(run.selected_papers) < k or any(count == 0 for count in run.source_counts.values()):
            run.status = "partial"
        run.artifacts = [PaperArtifact(paper_id=p.paper_id) for p in run.selected_papers]
        run.timings["search_select_seconds"] = round(time.perf_counter() - started, 6)
        self.store.save(run)
        self.store.event(
            run.run_id,
            "task_agent_selection",
            mode=run.mode,
            selected=[p.model_dump() for p in run.selected_papers],
            errors=run.errors,
        )
        return run.model_dump()

    async def prepare_fulltexts(self, run_id: str) -> dict:
        async with self._lock(run_id):
            run = self._load(run_id)
            if run.report_path:
                raise ValueError("Run already has a report; start a new run to reprocess")
            started = time.perf_counter()
            lookup = {p.paper_id: p for p in run.candidates}
            papers = [lookup[p.paper_id] for p in run.selected_papers]
            directory = self.store.directory(run_id)

            async def prepare(paper):
                return await self.fulltext.prepare(paper, directory)

            results = await process_items(
                papers, prepare, run.processing, self.settings.concurrency
            )
            run.artifacts = []
            for paper, result in zip(papers, results, strict=True):
                if isinstance(result, Exception):
                    reason = f"{type(result).__name__}: {result}"
                    run.artifacts.append(
                        PaperArtifact(
                            paper_id=paper.paper_id, fulltext_status="failed", failure_reason=reason
                        )
                    )
                    run.errors.append(f"fulltext {paper.paper_id}: {reason}")
                else:
                    run.artifacts.append(result)
            ready = sum(a.fulltext_status in {"ready", "demo_fixture"} for a in run.artifacts)
            run.status = "fulltexts_ready" if ready == run.top_k and not run.errors else "partial"
            run.timings["fulltext_seconds"] = round(time.perf_counter() - started, 6)
            self.store.save(run)
            self.store.event(
                run_id,
                "fulltexts_finished",
                ready=ready,
                artifacts=[a.model_dump() for a in run.artifacts],
            )
            return run.model_dump()

    async def generate_report(self, run_id: str) -> dict:
        async with self._lock(run_id):
            run = self._load(run_id)
            if run.report_path:
                return run.model_dump()
            if not run.artifacts or all(a.fulltext_status == "pending" for a in run.artifacts):
                raise ValueError("Call prepare_fulltexts before generate_report")
            started = time.perf_counter()
            lookup = {p.paper_id: p for p in run.candidates}
            ready = [a for a in run.artifacts if a.markdown_path and a.fulltext_status != "failed"]
            directory = self.store.directory(run_id)

            async def summarize(artifact):
                markdown = Path(artifact.markdown_path).read_text(encoding="utf-8")
                summary, coverage = await self.reporter.summarize(
                    lookup[artifact.paper_id], markdown, run.input_idea
                )
                path = directory / f"{paper_slug(artifact.paper_id)}.summary.md"
                path.write_text(summary, encoding="utf-8")
                return str(path), summary, coverage

            results = await process_items(
                ready, summarize, run.processing, self.settings.concurrency
            )
            summaries = {}
            for artifact, result in zip(ready, results, strict=True):
                if isinstance(result, Exception):
                    artifact.failure_reason = f"summary: {type(result).__name__}: {result}"
                    run.errors.append(f"{artifact.paper_id}: {artifact.failure_reason}")
                else:
                    artifact.summary_path, text, artifact.evidence_coverage = result
                    summaries[artifact.paper_id] = text
            complete = (
                len(summaries) == run.top_k
                and not run.errors
                and all("partial_fulltext" not in a.evidence_coverage for a in ready)
            )
            if summaries:
                try:
                    report = await self.reporter.synthesize(run.input_idea, summaries, not complete)
                    if not complete:
                        report = (
                            "> PARTIAL / 部分完成：请查看 run.json 中的错误和阅读覆盖。\n\n"
                            + report
                        )
                    report_path = directory / "report.md"
                    report_path.write_text(report, encoding="utf-8")
                    run.report_path = str(report_path)
                except Exception as error:
                    run.errors.append(f"report: {type(error).__name__}: {error}")
                    complete = False
            run.status = (
                ("demo_completed" if run.mode == "demo" else "completed")
                if complete and run.report_path
                else "partial"
            )
            run.timings["summaries_report_seconds"] = round(time.perf_counter() - started, 6)
            run.timings["pipeline_seconds"] = round(
                sum(value for key, value in run.timings.items() if key != "pipeline_seconds"), 6
            )
            self.store.save(run)
            self.store.event(
                run_id,
                "report_finished",
                status=run.status,
                report_path=run.report_path,
                timings=run.timings,
            )
            return run.model_dump()
