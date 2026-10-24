"""OpenAlex Works search with explicit failures and unverified OA PDF candidates.

This module fetches metadata only. A URL in ``pdf_urls`` is a candidate that the
downloader must still validate; it is never evidence of a downloaded full text.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from paper_agent.config import Settings
from paper_agent.models import ErrorInfo, Paper

_WORKS_URL = "https://api.openalex.org/works"
_MAX_RESULTS = 10_000
_MAX_PAGE_SIZE = 100
_PAGE_PAUSE_SECONDS = 0.12
_MAX_BACKOFF_SECONDS = 30.0
_ARXIV_PATTERN = re.compile(
    r"(?P<id>\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?P<version>v\d+)?$",
    re.IGNORECASE,
)


class OpenAlexSearchError(RuntimeError):
    """A source failure; structured details are safe to persist in run logs."""

    def __init__(self, error_info: ErrorInfo):
        self.error_info = error_info
        super().__init__(error_info.message)


def _failure(code: str, message: str, *, retryable: bool = False, **details: Any):
    return OpenAlexSearchError(
        ErrorInfo(
            code=code,
            message=message,
            stage="openalex_search",
            retryable=retryable,
            details=details,
        )
    )


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return " ".join(value.split()) or None
    return None


def _web_url(value: Any) -> str | None:
    """Retain HTTP(S) metadata links without accepting userinfo or other schemes."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme.lower() not in {"https", "http"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            return None
    except ValueError:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _normalize_doi(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    if text.lower().startswith("doi:"):
        text = text[4:].strip()
    elif text.lower().startswith(("https://doi.org/", "http://doi.org/")):
        text = unquote(urlsplit(text).path.lstrip("/"))
    elif text.lower().startswith(("https://dx.doi.org/", "http://dx.doi.org/")):
        text = unquote(urlsplit(text).path.lstrip("/"))
    return text.lower() if re.fullmatch(r"10\.\d{4,9}/\S+", text) else None


def _arxiv_identifier(value: Any) -> tuple[str, str | None] | None:
    if not isinstance(value, str):
        return None
    text = unquote(value.strip())
    lower = text.lower()
    if lower.startswith(("http://", "https://")):
        url = _web_url(text)
        if not url:
            return None
        parts = urlsplit(url)
        if parts.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            return None
        if not parts.path.startswith(("/abs/", "/pdf/")):
            return None
        text = parts.path[5:]
    elif lower.startswith("pmh:oai:arxiv.org:"):
        text = text[len("pmh:oai:arxiv.org:"):]
    elif lower.startswith("oai:arxiv.org:"):
        text = text[len("oai:arxiv.org:"):]
    elif lower.startswith("10.48550/arxiv."):
        text = text[len("10.48550/arxiv."):]
    elif lower.startswith("arxiv:"):
        text = text[len("arxiv:"):]
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE).rstrip("/")
    match = _ARXIV_PATTERN.fullmatch(text)
    if match:
        identifier = match.group("id")
        if "/" in identifier:
            category, number = identifier.split("/", 1)
            prefix, separator, suffix = category.partition(".")
            category = prefix.lower()
            if separator:
                category += "." + (suffix.upper() if len(suffix) == 2 else suffix.lower())
            identifier = category + "/" + number
        return identifier, match.group("version")
    return None


def restore_abstract(index: Any) -> str:
    """Reconstruct ordered tokens without allocating a sparse position array."""
    if not isinstance(index, dict):
        return ""
    positions: dict[int, str] = {}
    for word, offsets in index.items():
        if not isinstance(word, str) or not isinstance(offsets, list):
            continue
        for offset in offsets:
            if isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0:
                positions.setdefault(offset, word)
    return " ".join(positions[position] for position in sorted(positions))


def _looks_like_pdf(url: str) -> bool:
    parts = urlsplit(url)
    path = parts.path.lower().rstrip("/")
    return path.endswith(".pdf") or (
        parts.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
        and path.startswith("/pdf/")
        and _arxiv_identifier(url) is not None
    )


def _pdf_candidate(value: Any, *, explicit: bool = False) -> str | None:
    url = _web_url(value)
    if not url:
        return None
    parts = urlsplit(url)
    path = parts.path.lower()
    # Do not promote DOI pages, work pages, abstract pages, or obvious HTML to PDF.
    if parts.hostname in {"doi.org", "dx.doi.org", "openalex.org", "www.openalex.org"}:
        return None
    if path.rstrip("/").endswith((".html", ".htm", ".xhtml")):
        return None
    if parts.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        return url if _looks_like_pdf(url) else None
    return url if explicit or _looks_like_pdf(url) else None


