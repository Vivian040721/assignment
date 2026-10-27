"""Explicit offline fixtures; these are never scientific or performance evidence.

The normal selector and research-agent modules still execute. Only their source,
document and model dependencies are injected here for an honest offline demo.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pymupdf

from paper_agent.config import Settings
from paper_agent.llm import LLMUsage
from paper_agent.models import PageMapping, Paper
from paper_agent.services.pdf_runtime import PDF_PARSE_LOCK


def demo_papers(source: str, limit: int = 20) -> list[Paper]:
    if source not in {"arxiv", "openalex"}:
        raise ValueError("Unknown fixture source")
    numbers = range(1, 13) if source == "arxiv" else range(7, 19)
    return [Paper(
        paper_id=f"demo:{number:03d}",
        title=f"DEMO {number:02d}: Agent Memory and Evidence Tracking",
        authors=[f"Synthetic Author {number}"], year=2026,
        abstract=("Synthetic development fixture about agent memory, experience reuse, "
                  "tool reliability and token costs. This is not a real publication."),
        doi=f"10.9999/demo.paper{number:03d}", sources=[source],
        source_records=[{"source": source, "id": f"synthetic-{number:03d}", "synthetic": True}],
        paper_url=f"https://example.invalid/demo/{number:03d}",
        metadata={"synthetic": True},
    ) for number in list(numbers)[:limit]]


async def prepare_demo_paper(paper: Paper, *, settings: Settings | None = None) -> Paper:
    settings = settings or Settings.from_env()
    result = paper.model_copy(deep=True)
    slug = hashlib.sha256(result.paper_id.encode()).hexdigest()[:16]
    pdf_dir, md_dir = Path(settings.pdf_dir), Path(settings.markdown_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    pages = [
        (f"# {result.title}\n\n## Fixture notice\n"
         "This document is a synthetic development fixture, not a real scientific paper.\n\n"
         "## Research question\nHow can identifiers preserve evidence across an agent workflow?\n\n"
         "## Method\nMaintain stable paper identifiers, source provenance and page mappings.\n"),
        ("## Evidence\nNo real experiment was conducted and no empirical accuracy is reported.\n\n"
         "## Limitations\nSynthetic data cannot validate scholarly relevance, model quality or speedup.\n\n"
         "## Relation to the idea\nThis example only verifies interfaces and artifact persistence.\n"),
    ]
    pdf_path = pdf_dir / f"{slug}.pdf"
    with PDF_PARSE_LOCK:
        with pymupdf.open() as document:
            for text in pages:
                page = document.new_page()
                page.insert_text((50, 50), text, fontsize=10)
            document.save(str(pdf_path))
    markdown = ""
    mapping = []
    for number, text in enumerate(pages, 1):
        page_text = f"<!-- page:{number} -->\n{text}\n"
        start = len(markdown)
        markdown += page_text
        mapping.append(PageMapping(page_number=number, text=page_text,
                                   start_char=start, end_char=len(markdown)))
    markdown_path = md_dir / f"{slug}.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    result.pdf_path, result.markdown_path = str(pdf_path.resolve()), str(markdown_path.resolve())
    result.page_map = mapping
    result.status = "converted"
    result.metadata["synthetic"] = True
    result.fulltext_quality = {"complete": True, "synthetic": True, "page_count": 2,
                               "text_page_count": 2, "nonempty_ratio": 1.0}
    return result


class DemoLLM:
    """Deterministic dependency injection, not a real model or an API simulation claim."""

    synthetic = True

    def __init__(self):
        self.usage = LLMUsage()
        self.call_log: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        raise RuntimeError("Demo controller is scripted; DemoLLM does not fake autonomous tool calls")

    async def text(self, system: str, user: str) -> str:
        return json.dumps(await self.json(system, user), ensure_ascii=False)

    async def json(self, system: str, user: str) -> dict:
        payload = json.loads(user)
        task = payload.get("task", "selector" if "candidates" in payload else "query")
        self.usage.logical_calls += 1
        self.call_log.append({"task": task, "synthetic": True, "physical_api_requests": 0})
        if "candidates" in payload:
            selected = payload["candidates"][:payload["requested_count"]]
            return {"papers": [{"paper_id": paper["paper_id"], "rank": index,
                                "final_score": round(1 - index * .025, 4),
                                "selection_reason": "DEMO ONLY: injected fixture response; no real LLM."}
                               for index, paper in enumerate(selected, 1)]}
        if task == "summarize_chunk":
            segments = [segment for segment in payload["segments"] if segment.get("text", "").strip()]
            segment = segments[0]
            evidence = [{"paper_id": payload["paper"]["paper_id"],
                         "page_number": segment.get("page_number"),
                         "section": segment.get("section"), "quote": segment["text"][:140],
                         "claim": "This is fixture text, not an empirical finding.",
                         "evidence_type": "author_claim",
                         "source_segment_id": segment.get("segment_id")}]
            return {
                "research_question": "DEMO ONLY: verify the local research workflow.",
                "core_idea": "Keep stable identifiers and traceable fixture evidence.",
                "methods": ["Synthetic dependency injection; not a scientific method result."],
                "experiments": ["No real experiment was conducted."],
                "findings": ["No empirical finding is asserted."],
                "limitations": ["Fixtures cannot validate real paper relevance or LLM quality."],
                "author_claims": ["The supplied document labels itself as a synthetic fixture."],
                "system_inferences": [], "speculative_ideas": [], "evidence": evidence,
            }
        if task == "merge_paper_summary":
            evidence_ids = list(dict.fromkeys(
                evidence_id for summary in payload["chunk_summaries"]
                for evidence_id in summary.get("evidence_ids", [])
            ))
            return self._summary([
                {"evidence_id": evidence_id} for evidence_id in evidence_ids[:3]
            ])
        if task == "generate_research_report":
            summaries = payload["summaries"]
            ids = [summary["paper_id"] for summary in summaries]
            return {
                "overview": "DEMO ONLY: synthetic papers and injected model responses.",
                "related_work": ["Fixture workflow integration; not a literature review."],
                "comparison": [{"paper_ids": ids, "scope": "synthetic fixtures only"}],
                "researched_questions": ["Are interfaces and page evidence preserved?"],
                "limitations": ["No real model, empirical results or performance claim."],
                "research_directions": ["Configure a real model and independently verify evidence."],
                "references": [{"paper_id": summary["paper_id"], "title": summary["title"]}
                               for summary in summaries],
            }
        return {"queries": ["agent memory experience reuse token cost"]}

    @staticmethod
    def _summary(evidence: list[dict]) -> dict:
        return {"overview": "DEMO ONLY: synthetic fixture overview; no real paper was analyzed.",
                "deep_analysis": "## 演示说明\n\n这是离线合成样例，只验证接口与证据保存，不代表真实论文结论。",
                "research_question": "DEMO ONLY: verify the local research workflow.",
                "core_idea": "Keep stable identifiers and traceable fixture evidence.",
                "methods": ["Synthetic dependency injection; not a scientific method result."],
                "experiments": ["No real experiment was conducted."],
                "findings": ["No empirical finding is asserted."],
                "limitations": ["Fixtures cannot validate real paper relevance or LLM quality."],
                "relevance": "Software integration demonstration only.",
                "author_claims": ["The supplied document labels itself a synthetic fixture."],
                "system_inferences": [], "speculative_ideas": [], "evidence": evidence}
