"""Shared PDF runtime rules and deterministic, path-free artifact identifiers."""

from __future__ import annotations

import hashlib
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paper_agent.models import EvidenceLevel, Paper

PDF_PARSE_LOCK = threading.RLock()


def stable_artifact_id(kind: str, paper_id: str) -> str:
    """Return the frozen job-scoped artifact ID without exposing a local path."""
    return hashlib.sha256(f"{kind}:{paper_id}".encode("utf-8")).hexdigest()[:20]


def refresh_reading_status(paper: Paper) -> None:
    """Describe currently available material, not whether it has been analyzed."""
    status: EvidenceLevel
    if paper.markdown_path:
        if paper.page_map and paper.fulltext_quality.get("complete") is True:
            status = "fulltext"
        else:
            status = "partial_fulltext"
    elif paper.abstract.strip():
        status = "abstract_only"
    else:
        status = "metadata_only"
    paper.reading_status = status
