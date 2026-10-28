"""arXiv Atom search with shared rate limiting and injectable HTTP transport.

Only this module's requests share the process gate. Deployments using several
processes or machines must additionally coordinate their overall arXiv quota.
"""

from __future__ import annotations

import asyncio
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Awaitable, Callable
from urllib.parse import unquote, urlsplit

import httpx

from paper_agent.config import Settings
from paper_agent.models import ErrorInfo, Paper

API_URL = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
_ID = re.compile(r"(?P<base>\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?P<version>v[1-9]\d*)?", re.I)
_FIELD_QUERY = re.compile(
    r"(?<!\w)(?:all|ti|au|abs|co|jr|cat|rn|id|doi|submittedDate|lastUpdatedDate):", re.I
)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_PAGE_SIZE = 40
_MAX_RESULTS = 2000
_MAX_PAGES = 100
_MAX_STALLED_PAGES = 3


class ArxivSourceError(RuntimeError):
    """Explicit source failure; empty search results are returned as ``[]``."""

    def __init__(
        self, code: str, message: str, *, retryable: bool = False, **details: object
    ) -> None:
        super().__init__(message)
        self.error_info = ErrorInfo(
            code=code,
            message=message,
            stage="arxiv_search",
            retryable=retryable,
            details=details,
        )


