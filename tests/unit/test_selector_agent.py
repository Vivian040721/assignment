"""Deterministic fake-model tests; these do not validate real scholarly relevance."""

import asyncio
import copy
import json
from dataclasses import replace

import pytest

from paper_agent.agents.selector_agent import (
    SELECTOR_SYSTEM_PROMPT,
    SelectorError,
    build_selection_payload,
    select_top_k,
    validate_selection,
)
from paper_agent.config import Settings
from paper_agent.llm_client import LLMOutputError
from paper_agent.models import Paper


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def json(self, system, user):
        self.calls.append((system, json.loads(user)))
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.response)


def candidates(count=12):
    return [
        Paper(
            paper_id=f"p{i}", title=f"Agent memory paper {i}",
            abstract=f"This paper investigates long-term memory and task success, approach {i}.",
            authors=[f"Author {i}"], year=2020 + i % 6,
            doi=f"10.1234/p{i}", arxiv_id=f"2601.{i:05d}",
            sources=["arxiv", "openalex"],
            source_records=[{"source": "arxiv", "id": str(i)}],
            initial_score=1 - i / count,
            pdf_url=f"https://arxiv.org/pdf/2601.{i:05d}" if i % 2 else None,
            metadata={"nested": {"unchanged": [1]}},
        )
        for i in range(count)
    ]


def response(ids):
    return {"papers": [
        {
            "paper_id": paper_id, "final_score": 1 - i / (len(ids) + 1),
            "selection_reason": f"Title/abstract addresses memory reuse: {paper_id}",
            "rank": i + 1,
        }
        for i, paper_id in enumerate(ids)
    ]}


def test_twelve_candidates_llm_can_choose_lower_initial_rank_with_all_metadata():
    papers = candidates()
    original = copy.deepcopy([p.model_dump() for p in papers])
    llm = FakeLLM(response(["p10", "p8", "p6"]))
    selected = asyncio.run(select_top_k("Reuse memory to reduce agent cost", papers, 3, llm=llm))
    assert [p.paper_id for p in selected] == ["p10", "p8", "p6"]
    assert [p.rank for p in selected] == [1, 2, 3]
    assert selected[0].doi == papers[10].doi
    assert selected[0].arxiv_id == papers[10].arxiv_id
    assert selected[0].source_records == papers[10].source_records
    assert selected[0].authors == papers[10].authors
    assert selected[0].sources == ["arxiv", "openalex"]
    assert selected[0].status == "selected"
    assert [p.model_dump() for p in papers] == original
    audit = selected[0].metadata["selection"]
    assert audit["input_candidate_order"] == [p.paper_id for p in papers]
    assert audit["initial_scores"]["p0"] == 1
    assert audit["llm_scores"]["p10"] == 1
    assert audit["llm_scores"]["p0"] is None
    assert "p0" in audit["not_scored_ids"]
    assert audit["llm_success"] is True
    assert audit["degraded"] is False
    selected[0].metadata["nested"]["unchanged"].append(2)
    selected[0].metadata["selection"]["input_candidate_order"].append("changed")
    assert papers[10].metadata == {"nested": {"unchanged": [1]}}
    assert "changed" not in selected[1].metadata["selection"]["input_candidate_order"]
    system, payload = llm.calls[0]
    assert system == SELECTOR_SYSTEM_PROMPT
    assert "NOT academic relevance" in system
    assert len(payload["candidates"]) == 12
    assert payload["requested_count"] == 3
    assert payload["candidates"][1]["open_access"]["pdf_url_available"] is True


def test_complete_ranking_also_accepted_and_array_order_uses_rank():
    papers = candidates()
    value = response([p.paper_id for p in reversed(papers)])
    value["papers"].reverse()
    llm = FakeLLM(value)
    selected = asyncio.run(select_top_k("Memory", papers, 2, llm=llm))
    assert [p.paper_id for p in selected] == ["p11", "p10"]
    assert selected[0].metadata["selection"]["not_scored_ids"] == []


