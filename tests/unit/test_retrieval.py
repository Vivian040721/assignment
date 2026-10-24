import asyncio

import pytest

from paper_agent.models import Paper
from paper_agent.services.retrieval import (
    merge_dedupe_rank,
    normalize_arxiv_id,
    normalize_doi,
    plan_queries,
    retrieval_counts,
)


def paper(pid, **kwargs):
    return Paper(paper_id=pid, title="Long term memory for language model agents", authors=["Alice Chen"], year=2024, **kwargs)


def test_doi_merge_preserves_source_details_and_input_objects():
    left = paper("arxiv:2401.12345", doi="https://doi.org/10.1234/ABC", arxiv_id="2401.12345v2", sources=["arxiv"], pdf_url="https://arxiv.org/pdf/2401.12345", abstract="Short")
    right = paper("openalex:W1", doi="doi:10.1234/abc", sources=["openalex"], pdf_url="https://example.org/a.pdf", pdf_urls=["https://example.org/b.pdf"], abstract="Longer and more complete abstract", citation_count=12, source_records=[{"id": "W1"}], metadata={"publisher": "Example"})
    before = [p.model_dump() for p in [left, right]]
    result = merge_dedupe_rank([right, left], "agent memory")[0]
    assert result.doi == "10.1234/abc" and result.arxiv_id == "2401.12345"
    assert result.arxiv_version == "v2"
    assert result.sources == ["arxiv", "openalex"]
    assert len(result.pdf_urls) == 3 and result.abstract == right.abstract
    assert result.citation_count == 12 and result.metadata["publisher"] == "Example"
    assert len(result.metadata["retrieval_records"]) == 2
    assert result.source_records == [{"id": "W1"}]
    assert [p.model_dump() for p in [left, right]] == before
    assert retrieval_counts([result]) == {"input_count": 2, "unique_count": 1, "duplicate_count": 1}


def test_arxiv_versions_merge_and_legacy_identifier_normalization():
    result = merge_dedupe_rank([
        paper("a", arxiv_id="https://arxiv.org/abs/2401.12345v1"),
        paper("b", arxiv_id="2401.12345", arxiv_version="v3"),
    ], "memory")
    assert len(result) == 1 and result[0].arxiv_version == "v3"
    assert normalize_arxiv_id("https://arxiv.org/pdf/hep-th/9901001v2.pdf") == "hep-th/9901001"
    assert normalize_arxiv_id("https://arxiv.org/pdf/2401.12345v2.pdf?download=1") == "2401.12345"
    assert normalize_doi("https://dx.doi.org/10.1000%2FABC") == "10.1000/abc"
    assert normalize_doi("unknown") is None
    assert normalize_arxiv_id("not-an-arxiv-id") is None


def test_conflicting_dois_cannot_be_bridged_even_by_same_arxiv():
    data = [paper("a", doi="10.1000/a", arxiv_id="2401.12345"), paper("b"), paper("c", doi="10.1000/c", arxiv_id="2401.12345")]
    ranked = merge_dedupe_rank(data, "memory")
    assert len(ranked) == 2
    assert {p.doi for p in ranked} == {"10.1000/a", "10.1000/c"}
    assert max(len(p.metadata["merged_paper_ids"]) for p in ranked) == 2


def test_identifier_bridge_consolidates_prior_compatible_groups():
    data = [
        Paper(paper_id="first", title="First title", arxiv_id="2401.11111", doi="10.1000/a"),
        Paper(paper_id="second", title="Second title", arxiv_id="2401.22222"),
        Paper(paper_id="bridge", title="Third title", arxiv_id="2401.22222", doi="10.1000/a"),
    ]
    ranked = merge_dedupe_rank(data, "memory")
    assert len(ranked) == 1
    assert len(ranked[0].metadata["merged_paper_ids"]) == 3


def test_title_match_requires_author_and_year_evidence():
    base = paper("a")
    no_author = base.model_copy(update={"paper_id": "b", "authors": []})
    old_year = base.model_copy(update={"paper_id": "c", "year": 2020})
    no_year = base.model_copy(update={"paper_id": "d", "year": None})
    assert len(merge_dedupe_rank([base, no_author, old_year, no_year], "memory")) == 4
    near = base.model_copy(update={"paper_id": "e", "title": base.title + "!", "authors": ["A. Chen"], "year": 2025})
    assert len(merge_dedupe_rank([base, near], "memory")) == 1


def test_ranking_is_reproducible_normalized_and_explained():
    strong = Paper(paper_id="strong", title="Agent memory optimization", abstract="Memory optimization for agents", year=2023)
    weak = Paper(paper_id="weak", title="Unrelated quantum chemistry", year=2030)
    data = [weak, strong]
    ranked = merge_dedupe_rank(data, "agent memory optimization")
    reversed_ranked = merge_dedupe_rank(list(reversed(data)), "agent memory optimization")
    assert [p.model_dump() for p in ranked] == [p.model_dump() for p in reversed_ranked]
    assert ranked[0].paper_id == "strong" and ranked[1].initial_score == 0
    assert all(0 <= p.initial_score <= 1 for p in ranked)
    assert ranked[0].ranking_details["reference_year"] == 2030
    assert ranked[0].ranking_details["title_hits"] == ["agent", "memory", "optimization"]
    assert ranked[0].ranking_reason
    assert merge_dedupe_rank([], "anything") == []
    assert retrieval_counts([]) == {"input_count": 0, "unique_count": 0, "duplicate_count": 0}


class FakeLLM:
    def __init__(self, response):
        self.response = response

    async def json(self, system, user):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_chinese_query_uses_real_injected_planner_and_validates_output():
    async def run():
        result = await plan_queries("使用长期记忆降低Agent推理成本", llm=FakeLLM({"queries": ["agent long term memory inference cost", "memory efficient language agents"]}))
        assert result["mode"] == "llm" and len(result["queries"]) == 2
        assert result["ranking_query"] == result["queries"][0]
        bad = await plan_queries("长期记忆", llm=FakeLLM({"queries": ["长期记忆"]}))
        assert bad["mode"] == "needs_translation" and bad["queries"] == []
        assert bad["errors"][0]["code"] == "QUERY_PLANNING_FAILED"
    asyncio.run(run())


def test_query_fallback_is_explicit_and_never_leaks_exception():
    async def run():
        chinese = await plan_queries("长期记忆降低推理成本")
        assert chinese["queries"] == [] and chinese["mode"] == "needs_translation"
        english = await plan_queries("using agent memory optimization")
        assert english["queries"] == ["agent memory optimization"]
        assert english["mode"] == "english_passthrough"
        fallback = await plan_queries("agent memory", llm=FakeLLM(RuntimeError("secret-key")))
        assert fallback["mode"] == "english_fallback"
        assert "secret-key" not in str(fallback)
        with pytest.raises(ValueError):
            await plan_queries("   ")
    asyncio.run(run())
