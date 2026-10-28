"""OpenAlex adapter tests: all HTTP is local MockTransport, with no real API calls."""

import asyncio

import httpx
import pytest

from paper_agent.config import Settings
from paper_agent.sources import openalex_source
from paper_agent.sources.openalex_source import (
    OpenAlexSearchError,
    parse_work,
    restore_abstract,
    search_openalex,
)


def work(number=1, **overrides):
    return {
        "id": f"https://openalex.org/W{number}",
        "title": f"Agent memory paper {number}",
        **overrides,
    }


def settings(**kwargs):
    return Settings(http_retry_backoff=0, **kwargs)


def run_search(handler, query="agent memory", limit=3, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await search_openalex(
                query, limit, client=client, settings=kwargs.get("settings", settings()),
            )
            assert not client.is_closed, "An injected client belongs to its caller"
            return result

    return asyncio.run(run())


def test_metadata_provenance_and_oa_candidates():
    source = {"id": "https://openalex.org/S12", "display_name": "Example Journal"}
    item = work(
        title="  Agent\n Memory ",
        doi="https://doi.org/10.1234/ABC.2026",
        authorships=[
            {"author": {"display_name": " Alice  Smith "}},
            {"author": {"display_name": "Bob Jones"}},
            {"author": {"display_name": "Alice Smith"}},
            {"author": None},
        ],
        publication_year=2026,
        cited_by_count=12,
        abstract_inverted_index={"memory": [2, 4], "Agent": [0], "uses": [1], "and": [3]},
        primary_location={"landing_page_url": "https://doi.org/10.1234/ABC.2026"},
        best_oa_location={
            "id": "doi:10.1234/abc.2026", "is_oa": True,
            "landing_page_url": "https://journal.example/article/123",
            "pdf_url": "https://journal.example/article/123.pdf", "source": source,
        },
        locations=[
            {"is_oa": True, "pdf_url": "https://repo.example/download/123"},
            {"is_oa": False, "pdf_url": "https://private.example/123.pdf"},
            {"is_oa": True, "pdf_url": "https://journal.example/article/123.pdf"},
        ],
        open_access={"is_oa": True, "oa_status": "green", "oa_url": "https://repo.example/123"},
    )
    paper = run_search(lambda _: httpx.Response(200, json={"results": [item]}))[0]
    assert paper.paper_id == "openalex:W1"
    assert paper.title == "Agent Memory"
    assert paper.authors == ["Alice Smith", "Bob Jones"]
    assert paper.abstract == "Agent uses memory and memory"
    assert paper.doi == "10.1234/abc.2026"
    assert paper.citation_count == 12
    assert paper.sources == ["openalex"]
    assert paper.paper_url == "https://doi.org/10.1234/ABC.2026"
    assert paper.pdf_url == "https://journal.example/article/123.pdf"
    assert paper.pdf_urls == [
        "https://journal.example/article/123.pdf", "https://repo.example/download/123",
    ]
    assert paper.source_records[0]["id"] == "https://openalex.org/W1"
    assert paper.source_records[0]["locations"][0]["source_name"] == "Example Journal"
    assert paper.metadata["oa_landing_urls"] == [
        "https://journal.example/article/123", "https://repo.example/123",
    ]
    assert paper.metadata["pdf_urls_verified"] is False
    assert paper.status == "metadata_only"
    assert paper.acquisition_status is None


def test_missing_fields_are_not_invented():
    paper = parse_work({"id": "https://openalex.org/W2", "title": None})
    assert paper.title == "[Untitled OpenAlex work]"
    assert paper.metadata["title_missing"] is True
    assert paper.abstract == ""
    assert paper.authors == []
    assert paper.year is None
    assert paper.doi is None
    assert paper.citation_count is None
    assert paper.pdf_url is None
    assert paper.pdf_urls == []
    assert paper.metadata["is_oa"] is None


def test_null_malformed_optional_fields_are_tolerated():
    paper = parse_work(work(
        authorships=None, publication_year=True, cited_by_count=-1,
        abstract_inverted_index={"ok": [0, -1, "4", True], "bad": None},
        primary_location=[], best_oa_location="bad", locations=[None, "bad"],
        open_access=None, ids=[], doi="not a doi",
    ))
    assert paper.abstract == "ok"
    assert paper.year is None
    assert paper.citation_count is None
    assert paper.pdf_url is None


@pytest.mark.parametrize("index,expected", [
    (None, ""), ({}, ""), ({"A": [0, 2], "B": [1]}, "A B A"),
    ({"tail": [999999999], "head": [0]}, "head tail"),
    ({"first": [0], "second": [0]}, "first"),
])
def test_restore_abstract(index, expected):
    assert restore_abstract(index) == expected


@pytest.mark.parametrize("location,doi,identifier,version", [
    ({"landing_page_url": "https://arxiv.org/abs/2401.12345v2"}, None, "2401.12345", "v2"),
    ({"id": "pmh:oai:arXiv.org:hep-th/9901001"}, None, "hep-th/9901001", None),
    ({"pdf_url": "https://arxiv.org/pdf/math.GT/0309136v1.pdf"}, None,
     "math.GT/0309136", "v1"),
    ({}, "https://doi.org/10.48550/arXiv.2401.12345", "2401.12345", None),
])
def test_arxiv_identifiers_versions_and_pdf_candidates(location, doi, identifier, version):
    paper = parse_work(work(locations=[location], doi=doi))
    assert paper.arxiv_id == identifier
    assert paper.arxiv_version == version
    assert f"https://arxiv.org/pdf/{identifier}{version or ''}" in paper.pdf_urls


@pytest.mark.parametrize("url", [
    "https://doi.org/10.1234/test", "https://journal.example/article/123",
    "https://journal.example/article/123.html", "javascript:alert(1)",
])
def test_open_access_landing_page_is_not_promoted_to_pdf(url):
    paper = parse_work(work(open_access={"is_oa": True, "oa_url": url}))
    assert paper.pdf_url is None


def test_oa_url_with_pdf_suffix_is_candidate():
    paper = parse_work(work(open_access={
        "is_oa": True, "oa_url": "https://repo.example/paper.PDF?download=1",
    }))
    assert paper.pdf_url == "https://repo.example/paper.PDF?download=1"
    assert paper.metadata["oa_landing_urls"] == []


def test_closed_locations_and_html_pdf_fields_are_excluded():
    paper = parse_work(work(
        best_oa_location={"is_oa": True, "pdf_url": "https://journal.example/paper.html"},
        locations=[
            {"is_oa": False, "pdf_url": "https://paywall.example/file.pdf"},
            {"is_oa": True, "pdf_url": "https://doi.org/10.1234/file"},
            {"is_oa": True, "pdf_url": "ftp://repo.example/paper.pdf"},
            {"is_oa": True, "pdf_url": "https://user:password@repo.example/paper.pdf"},
            {"is_oa": True, "landing_page_url": "https://repo.example/record/123",
             "pdf_url": "https://repo.example/record/123"},
        ],
    ))
    assert paper.pdf_urls == []


def test_doi_and_openalex_id_fallbacks():
    paper = parse_work({
        "ids": {"openalex": "https://openalex.org/W8", "doi": "doi:10.1234/ABC"},
        "display_name": "Fallback title",
    })
    assert paper.paper_id == "openalex:W8"
    assert paper.doi == "10.1234/abc"
    assert paper.title == "Fallback title"


def test_unicode_and_reserved_query_are_encoded_and_key_is_header_only():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    query = "智能体 memory & reuse + reasoning"
    assert run_search(handler, query=query, settings=settings(openalex_api_key="secret-test")) == []
    assert seen[0].url.params["search"] == query
    assert "secret-test" not in str(seen[0].url)
    assert seen[0].headers["Authorization"] == "Bearer secret-test"
    assert seen[0].extensions["timeout"]["read"] == 30


@pytest.mark.parametrize("query,title", [
    ('title.search:"Agent Memory"', "Agent Memory"),
    ("title.search:Agent Memory", "Agent Memory"),
])
def test_title_search_uses_filter_instead_of_full_text_search(query, title):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": []})

    assert run_search(handler, query=query) == []
    assert seen[0].url.params["filter"] == f'title.search:"{title}"'
    assert "search" not in seen[0].url.params


def test_pagination_keeps_fixed_page_size_and_honors_limit(monkeypatch):
    requests = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr(openalex_source.asyncio, "sleep", no_sleep)

    def handler(request):
        requests.append(request)
        page = int(request.url.params["page"])
        start = (page - 1) * 100 + 1
        return httpx.Response(200, json={
            "meta": {"count": 250},
            "results": [work(i) for i in range(start, start + 100)],
        })

    papers = run_search(handler, limit=125)
    assert len(papers) == 125
    assert papers[0].paper_id == "openalex:W1"
    assert papers[-1].paper_id == "openalex:W125"
    assert [r.url.params["page"] for r in requests] == ["1", "2"]
    assert all(r.url.params["per_page"] == "100" for r in requests)


def test_meta_count_and_empty_results_stop_paging():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"meta": {"count": 100}, "results": [work(i) for i in range(100)]})

    assert len(run_search(handler, limit=101)) == 100
    assert len(calls) == 1
    assert run_search(lambda _: httpx.Response(200, json={"results": []})) == []


