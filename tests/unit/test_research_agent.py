"""Research tasks reject invented evidence and expose incomplete reading."""

import asyncio
import json
from functools import wraps
from pathlib import Path

import pytest

from paper_agent.agents.research_agent import (
    REPORT_SYSTEM,
    SUMMARY_SYSTEM,
    generate_report,
    summarize_paper,
)
from paper_agent.config import Settings
from paper_agent.llm_client import LLMBudgetError
from paper_agent.models import ErrorInfo, PageMapping, Paper, PaperSummary, ResearchReport


def test_model_prompts_require_chinese_prose_without_translating_citations():
    assert "Simplified Chinese" in SUMMARY_SYSTEM
    assert "verbatim" in SUMMARY_SYSTEM
    assert "Simplified Chinese" in REPORT_SYSTEM
    assert "references.title" in REPORT_SYSTEM


def test_atomic_artifact_write_with_long_windows_filename(tmp_path):
    from paper_agent.agents.research_agent import _write_pair

    # The final name fits MAX_PATH; appending the name again for a temporary
    # file used to exceed it inside a nested desktop job directory.
    parent = tmp_path / ("nested-" + "a" * 55)
    name_length = max(20, 238 - len(str(parent.resolve())) - 1)
    target = parent / ("r" * (name_length - 3) + ".md")
    report = ResearchReport(idea="fixture", markdown="fixture report")
    _write_pair(target, report)
    assert target.read_text(encoding="utf-8") == "fixture report"
    assert json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))["idea"] == "fixture"
    assert not list(parent.glob("*.tmp"))


