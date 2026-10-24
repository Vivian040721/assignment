"""Opt-in public-PDF acceptance; no LLM request and no performance claim.

Set PAPER_AGENT_ONLINE_TESTS=1 and, when needed, PAPER_AGENT_HTTP_PROXY.
Artifacts stay in ignored data/validation and data/pdfs|markdown directories.
"""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from paper_agent.config import Settings
from paper_agent.models import Paper
from paper_agent.services.downloader import download_pdf
from paper_agent.services.pdf_converter import convert_pdf

pytestmark = [
    pytest.mark.online,
    pytest.mark.skipif(os.getenv("PAPER_AGENT_ONLINE_TESTS") != "1",
                       reason="Public-network acceptance is opt-in"),
]


@pytest.mark.parametrize("paper", [
    Paper(paper_id="openalex:W4407759090", title="A-Mem: Agentic Memory for LLM Agents",
          arxiv_id="2502.12110", sources=["arxiv", "openalex"],
          paper_url="https://arxiv.org/abs/2502.12110",
          pdf_url="https://arxiv.org/pdf/2502.12110"),
    Paper(paper_id="arxiv:2303.11366", title="Reflexion: Language Agents with Verbal Reinforcement Learning",
          arxiv_id="2303.11366", sources=["arxiv"],
          paper_url="https://arxiv.org/abs/2303.11366",
          pdf_url="https://arxiv.org/pdf/2303.11366"),
    Paper(paper_id="arxiv:2304.03442", title="Generative Agents: Interactive Simulacra of Human Behavior",
          arxiv_id="2304.03442", sources=["arxiv"],
          paper_url="https://arxiv.org/abs/2304.03442",
          pdf_url="https://arxiv.org/pdf/2304.03442"),
], ids=["a-mem", "reflexion", "generative-agents"])
def test_public_pdf_download_conversion_and_page_mapping(paper):
    async def validate():
        settings = replace(Settings.from_env(), mode="live", http_max_retries=1,
                           download_timeout=30)
        acquired = await download_pdf(paper, settings=settings)
        assert acquired.status == "downloaded", acquired.model_dump_json()
        converted = await convert_pdf(acquired, settings=settings)
        target = Path(settings.data_dir) / "validation" / "public_documents"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{paper.arxiv_id}.json").write_text(
            converted.model_dump_json(indent=2), encoding="utf-8"
        )
        assert converted.status in {"converted", "partial_fulltext"}, converted.error
        assert converted.pdf_path and Path(converted.pdf_path).stat().st_size > 1000
        assert converted.markdown_path and converted.page_map
        text = Path(converted.markdown_path).read_text(encoding="utf-8")
        assert len(text.strip()) > 1000
        assert len(converted.page_map) == converted.fulltext_quality["extracted_pages"]
        for page in converted.page_map:
            assert text[page.start_char:page.end_char] == page.text
        # A conversion heuristic is deliberately not called semantic completeness.
        print(json.dumps({"paper_id": paper.paper_id, "status": converted.status,
                          "pages": converted.fulltext_quality.get("page_count"),
                          "text_pages": converted.fulltext_quality.get("text_page_count"),
                          "complete_heuristic": converted.fulltext_quality.get("complete")},
                         ensure_ascii=False))
    asyncio.run(validate())
