"""Owner 03: minimal arXiv API adapter; improve query planning and pagination here."""

import asyncio
import re
import time
import xml.etree.ElementTree as ET

import httpx

from paper_agent.schemas import PaperRecord
from paper_agent.sources.http import get_with_retry

NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def parse_feed(xml: str) -> list[PaperRecord]:
    papers = []
    for entry in ET.fromstring(xml).findall("a:entry", NS):
        landing = entry.findtext("a:id", "", NS)
        if "/abs/" not in landing:
            continue
        arxiv_id = landing.split("/abs/", 1)[-1]
        published = entry.findtext("a:published", "", NS)
        pdf = next(
            (
                link.get("href")
                for link in entry.findall("a:link", NS)
                if link.get("title") == "pdf"
            ),
            None,
        )
        papers.append(
            PaperRecord(
                paper_id="arxiv:" + arxiv_id,
                title=" ".join(entry.findtext("a:title", "", NS).split()),
                authors=[
                    author.findtext("a:name", "", NS) for author in entry.findall("a:author", NS)
                ],
                year=int(published[:4]) if published[:4].isdigit() else None,
                abstract=" ".join(entry.findtext("a:summary", "", NS).split()),
                doi=entry.findtext("x:doi", None, NS),
                arxiv_id=arxiv_id,
                sources=["arxiv"],
                source_records=[{"source": "arxiv", "id": arxiv_id, "url": landing}],
                landing_url=landing,
                oa_pdf_url=pdf,
            )
        )
    return papers


class ArxivSource:
    name = "arxiv"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def search(self, query: str, limit: int) -> list[PaperRecord]:
        # One connection; requests spaced at least 3 seconds within this server.
        # Team members must also avoid concurrent separate arXiv server processes.
        async with self._lock:
            await asyncio.sleep(max(0, 3.1 - (time.monotonic() - self._last_request)))
            if not re.search(r"\b(all|ti|au|abs|cat):", query):
                query = " AND ".join("all:" + token for token in re.findall(r"\w+", query))
            self._last_request = time.monotonic()
            response = await get_with_retry(
                self.client,
                "https://export.arxiv.org/api/query",
                params={
                    "search_query": query,
                    "max_results": limit,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
            )
            return parse_feed(response.text)[:limit]
