"""Small real API smoke check. Does not call a model or download PDFs."""

import asyncio
import json
import sys

import httpx

from paper_agent.config import Settings
from paper_agent.sources.arxiv import ArxivSource
from paper_agent.sources.openalex import OpenAlexSource


async def main():
    settings = Settings.from_env()
    query = " ".join(sys.argv[1:]) or "agent memory"
    records = {}
    async with httpx.AsyncClient(
        timeout=settings.http_timeout, proxy=settings.http_proxy or None
    ) as client:
        for source in [ArxivSource(client), OpenAlexSource(client, settings.openalex_api_key)]:
            try:
                papers = await source.search(query, 2)
                records[source.name] = {
                    "count": len(papers),
                    "papers": [p.model_dump() for p in papers],
                }
            except Exception as error:
                records[source.name] = {"count": 0, "error": f"{type(error).__name__}: {error}"}
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0 if all(source["count"] > 0 for source in records.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