class _RequestGate:
    """One in-flight request, spaced starts, across clients and asyncio loops.

    The thread lock is acquired without blocking the event loop. Unlike a
    module-level asyncio.Lock this also works across repeated asyncio.run calls.
    Cancellation always releases the lock, including cancellation during spacing.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._lock = threading.Lock()
        self._last_started: float | None = None
        self._clock = clock
        self._sleep = sleep

    @asynccontextmanager
    async def request(self, interval: float) -> AsyncIterator[None]:
        while not self._lock.acquire(blocking=False):
            await self._sleep(0.025)
        try:
            if self._last_started is not None:
                delay = interval - (self._clock() - self._last_started)
                if delay > 0:
                    await self._sleep(delay)
            self._last_started = self._clock()
            yield
        finally:
            self._lock.release()


_REQUEST_GATE = _RequestGate()


def _text(element: ET.Element, name: str) -> str:
    child = element.find(name, NS)
    return " ".join("".join(child.itertext()).split()) if child is not None else ""


def _identifier(raw: str) -> tuple[str, str | None]:
    value = unquote(raw.strip())
    if "://" in value:
        try:
            url = urlsplit(value)
        except ValueError:
            raise ArxivSourceError("invalid_entry", "arXiv 条目包含无效论文标识 URL。") from None
        if url.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            raise ArxivSourceError("invalid_entry", "arXiv 条目包含意外的 ID 主机。")
        if not url.path.startswith("/abs/"):
            raise ArxivSourceError("invalid_entry", "arXiv 条目缺少摘要页 ID。")
        value = url.path.removeprefix("/abs/")
    value = re.sub(r"^arxiv:", "", value, flags=re.I)
    match = _ID.fullmatch(value)
    if not match:
        raise ArxivSourceError("invalid_entry", "arXiv 条目包含无效论文标识。")
    return match.group("base"), match.group("version")


def _pdf_link(entry: ET.Element, base_id: str, version: str | None) -> str | None:
    for link in entry.findall("a:link", NS):
        if link.get("title", "").lower() != "pdf" and link.get("type") != "application/pdf":
            continue
        try:
            url = urlsplit(link.get("href", ""))
        except ValueError:
            continue
        if url.scheme not in {"http", "https"} or url.hostname not in {
            "arxiv.org", "www.arxiv.org", "export.arxiv.org"
        }:
            continue
        path = unquote(url.path)
        if not path.startswith("/pdf/"):
            continue
        match = _ID.fullmatch(path.removeprefix("/pdf/").removesuffix(".pdf"))
        if (
            match
            and match.group("base") == base_id
            and match.group("version") == version
        ):
            return "https://arxiv.org" + path
    return None


def _merge_paper(papers: dict[str, Paper], paper: Paper) -> None:
    previous = papers.get(paper.paper_id)
    if previous is not None:
        records = previous.source_records + [
            record for record in paper.source_records if record not in previous.source_records
        ]
        old_version = int((previous.arxiv_version or "v0")[1:])
        new_version = int((paper.arxiv_version or "v0")[1:])
        paper = paper if new_version > old_version else previous
        paper.source_records = records
    papers[paper.paper_id] = paper


def parse_feed(xml: str | bytes) -> list[Paper]:
    """Parse an Atom feed, keeping the newest version and all source records."""
    return _parse_page(xml)[0]


def _parse_page(xml: str | bytes) -> tuple[list[Paper], int]:
    """Return papers and the raw entry count used for pagination offsets."""
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, ValueError) as error:
        raise ArxivSourceError("invalid_xml", "arXiv 返回了格式错误的 XML。") from error
    if root.tag != "{" + NS["a"] + "}feed":
        raise ArxivSourceError("invalid_feed", "arXiv 响应不是 Atom feed。")
    papers: dict[str, Paper] = {}
    entries = root.findall("a:entry", NS)
    for entry in entries:
        raw_id = _text(entry, "a:id")
        title = _text(entry, "a:title")
        if "/api/errors" in raw_id or title.lower() == "error":
            raise ArxivSourceError(
                "api_error", "arXiv 拒绝了检索词。", api_message=_text(entry, "a:summary")
            )
        base_id, version = _identifier(raw_id)
        if not title:
            raise ArxivSourceError("invalid_entry", "arXiv 条目缺少标题。")
        published = _text(entry, "a:published")
        year = int(published[:4]) if re.match(r"[1-9]\d{3}(?:-|$)", published) else None
        doi = re.sub(
            r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", _text(entry, "x:doi"), flags=re.I
        ).strip().lower() or None
        pdf_url = _pdf_link(entry, base_id, version)
        paper_url = "https://arxiv.org/abs/" + base_id + (version or "")
        record = {
            "source": "arxiv", "id": base_id + (version or ""),
            "arxiv_id": base_id, "arxiv_version": version,
            "url": paper_url, "original_id": raw_id, "pdf_url": pdf_url,
        }
        paper = Paper(
            paper_id="arxiv:" + base_id,
            title=title,
            authors=[
                name for author in entry.findall("a:author", NS)
                if (name := _text(author, "a:name"))
            ],
            year=year,
            abstract=_text(entry, "a:summary"),
            doi=doi,
            arxiv_id=base_id,
            arxiv_version=version,
            sources=["arxiv"],
            source_records=[record],
            paper_url=paper_url,
            pdf_url=pdf_url,
            pdf_urls=[pdf_url] if pdf_url else [],
            metadata={
                "published": published, "updated": _text(entry, "a:updated"),
                "categories": [c.get("term") for c in entry.findall("a:category", NS) if c.get("term")],
                "journal_ref": _text(entry, "x:journal_ref") or None,
                "comment": _text(entry, "x:comment") or None,
            },
        )
        _merge_paper(papers, paper)
    return list(papers.values()), len(entries)


def build_query(query: str) -> str:
    """Preserve explicit arXiv syntax, otherwise safely quote plain keywords.

    httpx encodes the resulting search_query parameter exactly once. Chinese
    text is preserved; translation belongs to the upstream query planner.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    if len(query) > 8000:
        raise ValueError("query must contain at most 8000 characters")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in query):
        raise ValueError("query contains unsupported control characters")
    query = " ".join(query.split())
    if _FIELD_QUERY.search(query):
        return query
    # A literal quote/backslash cannot escape the generated term boundary.
    terms = query.replace('"', " ").replace("\\", " ").split()
    if not terms:
        raise ValueError("query must contain searchable keywords")
    return " AND ".join(f'all:"{term}"' for term in terms)


def _retry_after(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "").strip()
    try:
        delay = float(value)
        return max(0.0, delay) if math.isfinite(delay) else 0.0
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.0


