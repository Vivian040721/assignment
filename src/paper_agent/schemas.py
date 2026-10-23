"""Frozen starting contract; owners 02/05 coordinate schema changes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperRecord(Record):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    sources: list[str] = Field(default_factory=list)
    source_records: list[dict[str, str]] = Field(default_factory=list)
    landing_url: str = ""
    oa_pdf_url: str | None = None
    base_score: float = 0


class SelectedPaper(Record):
    paper_id: str
    rank: int
    selection_reason: str
    selection_basis: str = "title_abstract"


class PaperArtifact(Record):
    paper_id: str
    pdf_path: str | None = None
    markdown_path: str | None = None
    summary_path: str | None = None
    fulltext_status: Literal["pending", "ready", "demo_fixture", "failed"] = "pending"
    failure_reason: str | None = None
    evidence_coverage: str = "not_read"


class RunResult(Record):
    run_id: str
    mode: Literal["demo", "live"]
    input_idea: str
    query: str
    top_k: int
    processing: Literal["serial", "concurrent"] = "serial"
    source_counts: dict[str, int] = Field(default_factory=dict)
    merged_count: int = 0
    dedup_count: int = 0
    candidate_count: int = 0
    candidates: list[PaperRecord] = Field(default_factory=list)
    selected_papers: list[SelectedPaper] = Field(default_factory=list)
    artifacts: list[PaperArtifact] = Field(default_factory=list)
    report_path: str | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    status: Literal["selected", "fulltexts_ready", "partial", "completed", "demo_completed"]
    errors: list[str] = Field(default_factory=list)