def run_async(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapped


def settings_at(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path / "data", **kwargs)


def content_for(request):
    if request["task"] == "merge_paper_summary":
        notes = request["chunk_summaries"]
        response = {
            "overview": "本文根据提供的原文讨论方法、实验及其局限。",
            "deep_analysis": "## 方法与证据\n\n仅综合提供的分块笔记。\n\n"
                             "## 系统推断\n\n解释仍需验证。\n\n"
                             "## 未经验证的研究设想\n\n尚未开展独立实验。",
            "research_question": notes[0]["research_question"],
            "core_idea": notes[0]["core_idea"],
            "relevance": "This supplied evidence is relevant to the idea.",
            "evidence": [{"evidence_id": item["evidence_id"]}
                         for item in request["verified_evidence"]],
        }
        for field in ("methods", "experiments", "findings", "limitations", "author_claims",
                      "system_inferences", "speculative_ideas"):
            response[field] = [value for note in notes for value in note[field]]
        return response
    segment = next(segment for segment in request["segments"] if segment["text"].strip())
    quote = segment["text"].strip()[:80]
    evidence = {"paper_id": request["paper"]["paper_id"], "page_number": segment["page_number"],
                "section": segment["section"], "quote": quote, "claim": "A source-bounded finding.",
                "evidence_type": "author_claim", "source_segment_id": segment["segment_id"]}
    return {"research_question": "How can the described method work?", "core_idea": "Use the supplied evidence.",
            "methods": ["A method in the supplied text."], "experiments": ["Experiment for " + quote],
            "findings": ["A source-bounded finding."], "limitations": ["Limitation for " + quote],
            "author_claims": ["A source-bounded finding."],
            "system_inferences": ["This interpretation needs validation."], "speculative_ideas": [], "evidence": [evidence]}


class RecordingLLM:
    def __init__(self, mutate=None):
        self.requests = []
        self.mutate = mutate

    async def json(self, system, user):
        request = json.loads(user)
        self.requests.append((system, request))
        if request["task"] == "generate_research_report":
            ids = [summary["paper_id"] for summary in request["summaries"]]
            response = {"overview": "Evidence in [" + ids[0] + "] supports a scoped comparison.",
                        "related_work": ["One category of methods."], "comparison": [{"paper_ids": ids, "method": "Compared given summaries."}],
                        "researched_questions": ["What do these methods already address?"],
                        "limitations": ["No independent replication was done."], "research_directions": ["An untested direction requiring experiments."],
                        "references": [{"paper_id": summary["paper_id"], "title": summary["title"]} for summary in request["summaries"]]}
        else:
            response = content_for(request)
        if self.mutate:
            return self.mutate(request, response)
        return response


def full_paper(tmp_path, *, paper_id="arxiv:1234.5678", texts=None, quality=True):
    texts = texts or ["# Method\nA grounded method reduces repeated work.\n", "# Experiments\nThe authors report a small experiment and discuss limitations.\n"]
    text = "".join(texts)
    path = tmp_path / ("source-" + str(abs(hash(paper_id))) + ".md")
    path.write_text(text, encoding="utf-8")
    page_map = []
    offset = 0
    for number, page_text in enumerate(texts, 1):
        page_map.append(PageMapping(page_number=number, text=page_text, start_char=offset, end_char=offset + len(page_text)))
        offset += len(page_text)
    return Paper(paper_id=paper_id, title="A traceable paper", markdown_path=str(path),
                 page_map=page_map, status="converted", fulltext_quality={"complete": quality})


@run_async
async def test_fulltext_quotes_pages_and_json_artifacts(tmp_path):
    model = RecordingLLM()
    summary = await summarize_paper("agent memory", full_paper(tmp_path), settings=settings_at(tmp_path), llm=model)
    assert summary.summary_scope == "fulltext"
    assert summary.evidence[0].page_number == 1
    assert not summary.errors
    assert Path(summary.summary_path).parent == (tmp_path / "data" / "summaries")
    persisted = json.loads(Path(summary.summary_path).with_suffix(".json").read_text(encoding="utf-8"))
    assert persisted["summary_scope"] == "fulltext"
    assert persisted["overview"] and persisted["deep_analysis"]
    assert "系统推断" in summary.markdown and "未经验证的研究设想" in summary.markdown
    assert [request["task"] for _, request in model.requests] == [
        "summarize_chunk", "merge_paper_summary",
    ]
    assert "source_segment_id" not in persisted["evidence"][0]
    assert "evidence_id" not in persisted["evidence"][0]


@pytest.mark.parametrize("bad", ["page", "section", "quote", "id", "scope", "extra",
                                 "no_evidence", "segment", "enum", "evidence_id", "missing_field"])
@run_async
async def test_rejects_model_fabrication(tmp_path, bad):
    def mutate(request, response):
        if bad == "page":
            response["evidence"][0]["page_number"] = 999
        elif bad == "section":
            response["evidence"][0]["section"] = "Invented section"
        elif bad == "quote":
            response["evidence"][0]["quote"] = "An experiment never provided by the source."
        elif bad == "id":
            response["evidence"][0]["paper_id"] = "invented-paper"
        elif bad == "scope":
            response["summary_scope"] = "fulltext"
        elif bad == "extra":
            response["summary_path"] = "C:/do-not-trust.md"
        elif bad == "segment":
            response["evidence"][0]["source_segment_id"] = "unknown-segment"
        elif bad == "enum":
            response["evidence"][0]["evidence_type"] = "作者主张"
        elif bad == "evidence_id":
            response["evidence"][0]["evidence_id"] = "e1"
        elif bad == "missing_field":
            response.pop("findings")
        else:
            response["evidence"] = []
        return response
    paper = full_paper(tmp_path, quality=bad != "scope")
    result = await summarize_paper("agent memory", paper, settings=settings_at(tmp_path), llm=RecordingLLM(mutate))
    assert result.summary_scope == "failed"
    assert any(error.code == "invalid_model_output" for error in result.errors)
    assert not result.evidence


@pytest.mark.parametrize("bad", ["unknown_id", "duplicate_id", "rewritten_record"])
@run_async
async def test_merge_rejects_unverified_evidence_and_preserves_chunk_notes(tmp_path, bad):
    def mutate(request, response):
        if request["task"] != "merge_paper_summary":
            return response
        if bad == "unknown_id":
            response["evidence"][0]["evidence_id"] = "unknown-evidence"
        elif bad == "duplicate_id":
            response["evidence"].append(dict(response["evidence"][0]))
        else:
            response["evidence"][0]["quote"] = "A forged replacement quote."
        return response

    model = RecordingLLM(mutate)
    summary = await summarize_paper("memory", full_paper(tmp_path), settings=settings_at(tmp_path), llm=model)
    assert not summary.overview and not summary.deep_analysis
    assert summary.experiments and summary.evidence
    error = next(error for error in summary.errors if error.code == "invalid_model_output")
    assert error.details["stage"] == "merge"
    assert error.details["repair_attempted"] is True
    assert all(evidence.quote != "A forged replacement quote." for evidence in summary.evidence)
    assert [request["task"] for _, request in model.requests] == [
        "summarize_chunk", "merge_paper_summary", "merge_paper_summary",
    ]


@run_async
async def test_chunk_and_merge_share_one_repair_per_paper(tmp_path):
    def mutate(request, response):
        if request["task"] == "summarize_chunk" and "repair_context" not in request:
            response["evidence"][0]["source_segment_id"] = "unknown-segment"
        elif request["task"] == "merge_paper_summary":
            response["evidence"][0]["evidence_id"] = "unknown-evidence"
        return response

    model = RecordingLLM(mutate)
    summary = await summarize_paper("memory", full_paper(tmp_path), settings=settings_at(tmp_path), llm=model)
    assert [request["task"] for _, request in model.requests] == [
        "summarize_chunk", "summarize_chunk", "merge_paper_summary",
    ]
    assert sum("repair_context" in request for _, request in model.requests) == 1
    assert not summary.overview and not summary.deep_analysis
    assert summary.evidence
    assert summary.errors[0].details["stage"] == "merge"


@run_async
async def test_missing_fulltext_stays_abstract_only(tmp_path):
    paper = Paper(paper_id="p1", title="An abstract", abstract="Only an abstract about a method is available.", markdown_path=str(tmp_path / "missing.md"))
    model = RecordingLLM()
    summary = await summarize_paper("memory", paper, settings=settings_at(tmp_path), llm=model)
    assert summary.summary_scope == "abstract_only"
    assert summary.evidence[0].page_number is None
    assert "仅读取了提供的摘要" in " ".join(summary.warnings)


@run_async
async def test_empty_source_and_empty_idea_do_not_call_llm(tmp_path):
    paper = Paper(paper_id="p1", title="Missing text")
    model = RecordingLLM()
    missing = await summarize_paper("memory", paper, settings=settings_at(tmp_path), llm=model)
    empty = await summarize_paper(" ", paper, settings=settings_at(tmp_path), llm=model)
    assert missing.summary_scope == empty.summary_scope == "failed"
    assert missing.errors[0].code == "source_unavailable"
    assert empty.errors[0].code == "invalid_input"
    assert not model.requests


@run_async
async def test_demo_mode_does_not_implicitly_fake_llm(tmp_path):
    paper = full_paper(tmp_path)
    summary = await summarize_paper("memory", paper, settings=settings_at(tmp_path, mode="demo"))
    assert summary.summary_scope == "failed"
    assert summary.errors[0].code == "llm_configuration"


@run_async
async def test_explicit_synthetic_source_never_counts_as_fulltext(tmp_path):
    paper = full_paper(tmp_path)
    paper.metadata["synthetic"] = True
    summary = await summarize_paper("memory", paper, settings=settings_at(tmp_path), llm=RecordingLLM())
    assert summary.summary_scope == "demo_fixture"
    assert "演示样例" in " ".join(summary.warnings)


@run_async
async def test_bad_converter_mapping_cannot_authorize_page_number(tmp_path):
    paper = full_paper(tmp_path)
    paper.page_map[0].text = "This text is not in the saved markdown."
    summary = await summarize_paper("memory", paper, settings=settings_at(tmp_path), llm=RecordingLLM())
    assert summary.summary_scope == "partial_fulltext"
    assert summary.evidence[0].page_number is None
    assert "无效" in " ".join(summary.warnings)


@run_async
async def test_long_source_is_fully_covered_and_experiments_survive_merge(tmp_path):
    texts = [(f"# Section {index}\n" + f"Evidence on page {index}. " * 80) for index in range(1, 5)]
    paper = full_paper(tmp_path, texts=texts)
    model = RecordingLLM()
    settings = settings_at(tmp_path, chunk_chars=1000, max_chunks=30)
    summary = await summarize_paper("memory", paper, settings=settings, llm=model)
    chunks = [request for _, request in model.requests if request["task"] == "summarize_chunk"]
    assert "".join(segment["text"] for request in chunks for segment in request["segments"]) == "".join(texts)
    assert summary.summary_scope == "fulltext"
    assert len(chunks) > 1
    assert set(evidence.page_number for evidence in summary.evidence) == {1, 2, 3, 4}
    for request in chunks:
        quote = next(segment["text"].strip()[:80] for segment in request["segments"] if segment["text"].strip())
        assert "Experiment for " + quote in summary.experiments
        assert "Limitation for " + quote in summary.limitations


@run_async
async def test_max_chunks_is_explicit_partial_and_includes_end(tmp_path):
    texts = [(f"# Page {index}\n" + f"Evidence {index}. " * 100) for index in range(1, 8)]
    model = RecordingLLM()
    summary = await summarize_paper("memory", full_paper(tmp_path, texts=texts),
                                    settings=settings_at(tmp_path, chunk_chars=1000, max_chunks=2), llm=model)
    chunks = [request for _, request in model.requests if request["task"] == "summarize_chunk"]
    assert len(chunks) == 2
    assert chunks[-1]["segments"][-1]["page_number"] == 7
    assert summary.summary_scope == "partial_fulltext"
    assert "未读分块" in " ".join(summary.warnings)
    assert any(error.code == "partial_summary" for error in summary.errors)


@run_async
async def test_merge_input_budget_does_not_drop_verified_notes(tmp_path):
    paper = full_paper(tmp_path, texts=["Evidence for every paragraph. " * 150])
    model = RecordingLLM()
    settings = settings_at(tmp_path, chunk_chars=1000, max_chunks=20, llm_input_token_budget=3500)
    summary = await summarize_paper("memory", paper, settings=settings, llm=model)
    assert summary.summary_scope == "fulltext"
    assert not summary.overview and not summary.deep_analysis
    error = next(error for error in summary.errors if error.code == "llm_budget")
    assert error.details["stage"] == "merge" and error.details["reason"] == "input_budget"
    assert "未完成整篇综合" in " ".join(summary.warnings)
    assert all(request["task"] == "summarize_chunk" for _, request in model.requests)
    expected = [content_for(request) for _, request in model.requests]
    assert set(summary.experiments) == {value for note in expected for value in note["experiments"]}
    assert set(summary.limitations) == {value for note in expected for value in note["limitations"]}
    assert {evidence.quote for evidence in summary.evidence} == {
        evidence["quote"] for note in expected for evidence in note["evidence"]
    }
    for system, request in model.requests:
        size = len((system + json.dumps(request, ensure_ascii=False, separators=(",", ":"))).encode("utf-8")) + 64
        assert size <= settings.llm_input_token_budget


@run_async
async def test_budget_failure_after_first_chunk_is_partial(tmp_path):
    class BudgetLLM(RecordingLLM):
        async def json(self, system, user):
            if self.requests:
                raise LLMBudgetError("Simulated request budget exhaustion")
            return await super().json(system, user)
    summary = await summarize_paper("memory", full_paper(tmp_path, texts=["Evidence. " * 400]),
                                    settings=settings_at(tmp_path, chunk_chars=1000, max_chunks=10), llm=BudgetLLM())
    assert summary.summary_scope == "partial_fulltext"
    assert {error.code for error in summary.errors} >= {"llm_budget", "partial_summary"}


@run_async
async def test_error_messages_do_not_leak_model_exceptions(tmp_path):
    class BadLLM:
        async def json(self, system, user):
            raise RuntimeError("Authorization: Bearer secret-do-not-print")
    result = await summarize_paper("memory", full_paper(tmp_path), settings=settings_at(tmp_path), llm=BadLLM())
    assert result.summary_scope == "failed"
    assert "secret-do-not-print" not in result.model_dump_json()
    assert result.errors[0].code == "llm_failed"


def usable_summaries(count=3, scope="fulltext"):
    return [PaperSummary(paper_id=f"p{index}", title=f"Paper {index}", summary_scope=scope,
                         overview="A source-bounded overview.",
                         deep_analysis="A paper-level synthesis of the supplied evidence.",
                         research_question="A question", core_idea="An idea", findings=["A bounded finding."])
            for index in range(1, count + 1)]


@run_async
async def test_report_uses_only_actual_ids_and_persists(tmp_path):
    report = await generate_report("memory", usable_summaries(), settings=settings_at(tmp_path), llm=RecordingLLM())
    assert not report.errors
    assert {reference.paper_id for reference in report.references} == {"p1", "p2", "p3"}
    assert Path(report.report_path).with_suffix(".json").exists()
    assert "待验证的研究方向" in report.markdown


@pytest.mark.parametrize("field", ["references", "comparison", "inline", "title"])
@run_async
async def test_report_rejects_imaginary_citations(tmp_path, field):
    def mutate(request, response):
        if field == "references":
            response["references"].append({"paper_id": "ghost", "title": "Invented"})
        elif field == "comparison":
            response["comparison"][0]["paper_ids"].append("ghost")
        elif field == "inline":
            response["overview"] = "An imaginary result [ghost]."
        else:
            response["references"][0]["title"] = "Invented title"
        return response
    report = await generate_report("memory", usable_summaries(), settings=settings_at(tmp_path), llm=RecordingLLM(mutate))
    assert any(error.code == "invalid_model_output" for error in report.errors)
    assert not report.overview
    assert "ghost" not in {reference.paper_id for reference in report.references}


@run_async
async def test_report_with_fewer_than_three_or_abstracts_is_partial(tmp_path):
    for summaries in (usable_summaries(2), usable_summaries(3, scope="abstract_only")):
        report = await generate_report("memory", summaries, settings=settings_at(tmp_path), llm=RecordingLLM())
        assert any(error.code == "partial_report" for error in report.errors)
        assert "部分报告" in report.limitations[0]
        assert report.overview


@run_async
async def test_report_compacts_prompt_without_modifying_source_summaries(tmp_path):
    summaries = usable_summaries()
    summaries[0].experiments = ["Large experiment evidence. " * 2000]
    original = [summary.model_dump() for summary in summaries]
    model = RecordingLLM()
    report = await generate_report("memory", summaries, settings=settings_at(
        tmp_path, llm_input_token_budget=4000, report_input_token_budget=4000), llm=model)
    assert {error.code for error in report.errors} == {"partial_report"}
    error = report.errors[0]
    assert error.details["reason"] == "report_input_compacted"
    assert error.details["compact_level"] in (1, 2, 3)
    assert "有界摘录" in report.limitations[0]
    assert [summary.model_dump() for summary in summaries] == original
    assert len(model.requests) == 1
    system, request = model.requests[0]
    assert len((system + json.dumps(request, ensure_ascii=False, separators=(",", ":"))).encode("utf-8")) + 64 <= 4000
    assert request["allowed_paper_ids"] == ["p1", "p2", "p3"]
    excerpt = request["summaries"][0]["experiments"][0]
    assert summaries[0].experiments[0].startswith(excerpt)
    assert len(excerpt) < len(summaries[0].experiments[0])
    assert "experiments" in request["summaries"][0]["compacted_fields"]
    assert {reference.paper_id for reference in report.references} == {"p1", "p2", "p3"}


@run_async
async def test_report_unfit_even_after_compaction_rejects_without_llm(tmp_path):
    summaries = usable_summaries()
    original = [summary.model_dump() for summary in summaries]
    model = RecordingLLM()
    report = await generate_report("memory", summaries, settings=settings_at(
        tmp_path, report_input_token_budget=1000), llm=model)
    assert {error.code for error in report.errors} == {"llm_budget"}
    assert report.errors[0].details["compacted"] is True
    assert not report.overview
    assert not model.requests
    assert len(report.references) == 3
    assert [summary.model_dump() for summary in summaries] == original


@pytest.mark.parametrize("incomplete", ["overview", "deep_analysis", "error"])
@run_async
async def test_report_preserves_incomplete_paper_synthesis_status(tmp_path, incomplete):
    summaries = usable_summaries()
    if incomplete == "error":
        summaries[0].errors.append(ErrorInfo(
            code="llm_budget", stage="summarize", message="Fixture merge budget",
        ))
    else:
        setattr(summaries[0], incomplete, "")
    report = await generate_report("memory", summaries, settings=settings_at(tmp_path), llm=RecordingLLM())
    assert {error.code for error in report.errors} == {"partial_report"}
    assert report.errors[0].details["incomplete_summary_ids"] == ["p1"]
    assert report.errors[0].details["summary_error_codes"]["p1"] == [
        "llm_budget" if incomplete == "error" else "missing_paper_level_synthesis"
    ]


@run_async
async def test_report_budget_can_be_raised_independently(tmp_path):
    summaries = usable_summaries()
    summaries[0].experiments = ["Large experiment evidence. " * 2000]
    model = RecordingLLM()
    report = await generate_report("memory", summaries, settings=settings_at(
        tmp_path, llm_input_token_budget=4000, report_input_token_budget=200000), llm=model)
    assert not any(error.code == "llm_budget" for error in report.errors)
    assert report.overview
    assert model.requests


@run_async
async def test_failed_summaries_are_not_report_evidence(tmp_path):
    model = RecordingLLM()
    report = await generate_report("memory", [PaperSummary(paper_id="bad", summary_scope="failed")], settings=settings_at(tmp_path), llm=model)
    assert not model.requests
    assert report.errors[0].code == "no_summaries"
    assert not report.references


@run_async
async def test_paper_id_cannot_escape_summary_directory(tmp_path):
    paper = full_paper(tmp_path, paper_id="../../outside:bad?*")
    summary = await summarize_paper("memory", paper, settings=settings_at(tmp_path), llm=RecordingLLM())
    assert Path(summary.summary_path).parent.resolve() == (tmp_path / "data" / "summaries").resolve()
    assert ".." not in Path(summary.summary_path).name


@run_async
async def test_shared_demo_llm_contract_remains_explicitly_synthetic(tmp_path):
    from paper_agent.demo_fixtures import DemoLLM

    llm = DemoLLM()
    settings = settings_at(tmp_path)
    summaries = []
    for number in range(3):
        paper = full_paper(tmp_path, paper_id=f"demo:{number}")
        paper.metadata["synthetic"] = True
        summaries.append(await summarize_paper("memory", paper, settings=settings, llm=llm))
    assert all(summary.summary_scope == "demo_fixture" and not summary.errors for summary in summaries)
    report = await generate_report("memory", summaries, settings=settings, llm=llm)
    assert report.overview.startswith("DEMO ONLY")
    assert {error.code for error in report.errors} == {"partial_report"}
    assert llm.usage.requests == 0
