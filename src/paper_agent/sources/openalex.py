"""Owner 04: OpenAlex work normalization and public PDF location extraction."""

import httpx

from paper_agent.schemas import PaperRecord
from paper_agent.sources.http import get_with_retry


def restore_abstract(index: dict | None) -> str:
    positions = {}
    for word, offsets in (index or {}).items():
        for offset in offsets:
            positions[offset] = word
    return " ".join(positions[key] for key in sorted(positions))


def parse_work(work: dict) -> PaperRecord:
    locations = [work.get("best_oa_location"), *(work.get("locations") or [])]
    pdf = next(
        (
            location.get("pdf_url")
            for location in locations
            if location and location.get("is_oa") and location.get("pdf_url")
        ),
        None,
    )
    primary = work.get("primary_location") or {}
    work_id = work["id"]
    return PaperRecord(
        paper_id="openalex:" + work_id.rsplit("/", 1)[-1],
        title=work.get("display_name") or work.get("title") or "Untitled",
        authors=[
            item["author"].get("display_name", "")
            for item in work.get("authorships", [])
            if item.get("author")
        ],
        year=work.get("publication_year"),
        abstract=restore_abstract(work.get("abstract_inverted_index")),
        doi=work.get("doi"),
        sources=["openalex"],
        source_records=[{"source": "openalex", "id": work_id, "url": work_id}],
        landing_url=primary.get("landing_page_url") or work.get("doi") or work_id,
        oa_pdf_url=pdf,
    )


class OpenAlexSource:
    name = "openalex"

    def __init__(self, client: httpx.AsyncClient, api_key: str = ""):
        self.client, self.api_key = client, api_key

    async def search(self, query: str, limit: int) -> list[PaperRecord]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = await get_with_retry(
            self.client,
            "https://api.openalex.org/works",
            params={"search": query, "per_page": min(limit, 100)},
            headers=headers,
        )
        return [parse_work(work) for work in response.json().get("results", [])][:limit]