def _integer(value: Any, *, minimum: int, maximum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value if maximum is None or value <= maximum else None


def parse_work(work: dict[str, Any]) -> Paper:
    """Normalize one work, preserving provenance and distinct landing/PDF URLs."""
    ids = work.get("ids") if isinstance(work.get("ids"), dict) else {}
    work_id = _text(work.get("id")) or _text(ids.get("openalex"))
    if not work_id or not re.fullmatch(r"(?:https?://openalex\.org/)?W\d+", work_id):
        raise _failure("invalid_record", "OpenAlex 返回了缺少有效 ID 的论文。")
    short_id = work_id.rsplit("/", 1)[-1]
    work_id = f"https://openalex.org/{short_id}"
    best = work.get("best_oa_location")
    primary = work.get("primary_location")
    locations = work.get("locations")
    location_pairs = [(best, True), (primary, False)]
    if isinstance(locations, list):
        location_pairs.extend((location, False) for location in locations)
    ordered_locations = [
        (location, is_best) for location, is_best in location_pairs
        if isinstance(location, dict)
    ]
    oa = work.get("open_access") if isinstance(work.get("open_access"), dict) else {}
    doi = _normalize_doi(work.get("doi")) or _normalize_doi(ids.get("doi"))
    alternate_dois = list(dict.fromkeys(
        location_doi
        for location, _ in ordered_locations
        if (location_doi := _normalize_doi(location.get("doi")))
    ))
    doi = doi or next(iter(alternate_dois), None)
    pdf_urls: list[str] = []
    landing_urls: list[str] = []
    source_locations: list[dict[str, Any]] = []
    arxiv_values: list[Any] = [ids.get("arxiv"), doi, *alternate_dois]
    for location, is_best in ordered_locations:
        landing = _web_url(location.get("landing_page_url"))
        arxiv_values.extend([location.get("id"), landing, location.get("pdf_url")])
        source = location.get("source") if isinstance(location.get("source"), dict) else {}
        is_oa = location.get("is_oa") is True or (
            is_best and location.get("is_oa") is not False
        )
        if is_oa and landing and landing not in landing_urls:
            landing_urls.append(landing)
        pdf = _pdf_candidate(location.get("pdf_url"), explicit=True) if is_oa else None
        # A duplicated landing page needs an independent PDF-shaped URL signal.
        if pdf == landing and pdf and not _looks_like_pdf(pdf):
            pdf = None
        if pdf and pdf not in pdf_urls:
            pdf_urls.append(pdf)
        record = {
            "id": location.get("id"),
            "source_id": source.get("id"),
            "source_name": source.get("display_name"),
            "landing_page_url": landing,
            "pdf_url": pdf,
            "is_oa": is_oa,
            "version": location.get("version"),
            "license": location.get("license"),
        }
        if record not in source_locations:
            source_locations.append(record)
    oa_url = _web_url(oa.get("oa_url")) if oa.get("is_oa") is True else None
    if oa_url:
        arxiv_values.append(oa_url)
        if oa_url not in landing_urls:
            landing_urls.append(oa_url)
        oa_pdf = _pdf_candidate(oa_url)
        if oa_pdf and oa_pdf not in pdf_urls:
            pdf_urls.append(oa_pdf)
    arxiv = next(
        (identifier for value in arxiv_values if (identifier := _arxiv_identifier(value))),
        None,
    )
    if arxiv:
        arxiv_pdf = f"https://arxiv.org/pdf/{arxiv[0]}{arxiv[1] or ''}"
        if arxiv_pdf not in pdf_urls:
            pdf_urls.append(arxiv_pdf)
    authorships = work.get("authorships")
    authors = []
    for item in authorships if isinstance(authorships, list) else []:
        author = item.get("author") if isinstance(item, dict) else None
        name = _text(author.get("display_name")) if isinstance(author, dict) else None
        if name and name not in authors:
            authors.append(name)
    title = _text(work.get("title")) or _text(work.get("display_name"))
    primary = primary if isinstance(primary, dict) else {}
    paper_url = _web_url(primary.get("landing_page_url"))
    return Paper(
        paper_id=f"openalex:{short_id}",
        title=title or "[Untitled OpenAlex work]",
        authors=authors,
        year=_integer(work.get("publication_year"), minimum=1000, maximum=9999),
        abstract=restore_abstract(work.get("abstract_inverted_index")),
        doi=doi,
        arxiv_id=arxiv[0] if arxiv else None,
        arxiv_version=arxiv[1] if arxiv else None,
        sources=["openalex"],
        source_records=[{
            "source": "openalex", "id": work_id, "url": work_id,
            "locations": source_locations,
        }],
        paper_url=paper_url or (f"https://doi.org/{doi}" if doi else work_id),
        pdf_url=next(iter(pdf_urls), None),
        pdf_urls=pdf_urls,
        citation_count=_integer(work.get("cited_by_count"), minimum=0),
        metadata={
            "openalex_id": work_id,
            "is_oa": oa.get("is_oa") if isinstance(oa.get("is_oa"), bool) else None,
            "oa_status": _text(oa.get("oa_status")),
            "oa_landing_urls": landing_urls,
            "alternate_dois": alternate_dois,
            "publication_date": _text(work.get("publication_date")),
            "language": _text(work.get("language")),
            "work_type": _text(work.get("type")),
            "title_missing": title is None,
            "pdf_urls_verified": False,
        },
    )


def _retry_delay(response: httpx.Response | None, attempt: int, base: float) -> float:
    delay = base * (2 ** attempt)
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            delay = max(delay, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = max(delay, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return min(max(delay, 0), _MAX_BACKOFF_SECONDS)


async def _get_page(
    client: httpx.AsyncClient, params: dict[str, Any], settings: Settings,
    headers: dict[str, str],
) -> httpx.Response:
    # Configuration means retries *after* the initial request; bound accidental values.
    retries = max(0, min(int(getattr(settings, "http_max_retries", 2)), 5))
    base = max(0.0, float(getattr(settings, "http_retry_backoff", 0.5)))
    for attempt in range(retries + 1):
        response = None
        try:
            response = await client.get(
                _WORKS_URL, params=params, headers=headers, timeout=settings.http_timeout,
            )
        except httpx.TimeoutException:
            failure = _failure("timeout", "OpenAlex 请求超时。", retryable=True)
        except httpx.RequestError as error:
            failure = _failure(
                "network_error", "OpenAlex 网络请求失败。", retryable=True,
                exception_type=type(error).__name__,
            )
        else:
            status = response.status_code
            if 200 <= status < 300:
                return response
            if status == 429:
                failure = _failure(
                    "rate_limit", "OpenAlex 触发限流或每日额度不足。",
                    retryable=True, status_code=status,
                )
            elif status >= 500:
                failure = _failure(
                    "server_error", f"OpenAlex 返回 HTTP {status}",
                    retryable=True, status_code=status,
                )
            else:
                code = "authentication_error" if status in {401, 403} else "http_error"
                raise _failure(code, f"OpenAlex 返回 HTTP {status}", status_code=status)
        if attempt >= retries:
            failure.error_info.details["attempts"] = attempt + 1
            raise failure from None
        await asyncio.sleep(_retry_delay(response, attempt, base))
    raise AssertionError("unreachable retry loop")


async def search_openalex(
    query: str, limit: int, *, settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[Paper]:
    """Search Works with basic pagination; return empty only for genuine no results.

    The caller owns an injected HTTP client. Authentication uses a bearer header,
    so keys do not enter URLs or structured error details. Up to the first 10,000
    results are supported; deep cursor paging is deliberately outside this API.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("OpenAlex query must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= _MAX_RESULTS:
        raise ValueError("OpenAlex limit must be an integer between 0 and 10000")
    if limit == 0:
        return []
    settings = settings or Settings.from_env()
    if client is None:
        async with httpx.AsyncClient(
            timeout=settings.http_timeout, proxy=settings.http_proxy or None,
            follow_redirects=False,
        ) as owned_client:
            return await search_openalex(query, limit, settings=settings, client=owned_client)
    headers = {"User-Agent": "group-paper-agent/0.2 (OpenAlex metadata search)"}
    if settings.openalex_api_key:
        headers["Authorization"] = f"Bearer {settings.openalex_api_key}"
    per_page = min(limit, _MAX_PAGE_SIZE)
    papers: list[Paper] = []
    seen_ids: set[str] = set()
    page = 1
    while page * per_page <= _MAX_RESULTS:
        params: dict[str, Any] = {"search": query.strip(), "per_page": per_page, "page": page}
        try:
            response = await _get_page(client, params, settings, headers)
        except OpenAlexSearchError as error:
            error.error_info.details.update(page=page, partial_count=len(papers))
            raise
        try:
            payload = response.json()
        except ValueError:
            raise _failure(
                "parse_error", "OpenAlex 返回了无效 JSON。", page=page,
                partial_count=len(papers),
            ) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise _failure("invalid_response", "OpenAlex 响应没有结果列表。", page=page)
        results = payload["results"]
        previous_count = len(papers)
        for index, work in enumerate(results):
            if not isinstance(work, dict):
                raise _failure(
                    "invalid_record", "OpenAlex 返回了非对象论文记录。",
                    page=page, work_index=index,
                )
            try:
                paper = parse_work(work)
            except OpenAlexSearchError as error:
                error.error_info.details.update(page=page, work_index=index)
                raise
            if paper.paper_id not in seen_ids:
                seen_ids.add(paper.paper_id)
                papers.append(paper)
                if len(papers) >= limit:
                    return papers
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        count = _integer(meta.get("count"), minimum=0)
        if len(results) < per_page or (count is not None and page * per_page >= count):
            break
        if len(papers) == previous_count:
            raise _failure(
                "invalid_response", "OpenAlex 分页没有返回新的论文 ID。",
                page=page, partial_count=len(papers),
            )
        page += 1
        if page * per_page <= _MAX_RESULTS:
            await asyncio.sleep(_PAGE_PAUSE_SECONDS)
    return papers
