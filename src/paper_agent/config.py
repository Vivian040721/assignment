"""Shared settings. Owners 01/02 coordinate changes."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    mode: Literal["demo", "live"] = "demo"
    output_dir: Path = Path("outputs")
    processing: Literal["serial", "concurrent"] = "serial"
    concurrency: int = 3
    source_limit: int = 20
    candidate_limit: int = 15
    ranker: str = "keyword"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 120
    llm_max_steps: int = 8
    chunk_chars: int = 12000
    max_chunks: int = 12
    openalex_api_key: str = ""
    http_timeout: float = 30
    http_proxy: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False)
        settings = cls(
            mode=os.getenv("PAPER_AGENT_MODE", "demo"),
            output_dir=Path(os.getenv("PAPER_AGENT_OUTPUT_DIR", "outputs")).resolve(),
            processing=os.getenv("PAPER_AGENT_PROCESSING", "serial"),
            concurrency=int(os.getenv("PAPER_AGENT_CONCURRENCY", "3")),
            source_limit=int(os.getenv("PAPER_AGENT_SOURCE_LIMIT", "20")),
            candidate_limit=int(os.getenv("PAPER_AGENT_CANDIDATE_LIMIT", "15")),
            ranker=os.getenv("PAPER_AGENT_RANKER", "keyword"),
            llm_base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", ""),
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            llm_max_steps=int(os.getenv("LLM_MAX_STEPS", "8")),
            chunk_chars=int(os.getenv("LLM_CHUNK_CHARS", "12000")),
            max_chunks=int(os.getenv("LLM_MAX_CHUNKS", "12")),
            openalex_api_key=os.getenv("OPENALEX_API_KEY", ""),
            http_timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            http_proxy=os.getenv("PAPER_AGENT_HTTP_PROXY", ""),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.mode not in {"demo", "live"}:
            raise ValueError("PAPER_AGENT_MODE must be demo or live")
        if self.processing not in {"serial", "concurrent"}:
            raise ValueError("PAPER_AGENT_PROCESSING must be serial or concurrent")
        if not 1 <= self.concurrency <= 8:
            raise ValueError("Concurrency must be between 1 and 8")
        if not 1 <= self.source_limit <= 100 or not 1 <= self.candidate_limit <= 100:
            raise ValueError("Source/candidate limits must be between 1 and 100")
        if self.ranker not in {"keyword", "semantic"}:
            raise ValueError("Ranker must be keyword or semantic")
        if self.chunk_chars < 1000 or self.max_chunks < 1:
            raise ValueError("Invalid fulltext chunk limits")
        if min(self.http_timeout, self.llm_timeout, self.llm_max_steps) <= 0:
            raise ValueError("Timeouts and model step limit must be positive")

    def require_llm(self) -> None:
        if not all((self.llm_base_url, self.llm_api_key, self.llm_model)):
            raise ValueError("Live mode requires LLM_BASE_URL, LLM_API_KEY and LLM_MODEL")
