"""Owner 07: public PDF download and optional PyMuPDF4LLM conversion."""

import asyncio
import hashlib
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

import httpx

from paper_agent.config import Settings
from paper_agent.schemas import PaperArtifact, PaperRecord


def paper_slug(paper_id: str) -> str:
    return hashlib.sha256(paper_id.encode()).hexdigest()[:16]


class FulltextProcessor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.download_lock = asyncio.Lock()

    async def prepare(self, paper: PaperRecord, directory: Path) -> PaperArtifact:
        slug = paper_slug(paper.paper_id)
        md_path = directory / f"{slug}.md"
        if self.settings.mode == "demo":
            text = files("paper_agent").joinpath("fixtures/fulltext.md").read_text(encoding="utf-8")
            md_path.write_text(f"# {paper.title}\n\n{text}", encoding="utf-8")
            return PaperArtifact(
                paper_id=paper.paper_id,
                markdown_path=str(md_path),
                fulltext_status="demo_fixture",
                evidence_coverage="demo_fixture_only",
            )
        if not paper.oa_pdf_url:
            raise ValueError("No public PDF URL supplied by paper sources")
        if urlparse(paper.oa_pdf_url).scheme not in {"http", "https"}:
            raise ValueError("Public PDF URL must use HTTP(S)")
        try:
            import pymupdf4llm
        except ImportError:
            raise RuntimeError(
                "Install fulltext dependencies: pip install -e .[fulltext]"
            ) from None
        pdf_path = directory / f"{slug}.pdf"
        # Serialize remote downloads; concurrency is for independent conversion/model work.
        async with self.download_lock:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout,
                follow_redirects=True,
                proxy=self.settings.http_proxy or None,
            ) as client:
                async with client.stream("GET", paper.oa_pdf_url) as response:
                    if response.is_error:
                        raise RuntimeError(f"PDF download returned HTTP {response.status_code}")
                    size = 0
                    with pdf_path.open("wb") as stream:
                        async for block in response.aiter_bytes():
                            size += len(block)
                            if size > 40 * 1024 * 1024:
                                raise ValueError("PDF exceeds 40 MiB scaffold limit")
                            stream.write(block)
            # Conservative delay between remote fulltext downloads.
            await asyncio.sleep(3.1)
        with pdf_path.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                raise ValueError("Downloaded content is not a PDF")
        markdown = await asyncio.to_thread(pymupdf4llm.to_markdown, str(pdf_path))
        if len(markdown.strip()) < 100:
            raise ValueError("PDF conversion produced too little text")
        md_path.write_text(markdown, encoding="utf-8")
        return PaperArtifact(
            paper_id=paper.paper_id,
            pdf_path=str(pdf_path),
            markdown_path=str(md_path),
            fulltext_status="ready",
            evidence_coverage="converted_not_yet_summarized",
        )
