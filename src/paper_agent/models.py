"""Version 2 public contracts shared by sources, agents, and MCP tools.

This module intentionally has no business-layer imports. Legacy contracts in
``schemas.py`` remain readable while callers migrate to these richer records.
"""

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ErrorInfo(Record):
    code: str
    message: str
    stage: str
    paper_id: str | None = None
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class StageTiming(Record):
    stage: str
    duration_seconds: float = Field(ge=0)
    request_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    error_count: int = Field(default=0, ge=0)


class ToolCallRecord(Record):
    """One MCP tool invocation issued by the main Agent."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = Field(default=0, ge=0)
    status: str = "pending"
    controller: Literal["scripted_demo", "model_tool_call"] = "model_tool_call"
    error: str | None = None


class PageMapping(Record):
    """One-based PDF page with offsets into the persisted Markdown text."""

    page_number: int = Field(ge=1)
    text: str = ""
    start_char: int = Field(default=0, ge=0)
    end_char: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def ordered_offsets(self) -> "PageMapping":
        if self.end_char < self.start_char:
            raise ValueError("end_char must not precede start_char")
        return self


class EvidenceReference(Record):
    paper_id: str
    page_number: int | None = Field(default=None, ge=1)
    section: str | None = None
    quote: str | None = None
    claim: str | None = None
    evidence_type: Literal["author_claim", "system_inference", "research_idea"] = "author_claim"


class Paper(Record):
    paper_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1000, le=9999)
    abstract: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    arxiv_version: str | None = None
    sources: list[str] = Field(default_factory=list)
    source_records: list[dict[str, Any]] = Field(default_factory=list)
    paper_url: str | None = None
    pdf_url: str | None = None
    pdf_urls: list[str] = Field(default_factory=list)
    citation_count: int | None = Field(default=None, ge=0)
    initial_score: float | None = Field(default=None, ge=0, le=1)
    final_score: float | None = Field(default=None, ge=0, le=1)
    rank: int | None = Field(default=None, ge=1)
    selection_reason: str | None = None
    ranking_reason: str | None = None
    ranking_details: dict[str, Any] = Field(default_factory=dict)
    pdf_path: str | None = None
    markdown_path: str | None = None
    page_map: list[PageMapping] = Field(default_factory=list)
    fulltext_quality: dict[str, Any] = Field(default_factory=dict)
    status: str = "metadata_only"
    error: str | None = None
    errors: list[ErrorInfo] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


SummaryScope = Literal["fulltext", "partial_fulltext", "abstract_only", "failed", "demo_fixture"]


class PaperSummary(Record):
    paper_id: str
    title: str = ""
    summary_scope: SummaryScope = "abstract_only"
    research_question: str = ""
    core_idea: str = ""
    methods: list[str] = Field(default_factory=list)
    experiments: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    relevance: str = ""
    evidence: list[EvidenceReference] = Field(default_factory=list)
    author_claims: list[str] = Field(default_factory=list)
    system_inferences: list[str] = Field(default_factory=list)
    speculative_ideas: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary_path: str | None = None
    markdown: str = ""
    errors: list[ErrorInfo] = Field(default_factory=list)


class PaperReference(Record):
    paper_id: str
    title: str
    paper_url: str | None = None
    doi: str | None = None


class ResearchReport(Record):
    idea: str
    overview: str = ""
    related_work: list[str] = Field(default_factory=list)
    comparison: list[dict[str, Any]] = Field(default_factory=list)
    researched_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    research_directions: list[str] = Field(default_factory=list)
    references: list[PaperReference] = Field(default_factory=list)
    report_path: str | None = None
    markdown: str = ""
    errors: list[ErrorInfo] = Field(default_factory=list)


class PipelineResult(Record):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    idea: str
    query: str = ""
    queries: list[str] = Field(default_factory=list)
    mode: Literal["demo", "live"] = "live"
    execution_mode: Literal["sequential", "concurrent"] = "sequential"
    top_k: int = Field(default=5, ge=1, le=100)
    papers: list[Paper] = Field(default_factory=list)
    selected_papers: list[Paper] = Field(default_factory=list)
    summaries: list[PaperSummary] = Field(default_factory=list)
    report: ResearchReport | None = None
    source_counts: dict[str, int] = Field(default_factory=dict)
    timings: list[StageTiming] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    errors: list[ErrorInfo] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    output_dir: str | None = None