@pytest.mark.parametrize("problem", [
    "unknown_id", "duplicate_id", "wrong_count", "score_high", "score_low", "nan",
    "score_string", "score_bool", "rank_bool", "rank_float", "rank_gap", "score_order",
    "blank_reason", "extra_field", "wrong_root", "empty",
])
def test_rejects_semantically_or_structurally_invalid_llm_output(problem):
    value = response(["p1", "p2", "p3"])
    if problem == "unknown_id":
        value["papers"][0]["paper_id"] = "fabricated"
    elif problem == "duplicate_id":
        value["papers"][1]["paper_id"] = "p1"
    elif problem == "wrong_count":
        value["papers"].pop()
    elif problem.startswith("score_") and problem != "score_order":
        value["papers"][0]["final_score"] = {
            "score_high": 1.1, "score_low": -0.1, "score_string": "0.9", "score_bool": True,
        }[problem]
    elif problem == "nan":
        value["papers"][0]["final_score"] = float("nan")
    elif problem.startswith("rank_"):
        value["papers"][0]["rank"] = {
            "rank_bool": True, "rank_float": 1.0, "rank_gap": 99,
        }[problem]
    elif problem == "score_order":
        value["papers"][0]["final_score"] = 0
    elif problem == "blank_reason":
        value["papers"][0]["selection_reason"] = "  "
    elif problem == "extra_field":
        value["papers"][0]["doi"] = "invented"
    elif problem == "wrong_root":
        value = value["papers"]
    elif problem == "empty":
        value["papers"] = []
    llm = FakeLLM(value)
    with pytest.raises(SelectorError) as failure:
        asyncio.run(select_top_k("Memory", candidates(), 3, llm=llm))
    assert failure.value.error.code == "selector_invalid_output"
    assert len(llm.calls) == 1  # Output violations are not retried into success.


def test_reason_alias_supported_but_not_both_spellings():
    value = response(["p1"])
    value["papers"][0]["reason"] = value["papers"][0].pop("selection_reason")
    assert validate_selection(value, candidates(), 1)[0].selection_reason
    value["papers"][0]["selection_reason"] = "duplicate alias"
    with pytest.raises(SelectorError):
        validate_selection(value, candidates(), 1)


@pytest.mark.parametrize("exception,code", [
    (RuntimeError("PRIVATE_PROVIDER_MESSAGE"), "selector_llm_failure"),
    (LLMOutputError("PRIVATE_PROVIDER_MESSAGE"), "selector_invalid_output"),
])
def test_failure_sanitized_and_json_errors_fail_without_second_retry(exception, code, caplog):
    llm = FakeLLM(error=exception)
    with pytest.raises(SelectorError) as failure:
        asyncio.run(select_top_k("Memory", candidates(), 3, llm=llm))
    assert failure.value.error.code == code
    assert "PRIVATE_PROVIDER_MESSAGE" not in str(failure.value)
    assert "PRIVATE_PROVIDER_MESSAGE" not in caplog.text
    assert len(llm.calls) == 1


def test_demo_mode_does_not_implicitly_replace_real_llm_with_fixed_selection():
    with pytest.raises(SelectorError) as failure:
        asyncio.run(select_top_k("Memory", candidates(), 3, settings=Settings(mode="demo")))
    assert failure.value.error.code == "selector_llm_failure"


def test_explicit_initial_ranking_fallback_is_marked_and_does_not_claim_model_scores():
    papers = list(reversed(candidates()))
    original = copy.deepcopy([p.model_dump() for p in papers])
    settings = replace(Settings(), selector_fallback="initial_ranking")
    selected = asyncio.run(select_top_k(
        "Memory", papers, 3, settings=settings, llm=FakeLLM(response(["invented"])),
    ))
    assert [p.paper_id for p in selected] == ["p0", "p1", "p2"]
    for paper in selected:
        assert paper.final_score is None
        assert "降级" in paper.selection_reason
        assert paper.errors[-1].code == "selector_fallback"
        audit = paper.metadata["selection"]
        assert audit["degraded"] is True
        assert audit["llm_success"] is False
        assert audit["method"] == "initial_ranking_fallback"
        assert all(score is None for score in audit["llm_scores"].values())
    assert [p.model_dump() for p in papers] == original
    selected[0].metadata["selection"]["final_order"].clear()
    assert len(selected[1].metadata["selection"]["final_order"]) == 3