async def _request_page(
    search_query: str,
    start: int,
    page_size: int,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
) -> tuple[list[Paper], int]:
    """Fetch one page; retries retain the same offset and use the shared gate."""
    # Configuration may increase the interval but cannot violate the API terms.
    interval = max(3.0, settings.arxiv_request_interval)
    for attempt in range(settings.http_max_retries + 1):
        try:
            async with _REQUEST_GATE.request(interval):
                response = await client.get(
                    API_URL,
                    params={
                        "search_query": search_query, "start": start, "max_results": page_size,
                        "sortBy": "relevance", "sortOrder": "descending",
                    },
                    timeout=settings.http_timeout,
                    follow_redirects=False,
                    headers={"User-Agent": "assignment-paper-agent/0.2 (arXiv academic search)"},
                )
        except httpx.TransportError as error:
            if attempt < settings.http_max_retries:
                await asyncio.sleep(settings.http_retry_backoff * (2**attempt))
                continue
            code = "timeout" if isinstance(error, httpx.TimeoutException) else "network_error"
            raise ArxivSourceError(
                code, "arXiv 请求在有限重试后仍失败。", retryable=True,
                attempts=attempt + 1, exception_type=type(error).__name__, start=start,
            ) from None
        if response.status_code in _RETRYABLE_STATUS and attempt < settings.http_max_retries:
            await asyncio.sleep(max(_retry_after(response), settings.http_retry_backoff * (2**attempt)))
            continue
        if response.status_code != 200:
            raise ArxivSourceError(
                "rate_limited" if response.status_code == 429 else "http_error",
                f"arXiv 返回 HTTP {response.status_code}",
                retryable=response.status_code in _RETRYABLE_STATUS,
                status_code=response.status_code, attempts=attempt + 1, start=start,
            )
        return _parse_page(response.content)
    raise ArxivSourceError("invalid_config", "arXiv 重试次数不能为负数。")


async def search_arxiv(
    query: str,
    limit: int,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[Paper]:
    """Search up to 2000 unique papers using pages of at most 40 raw entries.

    Offsets count raw entries, not deduplicated papers. A short/empty page ends
    retrieval. Duplicate compensation scans at most twice limit raw slots,
    with at most 100 pages and three consecutive pages without a new base ID.
    Exhausting a safety bound raises a source error rather than returning an
    apparently successful partial result.

    A supplied client remains owned by the caller. Timeouts, exhausted retryable
    failures, HTTP errors and bad feeds raise ArxivSourceError with ErrorInfo.
    Validation errors raise ValueError before making any network request.
    """
    search_query = build_query(query)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_RESULTS:
        raise ValueError("limit must be an integer between 1 and 2000")
    settings = settings or Settings.from_env()
    if client is None:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout,
            proxy=settings.http_proxy or None,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        ) as owned_client:
            return await search_arxiv(query, limit, settings=settings, client=owned_client)
    papers: dict[str, Paper] = {}
    start, stalled_pages = 0, 0
    scan_limit = 2 * limit
    for _ in range(_MAX_PAGES):
        page_size = min(_PAGE_SIZE, limit - len(papers), scan_limit - start)
        if page_size <= 0:
            break
        page_papers, raw_count = await _request_page(
            search_query, start, page_size, settings=settings, client=client,
        )
        previous_count = len(papers)
        for paper in page_papers:
            _merge_paper(papers, paper)
        if len(papers) >= limit:
            return list(papers.values())[:limit]
        if raw_count < page_size:
            return list(papers.values())
        start += raw_count
        stalled_pages = stalled_pages + 1 if len(papers) == previous_count else 0
        if stalled_pages >= _MAX_STALLED_PAGES:
            raise ArxivSourceError(
                "pagination_stalled",
                "arXiv 分页连续未返回新论文。",
                start=start, unique_count=len(papers), limit=limit,
            )
    raise ArxivSourceError(
        "pagination_limit",
        "arXiv 分页达到安全扫描上限，检索未完成。",
        start=start, unique_count=len(papers), limit=limit,
        scan_limit=scan_limit, max_pages=_MAX_PAGES,
    )


class ArxivSource:
    """Optional object adapter with the same process-shared request gate."""

    name = "arxiv"

    def __init__(
        self, client: httpx.AsyncClient | None = None, *, settings: Settings | None = None
    ) -> None:
        self.client = client
        self.settings = settings

    async def search(self, query: str, limit: int) -> list[Paper]:
        return await search_arxiv(query, limit, client=self.client, settings=self.settings)
