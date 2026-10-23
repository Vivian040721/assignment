"""Synthetic records only. These are not real search results or real publications."""

import json
from importlib.resources import files

from paper_agent.schemas import PaperRecord


class DemoSource:
    def __init__(self, name: str):
        self.name = name

    async def search(self, query: str, limit: int) -> list[PaperRecord]:
        data = json.loads(
            files("paper_agent").joinpath("fixtures/papers.json").read_text(encoding="utf-8")
        )
        return [PaperRecord.model_validate(item) for item in data if self.name in item["sources"]][
            :limit
        ]
