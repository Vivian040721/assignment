"""Persistent business orchestration used by all six MCP v2 tools.

Source, document and model dependencies are injectable; the default live path
always uses the shared module contracts. Offline fixtures are explicitly labeled.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from paper_agent.agents.research_agent import generate_report, summarize_paper
from paper_agent.agents.selector_agent import select_top_k
from paper_agent.config import Settings
from paper_agent.llm_client import ChatClient
from paper_agent.models import ErrorInfo, Paper, PaperSummary, PipelineResult, StageTiming
from paper_agent.services.downloader import download_pdf
from paper_agent.services.pdf_converter import convert_pdf
from paper_agent.services.retrieval import (
    build_bounded_candidate_pool,
    match_explicit_targets,
    merge_dedupe_rank,
    normalize_explicit_targets,
    plan_queries,
    target_in_text,
)
from paper_agent.services.semantic_reranker import semantic_rerank
from paper_agent.sources.arxiv_source import search_arxiv
from paper_agent.sources.openalex_source import search_openalex
from paper_agent.storage import PipelineStore


def _failure(exc: Exception, stage: str, paper_id: str | None = None) -> ErrorInfo:
    for name in ("error_info", "error"):
        error = getattr(exc, name, None)
        if isinstance(error, ErrorInfo):
            return error.model_copy(deep=True)
    # Third-party exception text can contain credentials or untrusted content.
    return ErrorInfo(code=f"{stage}_failed", message=f"阶段 {stage} 执行失败", stage=stage,
                     paper_id=paper_id, details={"exception_type": type(exc).__name__})


def _usage(llm: Any) -> dict[str, int]:
    usage = getattr(llm, "usage", None)
    return usage.to_dict() if usage and hasattr(usage, "to_dict") else {}


def _delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: max(0, value - before.get(key, 0)) for key, value in after.items()}


def _add_errors(run: PipelineResult, errors) -> None:
    """Keep one machine-readable record per failure across resumed stages."""
    seen = {(error.code, error.stage, error.paper_id, error.message) for error in run.errors}
    for error in errors:
        key = (error.code, error.stage, error.paper_id, error.message)
        if key not in seen:
            run.errors.append(error)
            seen.add(key)


class ResearchPipeline:
    """One server-owned LLM shares task-Agent concurrency and request budgets.

    ``papers`` on ``run_research_pipeline`` is an explicit frozen metadata input,
    useful for paired experiments. Injected functions retain the public module
    signatures; injected fixtures do not constitute live API validation.
    """

    def __init__(self, settings: Settings, *, llm=None, search_arxiv_fn=None,
                 search_openalex_fn=None, query_planner_fn=None, selector_fn=None,
                 download_fn=None, convert_fn=None, summarize_fn=None, report_fn=None,
                 store=None):
        settings.validate()
        if settings.mode == "live" and getattr(llm, "synthetic", False):
            raise ValueError("合成测试模型仅允许在 demo 模式使用。")
        self.settings = settings
        self.store = store or PipelineStore(settings.output_dir)
        self.llm = llm
        self._injected_llm = llm is not None
        self.searchers = {"arxiv": search_arxiv_fn or search_arxiv,
                          "openalex": search_openalex_fn or search_openalex}
        self.query_planner = query_planner_fn or plan_queries
        self.selector = selector_fn or select_top_k
        self.download = download_fn or download_pdf
        self.convert = convert_fn or convert_pdf
        self.summarize = summarize_fn or summarize_paper
        self.report = report_fn or generate_report
        self._injected_selection = selector_fn is not None
        self._locks: dict[str, asyncio.Lock] = {}
        # Serialize separate tool operations while allowing bounded concurrency
        # inside a stage. Shared LLM usage deltas then belong to exactly one run.
        self._operation_gate = asyncio.Lock()
        self._source_gate = asyncio.Semaphore(settings.search_concurrency)
        self._download_gate = asyncio.Semaphore(settings.download_concurrency)
        self._convert_gate = asyncio.Semaphore(settings.conversion_concurrency)
        self._llm_gate = asyncio.Semaphore(settings.llm_concurrency)
        if settings.mode == "demo":
            from paper_agent.demo_fixtures import DemoLLM, demo_papers, prepare_demo_paper

            self.llm = llm or DemoLLM()
            for source, injected in (("arxiv", search_arxiv_fn), ("openalex", search_openalex_fn)):
                if injected is None:
                    async def source_fixture(query, limit, *, settings=None, client=None, _source=source):
                        return demo_papers(_source, limit)
                    self.searchers[source] = source_fixture
            if download_fn is None:
                self.download = prepare_demo_paper
            if convert_fn is None:
                async def already_converted(paper, *, settings=None):
                    return paper
                self.convert = already_converted

    def _model(self):
        if self.llm is None:
            self.llm = ChatClient(self.settings)
        return self.llm

    @asynccontextmanager
    async def _lock(self, run_id: str):
        # directory() validates the ID before any lookup or path access.
        self.store.directory(run_id)
        async with self._operation_gate:
            async with self._locks.setdefault(run_id, asyncio.Lock()):
                yield

    def _settings_for(self, run: PipelineResult) -> Settings:
        directory = self.store.directory(run.run_id)
        # Independent summary/report output prevents experiments overwriting each other.
        return replace(self.settings, summaries_dir=directory / "summaries",
                       reports_dir=directory / "reports")

    def _load(self, run_id: str) -> PipelineResult:
        run = self.store.load(run_id)
        if run.mode != self.settings.mode or run.execution_mode != self.settings.execution_mode:
            raise ValueError("继续运行需要保持原始模式和执行方式。")
        return run

    @staticmethod
    def _validate(idea: str, k: int, query: str) -> None:
        if not isinstance(idea, str) or not 3 <= len(idea.strip()) <= 12000:
            raise ValueError("研究问题需要包含 3 至 12000 个字符。")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
            raise ValueError("Top-k 必须在 1 至 100 之间。")
        if not isinstance(query, str) or len(query) > 1000:
            raise ValueError("检索词不能超过 1000 个字符。")

    def _new(self, idea: str, k: int, query: str) -> PipelineResult:
        self._validate(idea, k, query)
        if self.settings.candidate_limit < k:
            raise ValueError("候选上限必须覆盖 Top-k。")
        run = PipelineResult(idea=idea.strip(), query=query.strip(), top_k=k,
                             mode=self.settings.mode, execution_mode=self.settings.execution_mode)
        run.output_dir = str(self.store.directory(run.run_id))
        run.metrics = {"completed_stages": [], "source_success": {}, "source_requests": {},
                       "http_requests": {"source_search": 0, "download": 0, "total": 0},
                       "cache_hits": {"download": 0, "conversion": 0}, "llm_usage": {},
                       "replacements": [], "replacement_attempts": [],
                       "analysis_paper_ids": [], "attempted_paper_ids": [],
                       "target_coverage": {}, "candidate_pool": {},
                       "frozen_input": False, "injected_selection": self._injected_selection,
                       "model_provenance": ("synthetic_fixture" if self.settings.mode == "demo" else
                                            "injected" if self._injected_llm else "configured_chat_client"),
                       "synthetic": self.settings.mode == "demo"}
        self.store.save(run)
        return run

    def _finish(self, run, stage, start, before, *, successes=0, requests=0):
        usage = _delta(before, _usage(self.llm))
        for key, value in usage.items():
            run.metrics["llm_usage"][key] = run.metrics["llm_usage"].get(key, 0) + value
        run.metrics["physical_llm_requests"] = run.metrics["llm_usage"].get("requests", 0)
        timing = StageTiming(stage=stage, duration_seconds=time.perf_counter() - start,
                             request_count=requests + usage.get("requests", 0),
                             success_count=successes,
                             error_count=max(0, len(run.errors) - sum(t.error_count for t in run.timings if t.stage != "total")))
        run.timings.append(timing)
        run.metrics["completed_stages"].append(stage)
        self.store.event(run.run_id, stage, status=run.status, timing=timing.model_dump(),
                         llm_usage=usage)
        self.store.save(run)
        return run

    def _require(self, run: PipelineResult, stage: str) -> None:
        if stage not in run.metrics.get("completed_stages", []):
            raise ValueError(f"必须先完成 {stage} 阶段，才能调用这个工具")

    def _refresh_target_coverage(self, run: PipelineResult, stage: str) -> None:
        """Record actual target traceability at each real pipeline stage."""
        coverage = run.metrics.get("target_coverage")
        if not isinstance(coverage, dict) or not coverage.get("targets"):
            return
        papers = {paper.paper_id: paper for paper in run.papers}
        selected_ids = [paper.paper_id for paper in run.selected_papers]
        selected_set = set(selected_ids)
        analysis_ids = [
            value for value in run.metrics.get("analysis_paper_ids", []) if isinstance(value, str)
        ]
        analysis_set = set(analysis_ids)
        summaries = {summary.paper_id: summary for summary in run.summaries}
        report_ids = (
            [reference.paper_id for reference in run.report.references]
            if run.report is not None else []
        )
        report_set = set(report_ids)
        for item in coverage["targets"]:
            direct = [
                paper_id for paper_id in item.get("direct_title_paper_ids", [])
                if isinstance(paper_id, str)
            ]
            complete, partial, summarized = [], [], []
            fulltext_summaries, partial_summaries = [], []
            for paper_id in direct:
                paper = papers.get(paper_id)
                if paper is not None and paper_id in analysis_set:
                    if self._complete(paper):
                        complete.append(paper_id)
                    elif self._readable(paper):
                        partial.append(paper_id)
                summary = summaries.get(paper_id)
                if summary is not None:
                    summarized.append(paper_id)
                    if summary.summary_scope == "fulltext" and not summary.errors:
                        fulltext_summaries.append(paper_id)
                    else:
                        partial_summaries.append(paper_id)
            item.update(
                selected_paper_ids=[paper_id for paper_id in direct if paper_id in selected_set],
                analysis_paper_ids=[paper_id for paper_id in direct if paper_id in analysis_set],
                complete_fulltext_paper_ids=complete,
                partial_fulltext_paper_ids=partial,
                summarized_paper_ids=summarized,
                fulltext_summary_paper_ids=fulltext_summaries,
                partial_summary_paper_ids=partial_summaries,
                report_paper_ids=[paper_id for paper_id in direct if paper_id in report_set],
            )
        coverage.update(
            selected_paper_ids=selected_ids,
            analysis_paper_ids=analysis_ids,
            report_reference_ids=report_ids,
            stage=stage,
        )
        if run.metrics.get("report_valid") is not None:
            coverage["report_valid"] = bool(run.metrics["report_valid"])

    @staticmethod
    def _replacement_order(
        remaining: list[Paper], coverage: dict[str, Any], complete_ids: set[str],
        full_order: dict[str, int],
    ) -> list[Paper]:
        """Order bounded replacement attempts without changing the full pool."""
        direct_all: set[str] = set()
        uncovered_direct: set[str] = set()
        for item in coverage.get("targets", []) if isinstance(coverage, dict) else []:
            direct = {
                paper_id for paper_id in item.get("direct_title_paper_ids", [])
                if isinstance(paper_id, str)
            }
            direct_all |= direct
            if not (direct & complete_ids):
                uncovered_direct |= direct

        def key(paper: Paper) -> tuple[int, int]:
            if paper.paper_id in uncovered_direct:
                bucket = 0
            elif paper.paper_id in direct_all:
                bucket = 1
            elif paper.pdf_url or paper.pdf_urls:
                bucket = 2
            else:
                bucket = 3
            return bucket, full_order.get(paper.paper_id, len(full_order))

        return sorted(remaining, key=key)

    async def _map(self, function: Callable, values: list[Any]):
        if self.settings.processing == "serial":
            return [await function(value) for value in values]
        return await asyncio.gather(*(function(value) for value in values))

    async def search_papers(self, idea: str, k: int = 5, query: str = "") -> PipelineResult:
        async with self._operation_gate:
            return await self._search_papers(idea, k, query)

    async def _search_papers(self, idea: str, k: int = 5, query: str = "") -> PipelineResult:
        run = self._new(idea, k, query)
        start, before = time.perf_counter(), _usage(self.llm)
        run.status = "searching"
        try:
            explicit_query = query.strip()
            if explicit_query:
                # A caller-supplied English query is a contract, not a hint for
                # the planner. Never bill a model call that could rewrite it.
                plan = {"queries": [explicit_query], "ranking_query": explicit_query,
                        "mode": "explicit", "warnings": [], "errors": []}
            else:
                plan = await self.query_planner(run.idea, settings=self.settings,
                                                llm=self._model() if self.settings.mode == "demo" or self.llm is not None or self.settings.llm_model else None)
            if not isinstance(plan, dict):
                raise ValueError("查询规划返回了无效结构")
            plan = dict(plan)
            raw_queries = plan.get("queries", [])
            queries = [
                " ".join(value.split()) for value in raw_queries
                if isinstance(value, str) and value.strip()
            ] if isinstance(raw_queries, list) else []
            plan["warnings"] = [
                warning for warning in plan.get("warnings", []) if isinstance(warning, str)
            ] if isinstance(plan.get("warnings"), list) else []
            if len(queries) > 3:
                queries = queries[:3]
                plan["warnings"].append("规划器返回超过三条检索词，已按来源请求规模保留前三条。")
            existing_target_audit = plan.get("target_audit")
            targets, target_audit = normalize_explicit_targets(run.idea, plan.get("explicit_targets", []))
            if isinstance(existing_target_audit, dict) and isinstance(existing_target_audit.get("rejected"), list):
                # Preserve planner rejections while re-validating accepted names.
                target_audit["rejected"] = [
                    *existing_target_audit["rejected"], *target_audit["rejected"]
                ]
            target_queries: dict[str, str] = {}
            for target in targets:
                query = next((value for value in queries if target_in_text(target, value)), None)
                if query:
                    target_queries[target] = query
            covered_targets = list(target_queries)
            uncovered_targets = [target for target in targets if target not in target_queries]
            if uncovered_targets:
                plan["warnings"].append(
                    f"有 {len(uncovered_targets)} 个点名目标没有实际执行的定向查询；目标记录保留，检索覆盖不足。"
                )
            plan.update(
                queries=queries, explicit_targets=targets, target_queries=target_queries,
                query_covered_targets=covered_targets,
                query_uncovered_targets=uncovered_targets, target_audit=target_audit,
            )
            run.queries = queries
            run.metrics["query_plan"] = plan
            run.metrics["query_degraded"] = bool(plan.get("errors") or plan.get("mode") == "english_fallback")
            _add_errors(run, [
                ErrorInfo.model_validate(error) for error in plan.get("errors", [])
                if isinstance(error, dict)
            ])
            if not run.queries:
                run.errors.append(ErrorInfo(code="translation_required", message="请提供英文检索词，或配置翻译模型。", stage="search"))
                run.status = "failed"
                return self._finish(run, "search", start, before)
            ranking_query = str(plan.get("ranking_query") or run.queries[0])
            plan["ranking_query"] = ranking_query
            run.query = run.queries[0]
        except Exception as exc:
            run.errors.append(_failure(exc, "search"))
            run.status = "failed"
            return self._finish(run, "search", start, before)

        async def source_search(source):
            papers, count = [], 0
            async def on_request(request):
                nonlocal count
                count += 1
            try:
                async with self._source_gate:
                    if self.settings.mode == "demo":
                        for planned_query in run.queries:
                            papers.extend(await self.searchers[source](planned_query, self.settings.source_limit,
                                                                       settings=self.settings, client=None))
                    else:
                        async with httpx.AsyncClient(timeout=self.settings.http_timeout,
                                                 proxy=self.settings.http_proxy or None,
                                                 follow_redirects=False,
                                                 event_hooks={"request": [on_request]}) as client:
                            for planned_query in run.queries:
                                papers.extend(await self.searchers[source](planned_query, self.settings.source_limit,
                                                                           settings=self.settings, client=client))
                run.metrics["source_success"][source] = True
                run.source_counts[source] = len(papers)
                return papers
            except Exception as exc:
                run.metrics["source_success"][source] = False
                run.source_counts[source] = len(papers)
                run.errors.append(_failure(exc, "search"))
                return papers
            finally:
                run.metrics["source_requests"][source] = count

        batches = await self._map(source_search, list(self.searchers))
        ranked = merge_dedupe_rank([paper for batch in batches for paper in batch], ranking_query)
        run.metrics["retrieved_count"] = sum(run.source_counts.values())
        run.metrics["unique_count"] = len(ranked)
        run.metrics["merged_count"] = len(ranked)
        # Semantic fusion must see every deduplicated candidate. Truncation is
        # deliberately applied only after the complete RRF order is known.
        if self.settings.ranker == "semantic":
            self.store.event(run.run_id, "semantic_ranker", event="start",
                             requested_ranker="semantic", candidate_count=len(ranked))
            ranked, ranking_audit = await semantic_rerank(ranking_query, ranked)
            self.store.event(
                run.run_id, "semantic_ranker",
                event="finish" if ranking_audit.get("actual_ranker") == "semantic" else "fallback",
                requested_ranker="semantic",
                actual_ranker=ranking_audit.get("actual_ranker"),
                model_name=ranking_audit.get("model_name"),
                fallback_reason=ranking_audit.get("fallback_reason"),
                message=ranking_audit.get("fallback_message") or "语义排序完成。",
                candidate_count=len(ranked),
                duration_seconds=ranking_audit.get("duration_seconds"),
            )
        else:
            ranking_audit = {
                "requested_ranker": "keyword", "actual_ranker": "keyword",
                "model_name": None, "fallback_reason": None, "fallback_message": None,
                "candidate_count": len(ranked), "encoded_count": 0, "cache_hits": 0,
                "duration_seconds": 0.0,
            }
        # The complete ordered pool stays on the run for bounded replacements.
        # Only a separate copy is bounded for the Selector request.
        full_ranked = ranked
        targets = plan.get("explicit_targets", [])
        coverage = match_explicit_targets(targets, full_ranked) if targets else {}
        if coverage:
            query_map = plan.get("target_queries", {})
            for item in coverage.get("targets", []):
                item["query"] = query_map.get(item["target"])
                item["query_covered"] = bool(item.get("query"))
            coverage["query_covered_targets"] = list(plan.get("query_covered_targets", []))
            coverage["query_uncovered_targets"] = list(plan.get("query_uncovered_targets", []))
            for paper in full_ranked:
                matches = coverage.get("paper_matches", {}).get(paper.paper_id, {})
                direct = list(matches.get("direct_targets", []))
                abstract = list(matches.get("abstract_targets", []))
                if direct or abstract:
                    paper.metadata["target_matches"] = {
                        "direct_title": direct, "abstract_only": abstract,
                    }
        selector_pool, pool_audit = build_bounded_candidate_pool(
            full_ranked, k=run.top_k, merged_limit=self.settings.merged_candidate_limit,
            candidate_limit=self.settings.candidate_limit, coverage=coverage,
        )
        selector_ids = {paper.paper_id for paper in selector_pool}
        for item in coverage.get("targets", []):
            item["selector_pool_direct_paper_ids"] = [
                paper_id for paper_id in item.get("direct_title_paper_ids", [])
                if paper_id in selector_ids
            ]
        coverage["selector_pool_ids"] = [paper.paper_id for paper in selector_pool]
        ranking_audit["candidate_count_before_truncation"] = len(full_ranked)
        ranking_audit["candidate_count_after_truncation"] = pool_audit["merged_candidate_count"]
        ranking_audit["selector_input_count"] = len(selector_pool)
        ranking_audit["full_candidate_count"] = len(full_ranked)
        run.metrics["ranking"] = ranking_audit
        run.papers = full_ranked
        run.metrics.update(
            candidate_pool=pool_audit, target_coverage=coverage,
            downstream_candidate_count=len(selector_pool),
            full_candidate_count=len(full_ranked),
            merged_candidate_count=pool_audit["merged_candidate_count"],
        )
        self._refresh_target_coverage(run, "search")
        count = sum(run.metrics["source_requests"].values())
        run.metrics["http_requests"].update(source_search=count, total=count)
        run.status = "searched" if run.papers else "failed"
        return self._finish(run, "search", start, before, successes=len(run.papers), requests=count)

    async def select_top_k_papers(self, run_id: str) -> PipelineResult:
        async with self._lock(run_id):
            run = self._load(run_id)
            self._require(run, "search")
            if "selection" in run.metrics["completed_stages"]:
                return run
            start, before = time.perf_counter(), _usage(self.llm)
            coverage = run.metrics.get("target_coverage")
            coverage = coverage if isinstance(coverage, dict) else {}
            targets = [
                value for value in coverage.get("explicit_targets", []) if isinstance(value, str)
            ]
            by_id = {paper.paper_id: paper for paper in run.papers}
            required = min(run.top_k, len(run.papers))
            if self.settings.candidate_limit < required:
                raise ValueError("候选上限不足以覆盖请求的 Top-k")
            pool_ids = (run.metrics.get("candidate_pool") or {}).get("selector_pool_ids")
            selector_pool: list[Paper] = []
            if isinstance(pool_ids, list):
                seen_pool: set[str] = set()
                for paper_id in pool_ids:
                    if isinstance(paper_id, str) and paper_id in by_id and paper_id not in seen_pool:
                        seen_pool.add(paper_id)
                        selector_pool.append(by_id[paper_id])
            if len(selector_pool) < required:
                selector_pool, pool_audit = build_bounded_candidate_pool(
                    run.papers, k=run.top_k, merged_limit=self.settings.merged_candidate_limit,
                    candidate_limit=self.settings.candidate_limit, coverage=coverage,
                )
                run.metrics["candidate_pool"] = pool_audit
                run.metrics["downstream_candidate_count"] = len(selector_pool)
            selector_pool = selector_pool[:self.settings.candidate_limit]
            if len(selector_pool) < required:
                raise ValueError("Selector 输入池不足以覆盖请求的 Top-k")
            self.store.event(run_id, "selector", event="start", agent="llm_selector",
                             synthetic=run.mode == "demo", candidate_count=len(selector_pool),
                             full_candidate_count=len(run.papers), explicit_targets=targets)
            try:
                async with self._llm_gate:
                    if self._injected_selection:
                        selected = await self.selector(
                            run.idea, selector_pool, run.top_k,
                            settings=self.settings, llm=self._model(),
                        )
                    else:
                        selected = await self.selector(
                            run.idea, selector_pool, run.top_k,
                            settings=self.settings, llm=self._model(),
                            targets=targets, target_matches=coverage,
                        )
                run.selected_papers = selected
                run.metrics["selector_degraded"] = any(p.metadata.get("selection", {}).get("degraded") for p in run.selected_papers)
                run.metrics["selection"] = {
                    "selector_input_ids": [paper.paper_id for paper in selector_pool],
                    "llm_selected_ids": [paper.paper_id for paper in run.selected_papers],
                    "llm_ranking": [
                        {"paper_id": paper.paper_id, "rank": paper.rank}
                        for paper in run.selected_papers
                    ],
                    "target_protections": (run.metrics.get("candidate_pool") or {}).get("target_protections", []),
                }
                run.status = "selected" if run.selected_papers else "failed"
                _add_errors(run, [error for p in run.selected_papers for error in p.errors])
                self._refresh_target_coverage(run, "selection")
            except Exception as exc:
                run.errors.append(_failure(exc, "selection"))
                run.status = "failed"
            self.store.event(run_id, "selector", event="finish", agent="llm_selector",
                             selected_ids=[p.paper_id for p in run.selected_papers], status=run.status)
            return self._finish(run, "selection", start, before, successes=len(run.selected_papers))

    @staticmethod
    def _readable(paper: Paper) -> bool:
        return bool(paper.markdown_path and Path(paper.markdown_path).is_file()
                    and Path(paper.markdown_path).stat().st_size > 0 and paper.page_map)

    @classmethod
    def _complete(cls, paper: Paper) -> bool:
        return bool(cls._readable(paper) and paper.pdf_path and Path(paper.pdf_path).is_file()
                    and paper.status == "converted" and paper.fulltext_quality.get("complete") is True)

    async def prepare_fulltexts(self, run_id: str) -> PipelineResult:
        async with self._lock(run_id):
            run = self._load(run_id)
            self._require(run, "selection")
            if not run.selected_papers:
                raise ValueError("没有可用的入选论文，请查看筛选错误。")
            if "fulltexts" in run.metrics["completed_stages"]:
                return run
            start, before = time.perf_counter(), _usage(self.llm)
            attempted: dict[str, Paper] = {}
            attempt_order: list[str] = []
            coverage = run.metrics.get("target_coverage")
            coverage = coverage if isinstance(coverage, dict) else {}
            full_order = {paper.paper_id: index for index, paper in enumerate(run.papers)}

            async def prepare(paper):
                result = paper.model_copy(deep=True)
                try:
                    async with self._download_gate:
                        result = await self.download(result, settings=self.settings)
                    if result.pdf_path:
                        async with self._convert_gate:
                            result = await self.convert(result, settings=self.settings)
                except Exception as exc:
                    result.status, result.error = "failed", "全文处理失败"
                    result.errors.append(_failure(exc, "fulltexts", paper.paper_id))
                self.store.event(run_id, "fulltext_paper", paper_id=paper.paper_id,
                                 status=result.status, complete=self._complete(result))
                return result

            async def prepare_initial(item):
                index, paper = item
                result = await prepare(paper)
                result.metadata["fulltext_provenance"] = {
                    "role": "original", "attempt_order": index + 1,
                    "original_paper_id": paper.paper_id,
                }
                return result

            initial = await self._map(prepare_initial, list(enumerate(run.selected_papers)))
            for result in initial:
                attempted[result.paper_id] = result
                attempt_order.append(result.paper_id)
            complete = [p for p in initial if self._complete(p)]
            complete_ids = {p.paper_id for p in complete}
            unavailable = [p for p in initial if p.paper_id not in complete_ids]
            slots = [p.paper_id for p in unavailable]
            filled_slots: set[str] = set()
            original_ids = {p.paper_id for p in run.selected_papers}
            remaining = [p for p in run.papers if p.paper_id not in original_ids]
            replacements: list[dict[str, str]] = []
            replacement_attempts: list[dict[str, Any]] = []
            replacement_budget = self.settings.max_fulltext_attempts or len(remaining)
            replacements_attempted = 0
            while len(complete) < run.top_k and remaining and replacements_attempted < replacement_budget:
                requested = min(run.top_k - len(complete), replacement_budget - replacements_attempted)
                if requested <= 0:
                    break
                ordered = self._replacement_order(remaining, coverage, complete_ids, full_order)
                batch = ordered[:requested]
                batch_ids = {paper.paper_id for paper in batch}
                remaining = [paper for paper in remaining if paper.paper_id not in batch_ids]
                replacements_attempted += len(batch)
                for paper in await self._map(prepare, batch):
                    complete_flag = self._complete(paper)
                    readable = self._readable(paper)
                    original_id = next(
                        (slot for slot in slots if slot not in filled_slots), None
                    )
                    if complete_flag:
                        role = "replacement_complete"
                        if original_id is not None:
                            filled_slots.add(original_id)
                            replacements.append({
                                "original_paper_id": original_id,
                                "replacement_paper_id": paper.paper_id,
                            })
                        complete.append(paper)
                        complete_ids.add(paper.paper_id)
                    else:
                        role = "replacement_partial" if readable else "replacement_failed"
                    paper.metadata["fulltext_provenance"] = {
                        "role": role, "attempt_order": len(attempt_order) + 1,
                        "original_paper_id": original_id,
                    }
                    attempted[paper.paper_id] = paper
                    attempt_order.append(paper.paper_id)
                    failure_codes = [
                        error.code for error in paper.errors if isinstance(error.code, str)
                    ]
                    replacement_attempts.append({
                        "attempt_order": len(replacement_attempts) + 1,
                        "replacement_paper_id": paper.paper_id,
                        "original_paper_id": original_id,
                        "status": paper.status,
                        "complete": complete_flag,
                        "readable": readable,
                        "failure_code": failure_codes[0] if failure_codes else None,
                        "failure_codes": failure_codes,
                        "in_analysis": False,
                        "summary_scope": None,
                        "target_direct": any(
                            paper.paper_id in item.get("direct_title_paper_ids", [])
                            for item in coverage.get("targets", [])
                        ),
                        "open_access_url": bool(paper.pdf_url or paper.pdf_urls),
                    })
            # Partial text is usable only with an explicit partial summary scope;
            # it does not displace a complete candidate or count as complete.
            partial = [attempted[paper_id] for paper_id in attempt_order
                       if paper_id in attempted and not self._complete(attempted[paper_id])
                       and self._readable(attempted[paper_id])]
            analysis = (complete + partial)[:run.top_k]
            analysis_ids = {paper.paper_id for paper in analysis}
            for paper in initial:
                provenance = paper.metadata.get("fulltext_provenance", {})
                provenance["in_analysis"] = paper.paper_id in analysis_ids
                paper.metadata["fulltext_provenance"] = provenance
            for item in replacement_attempts:
                item["in_analysis"] = item["replacement_paper_id"] in analysis_ids
                result = attempted.get(item["replacement_paper_id"])
                if result is not None:
                    provenance = result.metadata.get("fulltext_provenance", {})
                    provenance["in_analysis"] = item["in_analysis"]
                    result.metadata["fulltext_provenance"] = provenance
            run.papers = [attempted.get(p.paper_id, p) for p in run.papers]
            run.metrics.update(
                replacements=replacements, replacement_attempts=replacement_attempts,
                analysis_paper_ids=[p.paper_id for p in analysis],
                attempted_paper_ids=attempt_order,
                attempted_count=len(attempt_order),
                replacement_attempt_count=len(replacement_attempts),
                partial_replacement_ids=[
                    item["replacement_paper_id"] for item in replacement_attempts
                    if item["in_analysis"] and not item["complete"]
                ],
                fulltext_count=len(complete),
                fulltext_attempt_limit_reached=bool(
                    self.settings.max_fulltext_attempts and remaining
                    and len(complete) < run.top_k),
                replacement_candidates_remaining=[p.paper_id for p in remaining],
                partial_fulltext_count=sum(not self._complete(p) for p in analysis),
            )
            for paper in attempted.values():
                _add_errors(run, paper.errors)
            downloads = sum(int(p.metadata.get("downloads", {}).get("request_count", 0)) for p in attempted.values())
            run.metrics["http_requests"]["download"] = downloads
            run.metrics["http_requests"]["total"] += downloads
            run.metrics["cache_hits"] = {
                "download": sum(bool(p.metadata.get("downloads", {}).get("cache_hit")) for p in attempted.values()),
                "conversion": sum(bool(p.fulltext_quality.get("cache_hit")) for p in attempted.values()),
            }
            self._refresh_target_coverage(run, "fulltexts")
            run.status = "fulltexts_ready" if analysis else "failed"
            return self._finish(run, "fulltexts", start, before, successes=len(complete), requests=downloads)

    async def summarize_selected_papers(self, run_id: str) -> PipelineResult:
        async with self._lock(run_id):
            run = self._load(run_id)
            self._require(run, "fulltexts")
            if "summarize" in run.metrics["completed_stages"]:
                return run
            start, before = time.perf_counter(), _usage(self.llm)
            by_id = {p.paper_id: p for p in run.papers}
            async def summarize(paper_id):
                self.store.event(run_id, "research_summarize", event="start", paper_id=paper_id,
                                 agent="llm_research_analyst", synthetic=run.mode == "demo")
                try:
                    async with self._llm_gate:
                        value = await self.summarize(run.idea, by_id[paper_id],
                                                     settings=self._settings_for(run), llm=self._model())
                except Exception as exc:
                    value = PaperSummary(paper_id=paper_id, summary_scope="failed",
                                         errors=[_failure(exc, "summarize", paper_id)])
                self.store.event(run_id, "research_summarize", event="finish", paper_id=paper_id,
                                 agent="llm_research_analyst", scope=value.summary_scope)
                return value
            run.summaries = await self._map(summarize, run.metrics["analysis_paper_ids"])
            _add_errors(run, [error for summary in run.summaries for error in summary.errors])
            run.metrics["fulltext_summary_count"] = sum(
                summary.summary_scope == "fulltext"
                and not summary.errors
                and bool(summary.overview.strip())
                and bool(summary.deep_analysis.strip())
                for summary in run.summaries
            )
            summaries_by_id = {summary.paper_id: summary for summary in run.summaries}
            for item in run.metrics.get("replacement_attempts", []):
                if not isinstance(item, dict):
                    continue
                summary = summaries_by_id.get(item.get("replacement_paper_id"))
                if summary is not None:
                    item["summary_scope"] = summary.summary_scope
                    item["summary_complete"] = summary.summary_scope == "fulltext" and not summary.errors
            self._refresh_target_coverage(run, "summarize")
            run.status = "summarized" if run.summaries else "failed"
            return self._finish(run, "summarize", start, before,
                                successes=sum(s.summary_scope != "failed" for s in run.summaries))

    async def generate_research_report(self, run_id: str) -> PipelineResult:
        async with self._lock(run_id):
            run = self._load(run_id)
            self._require(run, "summarize")
            if "report" in run.metrics["completed_stages"]:
                return run
            start, before = time.perf_counter(), _usage(self.llm)
            self.store.event(run_id, "research_report", event="start", agent="llm_research_analyst",
                             synthetic=run.mode == "demo")
            try:
                async with self._llm_gate:
                    run.report = await self.report(run.idea, run.summaries,
                                                    settings=self._settings_for(run), llm=self._model())
                _add_errors(run, run.report.errors)
            except Exception as exc:
                run.errors.append(_failure(exc, "report"))
            report = run.report
            available = {s.paper_id for s in run.summaries if s.summary_scope != "failed" and s.research_question.strip()}
            expected_scope = "demo_fixture" if run.mode == "demo" else "fulltext"
            summary_inputs_valid = bool(run.summaries) and all(
                summary.summary_scope == expected_scope
                and not summary.errors
                and bool(summary.overview.strip())
                and bool(summary.deep_analysis.strip())
                for summary in run.summaries
            )
            run.metrics["report_summary_inputs_valid"] = summary_inputs_valid
            report_valid = bool(
                report and report.overview.strip() and report.report_path
                and Path(report.report_path).is_file() and report.markdown.strip()
                and {r.paper_id for r in report.references} == available and available
                and summary_inputs_valid
                and not [e for e in report.errors if run.mode != "demo" or e.code != "partial_report"]
            )
            run.metrics["report_valid"] = report_valid
            run.metrics["report_reference_ids"] = (
                [reference.paper_id for reference in report.references] if report else []
            )
            self._refresh_target_coverage(run, "report")
            complete = (report_valid and run.metrics.get("fulltext_count", 0) >= run.top_k
                        and len(run.selected_papers) == run.top_k
                        and not run.metrics.get("selector_degraded")
                        and not run.metrics.get("query_degraded")
                        and all(run.metrics["source_success"].get(source, False) for source in self.searchers))
            if run.mode == "demo":
                complete = complete and len(run.summaries) == run.top_k and all(s.summary_scope == "demo_fixture" for s in run.summaries)
            else:
                complete = (complete and run.metrics.get("fulltext_summary_count", 0) == run.top_k
                            and run.metrics["llm_usage"].get("requests", 0) > 0
                            and all(run.metrics["source_requests"].get(source, 0) > 0 for source in self.searchers)
                            and not any(p.metadata.get("synthetic") or p.fulltext_quality.get("synthetic")
                                        for p in run.papers if p.paper_id in run.metrics["analysis_paper_ids"]))
            run.metrics["processing_complete"] = bool(
                report_valid and run.metrics.get("fulltext_count", 0) >= run.top_k
                and len(run.summaries) == run.top_k
                and all(s.summary_scope == ("demo_fixture" if run.mode == "demo" else "fulltext") and
                        not s.errors for s in run.summaries)
                and not run.metrics.get("selector_degraded"))
            run.metrics["online_complete"] = bool(complete and run.mode == "live" and not run.metrics["frozen_input"])
            run.status = ("demo_completed" if run.mode == "demo" else "completed") if complete else "partial" if available or run.papers else "failed"
            self.store.event(run_id, "research_report", event="finish", agent="llm_research_analyst", status=run.status)
            return self._finish(run, "report", start, before, successes=int(report_valid))

    async def run_research_pipeline(self, idea: str, k: int = 5, query: str = "",
                                    papers: list[Paper] | None = None) -> PipelineResult:
        start = time.perf_counter()
        if papers is None:
            run = await self.search_papers(idea, k, query)
        else:
            run = self._new(idea, k, query)
            run.papers = [paper.model_copy(deep=True) for paper in papers]
            if len({p.paper_id for p in run.papers}) != len(run.papers):
                raise ValueError("冻结的论文输入必须包含唯一的论文 ID。")
            run.metrics.update(frozen_input=True, unique_count=len(run.papers),
                               source_success={source: False for source in self.searchers},
                               frozen_source_presence={source: any(source in p.sources for p in run.papers)
                                                       for source in self.searchers})
            run.source_counts = {source: sum(source in p.sources for p in run.papers) for source in self.searchers}
            run.status = "searched" if papers else "failed"
            self._finish(run, "search", start, _usage(self.llm), successes=len(papers))
        if run.papers:
            run = await self.select_top_k_papers(run.run_id)
            if run.selected_papers:
                run = await self.prepare_fulltexts(run.run_id)
                run = await self.summarize_selected_papers(run.run_id)
                run = await self.generate_research_report(run.run_id)
        run.timings.append(StageTiming(stage="total", duration_seconds=time.perf_counter() - start,
                                       request_count=sum(t.request_count for t in run.timings),
                                       success_count=int(run.status == "completed"), error_count=len(run.errors)))
        self.store.save(run)
        return run