def test_duplicate_records_do_not_produce_duplicate_papers():
    papers = run_search(lambda _: httpx.Response(200, json={
        "meta": {"count": 3}, "results": [work(1), work(1), work(2)],
    }))
    assert [paper.paper_id for paper in papers] == ["openalex:W1", "openalex:W2"]


def test_server_repeating_full_pages_fails_without_unbounded_requests(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(openalex_source.asyncio, "sleep", no_sleep)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [work(1), work(1), work(2)]})

    with pytest.raises(OpenAlexSearchError, match="分页"):
        run_search(handler)
    assert len(calls) == 2


@pytest.mark.parametrize("status,code,retryable,expected_calls", [
    (429, "rate_limit", True, 3), (503, "server_error", True, 3),
    (403, "authentication_error", False, 1), (401, "authentication_error", False, 1),
    (404, "http_error", False, 1), (302, "http_error", False, 1),
])
def test_http_failure_classification_and_bounded_retries(status, code, retryable, expected_calls):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="response may contain secret-test")

    with pytest.raises(OpenAlexSearchError) as caught:
        run_search(handler, settings=settings(openalex_api_key="secret-test"))
    info = caught.value.error_info
    assert info.code == code
    assert info.stage == "openalex_search"
    assert info.retryable is retryable
    assert len(calls) == expected_calls
    assert "secret-test" not in str(caught.value)
    assert "secret-test" not in info.model_dump_json()


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network_error"),
])
def test_transport_errors_are_explicit(exception, code):
    calls = []

    def handler(request):
        calls.append(request)
        raise exception("secret-test", request=request)

    with pytest.raises(OpenAlexSearchError) as caught:
        run_search(handler)
    assert len(calls) == 3
    assert caught.value.error_info.code == code
    assert caught.value.error_info.details["attempts"] == 3
    assert "secret-test" not in str(caught.value)


