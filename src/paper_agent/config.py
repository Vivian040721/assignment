"""Shared settings. Owners 01/02 coordinate changes."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    mode: Literal["demo", "live"] = "demo"
    output_dir: Path = Path("data/runs")
    processing: Literal["serial", "concurrent"] = "serial"
    concurrency: int = 3
    source_limit: int = 20
    candidate_limit: int = 15
    merged_candidate_limit: int = 0
    selector_input_token_budget: int = 0
    report_input_token_budget: int = 64000
    max_fulltext_attempts: int = 0
    ranker: str = "keyword"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = "deepseek-flash"
    llm_timeout: float = 120
    llm_max_steps: int = 8
    chunk_chars: int = 12000
    max_chunks: int = 16
    openalex_api_key: str = field(default="", repr=False)
    http_timeout: float = 30
    http_proxy: str = ""
    top_k: int = 5
    data_dir: Path = Path("data")
    pdf_dir: Path | None = None
    markdown_dir: Path | None = None
    summaries_dir: Path | None = None
    reports_dir: Path | None = None
    search_concurrency: int = 2
    download_concurrency: int = 3
    conversion_concurrency: int = 2
    llm_concurrency: int = 2
    cache_enabled: bool = True
    cache_mode: Literal["off", "read_only", "read_write"] = "read_write"
    llm_max_retries: int = 2
    llm_retry_backoff: float = 0.5
    llm_max_backoff: float = 8.0
    llm_max_calls: int = 100
    llm_max_total_tokens: int = 200000
    llm_max_output_tokens: int = 4000
    llm_input_token_budget: int = 24000
    selector_fallback: Literal["error", "initial_ranking"] = "error"
    http_max_retries: int = 2
    http_retry_backoff: float = 0.5
    arxiv_request_interval: float = 3.0
    max_pdf_bytes: int = 50 * 1024 * 1024
    max_redirects: int = 5
    download_timeout: float = 180.0
    download_candidate_timeout: float = 45.0
    llm_thinking_enabled: bool = False

    def __post_init__(self) -> None:
        # No directory creation on import/configuration; modules create paths on use.
        for name, suffix in (
            ("pdf_dir", "pdfs"),
            ("markdown_dir", "markdown"),
            ("summaries_dir", "summaries"),
            ("reports_dir", "reports"),
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, Path(value) if value else self.data_dir / suffix)

    @property
    def execution_mode(self) -> Literal["sequential", "concurrent"]:
        return "concurrent" if self.processing == "concurrent" else "sequential"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False)
        settings = cls(
            mode=os.getenv("PAPER_AGENT_MODE", "demo"),
            output_dir=Path(os.getenv("PAPER_AGENT_OUTPUT_DIR", "data/runs")).resolve(),
            processing=os.getenv("PAPER_AGENT_PROCESSING", "serial"),
            concurrency=int(os.getenv("PAPER_AGENT_CONCURRENCY", "3")),
            source_limit=int(os.getenv("PAPER_AGENT_SOURCE_LIMIT", "20")),
            candidate_limit=int(os.getenv("PAPER_AGENT_CANDIDATE_LIMIT", "15")),
            merged_candidate_limit=int(os.getenv("PAPER_AGENT_MERGED_CANDIDATE_LIMIT", "0")),
            selector_input_token_budget=int(os.getenv("PAPER_AGENT_SELECTOR_INPUT_TOKEN_BUDGET", "0")),
            report_input_token_budget=int(os.getenv("PAPER_AGENT_REPORT_INPUT_TOKEN_BUDGET", "64000")),
            max_fulltext_attempts=int(os.getenv("PAPER_AGENT_MAX_FULLTEXT_ATTEMPTS", "0")),
            ranker=os.getenv("PAPER_AGENT_RANKER", "keyword"),
            llm_base_url=_preferred_env("LLM_BASE_URL", "DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            llm_api_key=_preferred_env("LLM_API_KEY", "DEEPSEEK_API_KEY", ""),
            llm_model=_preferred_env("LLM_MODEL", "DEEPSEEK_MODEL", "deepseek-flash"),
            llm_thinking_enabled=_env_bool("LLM_THINKING_ENABLED", False),
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            llm_max_steps=int(os.getenv("LLM_MAX_STEPS", "8")),
            chunk_chars=int(os.getenv("LLM_CHUNK_CHARS", "12000")),
            max_chunks=int(os.getenv("LLM_MAX_CHUNKS", "16")),
            openalex_api_key=os.getenv("OPENALEX_API_KEY", ""),
            http_timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            http_proxy=os.getenv("PAPER_AGENT_HTTP_PROXY", ""),
            top_k=int(os.getenv("PAPER_AGENT_TOP_K", "5")),
            data_dir=Path(os.getenv("PAPER_AGENT_DATA_DIR", "data")).resolve(),
            pdf_dir=_optional_path("PAPER_AGENT_PDF_DIR"),
            markdown_dir=_optional_path("PAPER_AGENT_MARKDOWN_DIR"),
            summaries_dir=_optional_path("PAPER_AGENT_SUMMARIES_DIR"),
            reports_dir=_optional_path("PAPER_AGENT_REPORTS_DIR"),
            search_concurrency=int(os.getenv("PAPER_AGENT_SEARCH_CONCURRENCY", "2")),
            download_concurrency=int(os.getenv("PAPER_AGENT_DOWNLOAD_CONCURRENCY", "3")),
            conversion_concurrency=int(os.getenv("PAPER_AGENT_CONVERSION_CONCURRENCY", "2")),
            llm_concurrency=int(os.getenv("PAPER_AGENT_LLM_CONCURRENCY", "2")),
            cache_enabled=_env_bool("PAPER_AGENT_CACHE_ENABLED", True),
            cache_mode=os.getenv("PAPER_AGENT_CACHE_MODE", "read_write"),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
            llm_retry_backoff=float(os.getenv("LLM_RETRY_BACKOFF", "0.5")),
            llm_max_backoff=float(os.getenv("LLM_MAX_BACKOFF", "8")),
            llm_max_calls=int(os.getenv("LLM_MAX_CALLS", "100")),
            llm_max_total_tokens=int(os.getenv("LLM_MAX_TOTAL_TOKENS", "200000")),
            llm_max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4000")),
            llm_input_token_budget=int(os.getenv("LLM_INPUT_TOKEN_BUDGET", "24000")),
            selector_fallback=os.getenv("PAPER_AGENT_SELECTOR_FALLBACK", "error"),
            http_max_retries=int(os.getenv("HTTP_MAX_RETRIES", "2")),
            http_retry_backoff=float(os.getenv("HTTP_RETRY_BACKOFF", "0.5")),
            arxiv_request_interval=float(os.getenv("ARXIV_REQUEST_INTERVAL", "3")),
            max_pdf_bytes=int(os.getenv("PAPER_AGENT_MAX_PDF_BYTES", str(50 * 1024 * 1024))),
            max_redirects=int(os.getenv("PAPER_AGENT_MAX_REDIRECTS", "5")),
            download_timeout=float(os.getenv("PAPER_AGENT_DOWNLOAD_TIMEOUT", "180")),
            download_candidate_timeout=float(os.getenv("PAPER_AGENT_DOWNLOAD_CANDIDATE_TIMEOUT", "45")),
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
        if not 0 <= self.merged_candidate_limit <= 100:
            raise ValueError("Merged candidate limit must be 0 or between 1 and 100")
        if self.merged_candidate_limit and self.merged_candidate_limit < self.top_k:
            raise ValueError("Merged candidate limit must accommodate Top-k")
        if min(self.selector_input_token_budget, self.report_input_token_budget,
               self.max_fulltext_attempts) < 0:
            raise ValueError("Selector/report budgets and fulltext attempts must be nonnegative")
        if self.ranker not in {"keyword", "semantic"}:
            raise ValueError("Ranker must be keyword or semantic")
        if self.chunk_chars < 1000 or self.max_chunks < 1:
            raise ValueError("Invalid fulltext chunk limits")
        if min(self.http_timeout, self.llm_timeout, self.llm_max_steps) <= 0:
            raise ValueError("Timeouts and model step limit must be positive")
        if not 1 <= self.top_k <= 100:
            raise ValueError("Top-k must be between 1 and 100")
        for value in (
            self.search_concurrency,
            self.download_concurrency,
            self.conversion_concurrency,
            self.llm_concurrency,
        ):
            if not 1 <= value <= 32:
                raise ValueError("Module concurrency must be between 1 and 32")
        if self.cache_mode not in {"off", "read_only", "read_write"}:
            raise ValueError("Cache mode must be off, read_only or read_write")
        if self.selector_fallback not in {"error", "initial_ranking"}:
            raise ValueError("Selector fallback must be error or initial_ranking")
        if not 0 <= self.llm_max_retries <= 5 or not 0 <= self.http_max_retries <= 5:
            raise ValueError("Retry limits must be between 0 and 5")
        if min(self.llm_retry_backoff, self.llm_max_backoff, self.http_retry_backoff) < 0:
            raise ValueError("Retry delays must be nonnegative")
        if self.arxiv_request_interval < 3:
            raise ValueError("arXiv requests must be separated by at least three seconds")
        if min(
            self.llm_max_calls,
            self.llm_max_total_tokens,
            self.llm_max_output_tokens,
            self.llm_input_token_budget,
            self.max_pdf_bytes,
            self.download_timeout,
            self.download_candidate_timeout,
        ) <= 0:
            raise ValueError("Budgets, download size and timeout must be positive")
        if not 0 <= self.max_redirects <= 10:
            raise ValueError("Maximum redirects must be between 0 and 10")

    def require_llm(self) -> None:
        if not all((self.llm_base_url, self.llm_api_key, self.llm_model)):
            raise ValueError("Live mode requires an API key, base URL and model (set DEEPSEEK_API_KEY or LLM_API_KEY)")


def _preferred_env(primary: str, fallback: str, default: str) -> str:
    """Use a nonblank legacy LLM_* value first, then its provider-specific value."""
    for name in (primary, fallback):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _optional_path(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value).resolve() if value else None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be a boolean")
    return normalized in {"true", "1", "yes"}
