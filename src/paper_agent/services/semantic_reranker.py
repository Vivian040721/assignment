"""Sentence-BERT candidate reranking with reciprocal-rank fusion.

The keyword rank produced by ``merge_dedupe_rank`` stays authoritative for
``initial_score``. This module only adds semantic ranks and an RRF ordering
before the merged-candidate truncation step. All model work runs in a worker
thread; the process lazily shares one model and a bounded content-hash vector
cache. Any failure returns the untouched keyword ordering and an audit record.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from paper_agent.models import Paper

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
RRF_K = 60
RRF_WEIGHT = 0.5
VECTOR_CACHE_LIMIT = 4096

_MODEL = None
_MODEL_LOCK = threading.Lock()
_ENCODE_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_VECTOR_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()


def _cache_get(key: str) -> list[float] | None:
    with _CACHE_LOCK:
        value = _VECTOR_CACHE.get(key)
        if value is not None:
            _VECTOR_CACHE.move_to_end(key)
        return value


def _cache_put(key: str, value: list[float]) -> None:
    with _CACHE_LOCK:
        _VECTOR_CACHE[key] = value
        _VECTOR_CACHE.move_to_end(key)
        while len(_VECTOR_CACHE) > VECTOR_CACHE_LIMIT:
            _VECTOR_CACHE.popitem(last=False)


def cache_size() -> int:
    with _CACHE_LOCK:
        return len(_VECTOR_CACHE)


def _content_key(kind: str, *parts: str) -> str:
    digest = hashlib.sha256()
    digest.update(MODEL_NAME.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(kind.encode("utf-8"))
    for part in parts:
        digest.update(b"\x00")
        digest.update(part.encode("utf-8", "surrogatepass"))
    return digest.hexdigest()


def _paper_text(paper: Paper) -> str:
    title = " ".join(paper.title.split())
    abstract = " ".join(paper.abstract.split())
    return title if not abstract else title + "\n" + abstract


def _load_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            # Lazy import keeps keyword mode independent of the optional extra.
            from sentence_transformers import SentenceTransformer

            # The cache is prepared during installation. Local-only loading avoids
            # a network round-trip and makes an incomplete cache a recorded fallback.
            _MODEL = SentenceTransformer(MODEL_NAME, local_files_only=True)
        return _MODEL


def _encode(texts: list[str]) -> list[list[float]]:
    with _ENCODE_LOCK:
        model = _load_model()
        vectors = model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
    return [[float(value) for value in row] for row in vectors]


def _fallback_reason(exc: BaseException, phase: str) -> str:
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "dependency_missing"
    if phase == "load":
        return "model_load_failed"
    if phase == "encode":
        return "embedding_failed"
    return "semantic_rerank_failed"


_FALLBACK_MESSAGES = {
    "dependency_missing": "语义排序依赖未安装，已回退为关键词排序。",
    "model_load_failed": "语义模型加载失败（本地模型缓存可能不完整），已回退为关键词排序。",
    "embedding_failed": "语义向量计算失败，已回退为关键词排序。",
    "empty_query": "检索词为空，语义排序未执行，已保留关键词排序。",
    "no_candidates": "没有可重排候选，已保留关键词顺序。",
    "semantic_rerank_failed": "语义排序过程中出现异常，已回退为关键词排序。",
}


def _keyword_rank(paper: Paper, index: int) -> int:
    return paper.rank if isinstance(paper.rank, int) and paper.rank >= 1 else index + 1


def _base_audit(papers: list[Paper]) -> dict[str, Any]:
    return {
        "requested_ranker": "semantic",
        "actual_ranker": "keyword",
        "model_name": MODEL_NAME,
        "fallback_reason": None,
        "fallback_message": None,
        "candidate_count": len(papers),
        "encoded_count": 0,
        "cache_hits": 0,
        "model_load_seconds": 0.0,
        "encode_seconds": 0.0,
        "duration_seconds": 0.0,
        "rrf_k": RRF_K,
        "rrf_weights": [RRF_WEIGHT, RRF_WEIGHT],
        "vector_cache_capacity": VECTOR_CACHE_LIMIT,
        "vector_cache_size": cache_size(),
    }


def _fallback(papers: list[Paper], audit: dict[str, Any], reason: str, error_type: str) -> tuple[list[Paper], dict[str, Any]]:
    audit.update(
        actual_ranker="keyword",
        fallback_reason=reason,
        fallback_message=_FALLBACK_MESSAGES.get(reason, _FALLBACK_MESSAGES["semantic_rerank_failed"]),
        error_type=error_type,
        vector_cache_size=cache_size(),
    )
    logger.warning("Semantic reranking degraded: reason=%s candidates=%d", reason, len(papers))
    return [paper.model_copy(deep=True) for paper in papers], audit


async def semantic_rerank(
    query: str, papers: list[Paper],
) -> tuple[list[Paper], dict[str, Any]]:
    """Return all candidates ordered by RRF or an exact keyword-order fallback.

    No Paper is mutated until every vector and every rank has been computed.
    When the keyword score is the final result, the caller still performs the
    merged-candidate truncation afterwards.
    """
    started = time.perf_counter()
    audit = _base_audit(papers)
    if not query.strip():
        audit["duration_seconds"] = round(time.perf_counter() - started, 6)
        return _fallback(papers, audit, "empty_query", "ValueError")
    if not papers:
        audit.update(
            actual_ranker="keyword",
            fallback_reason="no_candidates",
            fallback_message=_FALLBACK_MESSAGES["no_candidates"],
            duration_seconds=round(time.perf_counter() - started, 6),
        )
        return [paper.model_copy(deep=True) for paper in papers], audit

    texts = [_paper_text(paper) for paper in papers]
    keys = [_content_key("paper", text) for text in texts]
    query_key = _content_key("query", " ".join(query.split()))

    try:
        cached = [_cache_get(key) for key in keys]
        query_vector = _cache_get(query_key)
        query_cache_hit = query_vector is not None
        hits = sum(vector is not None for vector in cached)
        missing_indices = [index for index, vector in enumerate(cached) if vector is None]
        missing_texts = [texts[index] for index in missing_indices]
        if query_vector is None:
            missing_texts.insert(0, " ".join(query.split()))

        load_started = 0.0
        encode_started = 0.0
        if missing_texts:
            load_started = time.perf_counter()
            try:
                # Loading is performed inside the worker so the event loop is never blocked.
                await asyncio.to_thread(_load_model)
            except Exception as exc:  # noqa: BLE001 - the failure is classified and sanitized
                audit["model_load_seconds"] = round(time.perf_counter() - load_started, 6)
                audit["duration_seconds"] = round(time.perf_counter() - started, 6)
                return _fallback(papers, audit, _fallback_reason(exc, "load"), type(exc).__name__)
            audit["model_load_seconds"] = round(time.perf_counter() - load_started, 6)
            encode_started = time.perf_counter()
            try:
                encoded = await asyncio.to_thread(_encode, missing_texts)
            except Exception as exc:  # noqa: BLE001
                audit["encode_seconds"] = round(time.perf_counter() - encode_started, 6)
                audit["duration_seconds"] = round(time.perf_counter() - started, 6)
                return _fallback(papers, audit, _fallback_reason(exc, "encode"), type(exc).__name__)
            audit["encode_seconds"] = round(time.perf_counter() - encode_started, 6)
            offset = 0
            if query_vector is None:
                query_vector = encoded[0]
                _cache_put(query_key, query_vector)
                offset = 1
            for index, vector in zip(missing_indices, encoded[offset:], strict=True):
                cached[index] = vector
                _cache_put(keys[index], vector)
        audit.update(encoded_count=len(missing_indices), cache_hits=hits,
                     query_cache_hit=query_cache_hit)

        keyword_ranks = {paper.paper_id: _keyword_rank(paper, index) for index, paper in enumerate(papers)}
        if len(keyword_ranks) != len(papers):
            raise ValueError("Duplicate paper IDs")
        cosines = [max(-1.0, min(1.0, sum(a * b for a, b in zip(query_vector, vector, strict=True))))
                   for vector in cached]
        semantic_order = sorted(
            range(len(papers)),
            key=lambda index: (-cosines[index], keyword_ranks[papers[index].paper_id], papers[index].paper_id),
        )
        semantic_ranks = {papers[index].paper_id: rank for rank, index in enumerate(semantic_order, 1)}
        rrf_scores = {
            paper.paper_id: RRF_WEIGHT / (RRF_K + keyword_ranks[paper.paper_id])
            + RRF_WEIGHT / (RRF_K + semantic_ranks[paper.paper_id])
            for paper in papers
        }
        rrf_order = sorted(
            range(len(papers)),
            key=lambda index: (-rrf_scores[papers[index].paper_id],
                               keyword_ranks[papers[index].paper_id], papers[index].paper_id),
        )
        rrf_ranks = {papers[index].paper_id: rank for rank, index in enumerate(rrf_order, 1)}

        updated: list[Paper] = []
        for index in rrf_order:
            paper = papers[index].model_copy(deep=True)
            details = dict(paper.ranking_details)
            details.update(
                ranker="semantic_rrf",
                keyword_rank=keyword_ranks[paper.paper_id],
                semantic_rank=semantic_ranks[paper.paper_id],
                cosine_similarity=round(cosines[index], 6),
                rrf_rank=rrf_ranks[paper.paper_id],
                rrf_score=round(rrf_scores[paper.paper_id], 9),
                rrf_k=RRF_K,
                rrf_weights=[RRF_WEIGHT, RRF_WEIGHT],
                model_name=MODEL_NAME,
                score_semantics="rrf_score is a ranking-fusion score, not a probability",
            )
            paper.ranking_details = details
            paper.rank = rrf_ranks[paper.paper_id]
            order_note = (
                f"语义融合：关键词名次 {keyword_ranks[paper.paper_id]}，语义名次 {semantic_ranks[paper.paper_id]}，"
                f"余弦相似度 {cosines[index]:.4f}，RRF 名次 {rrf_ranks[paper.paper_id]}（RRF 分数不是概率）。"
            )
            paper.ranking_reason = (paper.ranking_reason + " " + order_note).strip() if paper.ranking_reason else order_note
            updated.append(paper)

        audit.update(
            actual_ranker="semantic",
            fallback_reason=None,
            fallback_message=None,
            duration_seconds=round(time.perf_counter() - started, 6),
            vector_cache_size=cache_size(),
        )
        return updated, audit
    except Exception as exc:  # noqa: BLE001 - an unexpected fusion failure must degrade atomically
        audit["duration_seconds"] = round(time.perf_counter() - started, 6)
        return _fallback(papers, audit, _fallback_reason(exc, "rank"), type(exc).__name__)


__all__ = ["MODEL_NAME", "RRF_K", "RRF_WEIGHT", "semantic_rerank", "cache_size"]