def test_retry_after_is_honored_and_success_returned(monkeypatch):
    delays, calls = [], []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(openalex_source.asyncio, "sleep", record_sleep)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"results": [work()]})

    assert len(run_search(handler)) == 1
    assert len(calls) == 2
    assert delays == [2.0]


def test_failure_on_second_page_is_not_silent_partial_success(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(openalex_source.asyncio, "sleep", no_sleep)

    def handler(request):
        if request.url.params["page"] == "1":
            return httpx.Response(200, json={"results": [work(i) for i in range(100)]})
        return httpx.Response(503)

    with pytest.raises(OpenAlexSearchError) as caught:
        run_search(handler, limit=101)
    assert caught.value.error_info.details["partial_count"] == 100
    assert caught.value.error_info.details["page"] == 2


@pytest.mark.parametrize("response,code", [
    (httpx.Response(200, text="<html>bad gateway</html>"), "parse_error"),
    (httpx.Response(200, json={"error": "API error"}), "invalid_response"),
    (httpx.Response(200, json={"results": None}), "invalid_response"),
    (httpx.Response(200, json={"results": [None]}), "invalid_record"),
    (httpx.Response(200, json={"results": [{"title": "missing id"}]}), "invalid_record"),
])
def test_invalid_response_raises_instead_of_returning_empty(response, code):
    with pytest.raises(OpenAlexSearchError) as caught:
        run_search(lambda _: response)
    assert caught.value.error_info.code == code


@pytest.mark.parametrize("query,limit", [("", 1), ("   ", 1), ("query", -1), ("query", 10001), ("query", True)])
def test_invalid_inputs_fail_before_network(query, limit):
    def unexpected(_):
        pytest.fail("Invalid inputs should not reach the API")

    with pytest.raises(ValueError):
        run_search(unexpected, query=query, limit=limit)


def test_zero_limit_does_not_request():
    assert run_search(lambda _: pytest.fail("Zero limit should not request"), limit=0) == []


def test_owned_client_is_closed(monkeypatch):
    created = []
    created_kwargs = []
    original_client = httpx.AsyncClient

    def factory(**kwargs):
        created_kwargs.append(kwargs)
        created.append(original_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": [work()]})),
            **kwargs,
        ))
        return created[-1]

    monkeypatch.setattr(openalex_source.httpx, "AsyncClient", factory)
    assert len(asyncio.run(search_openalex("memory", 1, settings=settings()))) == 1
    assert created[0].is_closed
    assert created_kwargs[0]["trust_env"] is False