def test_candidate_limit_excludes_later_candidates_from_model_and_audits_them():
    papers = candidates(20)
    settings = replace(Settings(), candidate_limit=10)
    llm = FakeLLM(response(["p9", "p8", "p7"]))
    selected = asyncio.run(select_top_k("Memory", papers, 3, settings=settings, llm=llm))
    assert len(llm.calls[0][1]["candidates"]) == 10
    assert selected[0].metadata["selection"]["excluded_by_candidate_limit"] == [
        f"p{i}" for i in range(10, 20)
    ]
    llm.response = response(["p11", "p8", "p7"])
    with pytest.raises(SelectorError):
        asyncio.run(select_top_k("Memory", papers, 3, settings=settings, llm=llm))


def test_long_multilingual_abstracts_share_budget_and_truncation_is_explicit():
    papers = candidates()
    for paper in papers:
        paper.abstract = "长期记忆 costs \\ quoted \"\n" * 3000
    settings = replace(Settings(), llm_input_token_budget=8000)
    payload, audit = build_selection_payload("Memory", papers, 3, settings)
    assert audit["input_units"] <= 8000
    assert all(item["truncated"] for item in audit["abstract_lengths"])
    assert all(item["submitted_chars"] > 0 for item in audit["abstract_lengths"])
    assert payload["candidates"][0]["title"] == papers[0].title
    assert papers[0].abstract.endswith("\n")


def test_required_metadata_over_budget_fails_before_calling_model():
    llm = FakeLLM(response(["p0"]))
    with pytest.raises(SelectorError) as failure:
        asyncio.run(select_top_k(
            "Research idea" * 2000, candidates(), 1,
            settings=replace(Settings(), llm_input_token_budget=4000), llm=llm,
        ))
    assert failure.value.error.code == "selector_input_budget"
    assert llm.calls == []


@pytest.mark.parametrize("k", [0, -1, True, 1.0, 101])
def test_invalid_k_rejected(k):
    with pytest.raises(ValueError):
        asyncio.run(select_top_k("Memory", candidates(), k, llm=FakeLLM()))


def test_empty_candidates_no_model_call_and_invalid_inputs_are_not_hidden_by_fallback():
    llm = FakeLLM()
    assert asyncio.run(select_top_k("Memory", [], 3, llm=llm)) == []
    assert llm.calls == []
    with pytest.raises(ValueError, match="非空"):
        asyncio.run(select_top_k("  ", candidates(), 3, llm=llm))
    with pytest.raises(ValueError, match="唯一候选"):
        asyncio.run(select_top_k("Memory", [candidates()[0]] * 2, 1, llm=llm))
    with pytest.raises(ValueError, match="覆盖"):
        asyncio.run(select_top_k(
            "Memory", candidates(), 5, settings=replace(Settings(), candidate_limit=3), llm=llm,
        ))


def test_fewer_papers_than_k_is_valid_but_uses_exact_available_count():
    llm = FakeLLM(response(["p0", "p1"]))
    selected = asyncio.run(select_top_k("Memory", candidates(2), 5, llm=llm))
    assert len(selected) == 2
    assert llm.calls[0][1]["requested_count"] == 2


def test_cancellation_propagates_instead_of_producing_a_fallback():
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await select_top_k(
                "Memory", candidates(), 3,
                settings=replace(Settings(), selector_fallback="initial_ranking"),
                llm=FakeLLM(error=asyncio.CancelledError()),
            )
    asyncio.run(run())


def test_selector_prompt_requires_chinese_reason():
    assert "Simplified Chinese" in SELECTOR_SYSTEM_PROMPT
