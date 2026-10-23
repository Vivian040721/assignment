"""Real MCP stdio server. stdout is reserved for protocol messages."""

import logging
import sys

from mcp.server.fastmcp import FastMCP

from paper_agent.config import Settings
from paper_agent.service import ResearchService

mcp = FastMCP("group-paper-research", log_level="WARNING")
_service: ResearchService | None = None


def service() -> ResearchService:
    global _service
    if _service is None:
        _service = ResearchService(Settings.from_env())
    return _service


@mcp.tool()
async def search_and_select(idea: str, k: int = 3, query: str = "") -> dict:
    """Search two sources, deduplicate/rank, then invoke server task agent for Top-k.

    idea is the original research idea; query can be concise English search keywords.
    Returns run_id to use in the following tools. Demo mode uses labeled fixtures.
    """
    return await service().search_and_select(idea, k, query)


@mcp.tool()
async def prepare_fulltexts(run_id: str) -> dict:
    """Download public fulltexts and convert to Markdown for a selected run.

    Keep unavailable fulltexts explicitly failed. Never substitute abstracts as fulltext.
    """
    return await service().prepare_fulltexts(run_id)


@mcp.tool()
async def generate_report(run_id: str) -> dict:
    """Invoke server task agent on converted text for individual and combined reports.

    Must follow prepare_fulltexts. Returns artifact paths, coverage, errors and timings.
    """
    return await service().generate_report(run_id)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
