"""Deterministic source-contract tests; never call the real arXiv service."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from paper_agent.config import Settings
from paper_agent.sources import arxiv_source as source


def entry(
    identifier="2410.12345v2", *, title="Agent Memory", published="2024-10-12T00:00:00Z",
    pdf=True, doi="https://doi.org/10.1234/TEST", extra="",
):
    pdf_link = (
        f'<link title="pdf" href="http://arxiv.org/pdf/{identifier}" '
        'rel="related" type="application/pdf"/>' if pdf else ""
    )
    return f"""<entry>
      <id>http://arxiv.org/abs/{identifier}</id><title>{title}</title>
      <summary>  Memory\n for agents  </summary><published>{published}</published>
      <updated>2024-10-15T00:00:00Z</updated>
      <author><name>张 三</name></author><author><name>A. Researcher</name></author>
      <arxiv:doi>{doi}</arxiv:doi><category term="cs.AI"/>{pdf_link}{extra}
    </entry>"""


def feed(*entries):
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom">' + "".join(entries) + "</feed>"
    )


class NoWaitGate:
    def __init__(self):
        self.intervals = []

    @asynccontextmanager
    async def request(self, interval):
        self.intervals.append(interval)
        yield


@pytest.fixture
def gate(monkeypatch):
    gate = NoWaitGate()
    monkeypatch.setattr(source, "_REQUEST_GATE", gate)
    return gate


@pytest.fixture
def settings():
    return Settings(http_max_retries=2, http_retry_backoff=0)


def test_parse_normal_feed_preserves_provenance_and_version():
    paper, = source.parse_feed(feed(entry(title="Agent\n  Memory &amp; Reuse")))
    assert paper.paper_id == "arxiv:2410.12345"
    assert paper.arxiv_id == "2410.12345"
    assert paper.arxiv_version == "v2"
    assert paper.title == "Agent Memory & Reuse"
    assert paper.abstract == "Memory for agents"
    assert paper.authors == ["张 三", "A. Researcher"]
    assert paper.year == 2024
    assert paper.doi == "10.1234/test"
    assert paper.sources == ["arxiv"]
    assert paper.paper_url == "https://arxiv.org/abs/2410.12345v2"
    assert paper.pdf_url == "https://arxiv.org/pdf/2410.12345v2"
    assert paper.pdf_urls == [paper.pdf_url]
    assert paper.source_records[0]["id"] == "2410.12345v2"
    assert paper.metadata["categories"] == ["cs.AI"]


def test_parse_legacy_id_and_missing_optional_metadata():
    paper, = source.parse_feed(feed(entry("math.GT/0309136v1", published="", doi="", pdf=False)))
    assert paper.paper_id == "arxiv:math.GT/0309136"
    assert paper.arxiv_version == "v1"
    assert paper.year is None
    assert paper.doi is None
    assert paper.pdf_url is None
    assert paper.pdf_urls == []


def test_duplicate_versions_keep_newest_and_all_source_records():
    papers = source.parse_feed(feed(
        entry("2410.12345v1", title="Old"), entry("2410.12345v3", title="New"),
        entry("2410.12345v2", title="Middle"), entry("2410.0001", title="Other"),
    ))
    assert len(papers) == 2
    assert papers[0].title == "New"
    assert papers[0].arxiv_version == "v3"
    assert [r["id"] for r in papers[0].source_records] == [
        "2410.12345v1", "2410.12345v3", "2410.12345v2"
    ]
    assert papers[0].pdf_url.endswith("v3")


def test_empty_feed_is_a_valid_empty_result():
    assert source.parse_feed(feed()) == []


@pytest.mark.parametrize(("xml", "code"), [
    ("<feed>", "invalid_xml"),
    ("<html><body>failure</body></html>", "invalid_feed"),
    (feed('<entry><id>http://arxiv.org/api/errors#bad_query</id><title>Error</title>'
          '<summary>Invalid search query</summary></entry>'), "api_error"),
    (feed(entry("not-a-valid-id")), "invalid_entry"),
    (feed(entry(title="")), "invalid_entry"),
    (feed(entry().replace("arxiv.org/abs/", "example.com/abs/")), "invalid_entry"),
])
def test_invalid_and_error_feeds_raise_structured_errors(xml, code):
    with pytest.raises(source.ArxivSourceError) as raised:
        source.parse_feed(xml)
    assert raised.value.error_info.code == code
    assert raised.value.error_info.stage == "arxiv_search"
    assert raised.value.error_info.retryable is False


@pytest.mark.parametrize("extra", [
    '<link title="pdf" href="https://evil.example/pdf/2410.12345v2"/>',
    '<link title="pdf" href="https://arxiv.org/abs/2410.12345v2"/>',
    '<link title="pdf" href="https://arxiv.org/pdf/2410.00001v2"/>',
])
def test_non_pdf_or_mismatched_links_are_not_claimed_as_fulltext(extra):
    paper, = source.parse_feed(feed(entry(pdf=False, extra=extra)))
    assert paper.pdf_url is None


def test_mime_type_pdf_link_without_title_is_accepted():
    paper, = source.parse_feed(feed(entry(
        pdf=False, extra='<link type="application/pdf" href="https://arxiv.org/pdf/2410.12345v2.pdf"/>'
    )))
    assert paper.pdf_url == "https://arxiv.org/pdf/2410.12345v2.pdf"


def test_pdf_candidate_must_match_entry_version_and_remain_unacquired():
    paper, = source.parse_feed(feed(entry(
        pdf=False,
        extra=(
            '<link title="pdf" href="https://arxiv.org/pdf/2410.12345v1"/>'
            '<link title="pdf" href="https://arxiv.org/pdf/2410.12345v2"/>'
        ),
    )))
    assert paper.arxiv_version == "v2"
    assert paper.paper_url == "https://arxiv.org/abs/2410.12345v2"
    assert paper.pdf_url == "https://arxiv.org/pdf/2410.12345v2"
    assert paper.pdf_urls == [paper.pdf_url]
    assert paper.source_records[0]["pdf_url"] == paper.pdf_url
    assert paper.status == "metadata_only"
    assert paper.acquisition_status is None
    assert paper.pdf_path is None
    assert paper.pdf_artifact_id is None


@pytest.mark.parametrize("identifier", ["2410.12345", "2410.12345v1", "2410.12345v3"])
def test_pdf_candidate_with_different_version_is_not_used(identifier):
    paper, = source.parse_feed(feed(entry(pdf=False, extra=(
        f'<link title="pdf" href="https://arxiv.org/pdf/{identifier}"/>'
    ))))
    assert paper.arxiv_version == "v2"
    assert paper.pdf_url is None
    assert paper.pdf_urls == []
    assert paper.source_records[0]["pdf_url"] is None


def test_query_keywords_and_explicit_field_syntax():
    assert source.build_query("large language model") == 'all:"large" AND all:"language" AND all:"model"'
    explicit = 'ti:"agent memory" OR abs:"experience reuse"'
    assert source.build_query(explicit) == explicit
    assert source.build_query('记忆 C++ & a+b #tag') == (
        'all:"记忆" AND all:"C++" AND all:"&" AND all:"a+b" AND all:"#tag"'
    )


@pytest.mark.parametrize("query", ["", " \n ", '""', "bad\x00query", "a" * 8001, None])
def test_bad_queries_fail_before_network(query):
    with pytest.raises(ValueError):
        asyncio.run(source.search_arxiv(query, 1))


@pytest.mark.parametrize("limit", [0, -1, 2001, True, 1.5, "5"])
def test_bad_limits_fail_before_network(limit):
    with pytest.raises(ValueError):
        asyncio.run(source.search_arxiv("agent memory", limit))


def test_http_query_encodes_unicode_specials_and_does_not_close_client(gate, settings):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=feed(entry()).encode("utf-8"))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            papers = await source.search_arxiv("记忆 C++ & a+b", 1, settings=settings, client=client)
            assert not client.is_closed
            return papers

    assert len(asyncio.run(run())) == 1
    assert requests[0].url.params["search_query"] == source.build_query("记忆 C++ & a+b")
    assert requests[0].url.params["max_results"] == "1"
    assert set(requests[0].url.params) == {"search_query", "start", "max_results", "sortBy", "sortOrder"}
    assert "%26" in str(requests[0].url)
    assert gate.intervals == [3.0]


def test_api_200_error_is_not_silently_an_empty_result(gate, settings):
    async def run():
        xml = feed('<entry><id>http://arxiv.org/api/errors#x</id><title>Error</title>'
                   '<summary>Bad query</summary></entry>')
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=xml))) as client:
            return await source.search_arxiv("all:memory", 2, settings=settings, client=client)

    with pytest.raises(source.ArxivSourceError, match="拒绝") as raised:
        asyncio.run(run())
    assert raised.value.error_info.details["api_message"] == "Bad query"


@pytest.mark.parametrize(("exception", "code"), [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network_error"),
])
def test_transport_failures_retry_boundedly_without_leaking_url(gate, settings, exception, code):
    attempts = []

    def handle(request):
        attempts.append(request)
        raise exception("secret-bearing-proxy-url", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 1, settings=settings, client=client)

    with pytest.raises(source.ArxivSourceError) as raised:
        asyncio.run(run())
    assert len(attempts) == 3
    assert raised.value.error_info.code == code
    assert raised.value.error_info.retryable
    assert raised.value.error_info.details["attempts"] == 3
    assert "secret" not in str(raised.value)
    assert len(gate.intervals) == 3


def test_retryable_http_then_success(gate, settings):
    statuses = iter([503, 429, 200])

    def handle(_):
        return httpx.Response(next(statuses), text=feed(entry()), headers={"Retry-After": "0"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.ArxivSource(client, settings=settings).search("memory", 1)

    assert len(asyncio.run(run())) == 1
    assert len(gate.intervals) == 3


@pytest.mark.parametrize(("status", "code", "attempts"), [
    (403, "http_error", 1), (404, "http_error", 1), (302, "http_error", 1),
    (429, "rate_limited", 3), (503, "http_error", 3),
])
def test_http_failures_are_explicit(gate, settings, status, code, attempts):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
            return await source.search_arxiv("memory", 1, settings=settings, client=client)

    with pytest.raises(source.ArxivSourceError) as raised:
        asyncio.run(run())
    assert raised.value.error_info.code == code
    assert raised.value.error_info.details["status_code"] == status
    assert len(gate.intervals) == attempts


def test_config_cannot_reduce_official_rate_interval(gate, settings):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=feed()))) as client:
            await source.search_arxiv("memory", 1, settings=replace(settings, arxiv_request_interval=0), client=client)
            await source.search_arxiv("memory", 1, settings=replace(settings, arxiv_request_interval=6), client=client)

    asyncio.run(run())
    assert gate.intervals == [3.0, 6]


def test_process_gate_shared_across_clients_and_repeated_event_loops(monkeypatch, settings):
    now = [100.0]
    sleeps = []
    starts = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(source, "_REQUEST_GATE", source._RequestGate(clock=lambda: now[0], sleep=fake_sleep))

    def handle(_):
        starts.append(now[0])
        return httpx.Response(200, text=feed())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            await source.search_arxiv("memory", 1, settings=settings, client=client)

    asyncio.run(run())
    asyncio.run(run())
    assert starts == [100, 103]
    assert sleeps == [3]


def test_shared_gate_serializes_concurrent_calls(monkeypatch, settings):
    now = [100.0]
    active = [0]
    peak = [0]
    starts = []

    async def fake_sleep(seconds):
        now[0] += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(source, "_REQUEST_GATE", source._RequestGate(clock=lambda: now[0], sleep=fake_sleep))

    async def handle(_):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        starts.append(now[0])
        await asyncio.sleep(0)
        active[0] -= 1
        return httpx.Response(200, text=feed())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as first:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as second:
                await asyncio.gather(
                    source.search_arxiv("memory", 1, settings=settings, client=first),
                    source.search_arxiv("agents", 1, settings=settings, client=second),
                )

    asyncio.run(run())
    assert peak == [1]
    assert starts[1] - starts[0] >= 3


def test_cancelling_wait_does_not_leak_gate_lock():
    waiting = asyncio.Event()

    async def suspended_sleep(_):
        waiting.set()
        await asyncio.Future()

    gate = source._RequestGate(clock=lambda: 10, sleep=suspended_sleep)
    gate._last_started = 10

    async def run():
        async def acquire():
            async with gate.request(3):
                pass

        task = asyncio.create_task(acquire())
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert gate._lock.acquire(blocking=False)
        gate._lock.release()

    asyncio.run(run())


def test_retry_after_seconds_and_bad_value():
    assert source._retry_after(httpx.Response(429, headers={"Retry-After": "12"})) == 12
    assert source._retry_after(httpx.Response(429, headers={"Retry-After": "bad"})) == 0


def test_result_count_never_exceeds_requested_limit(gate, settings):
    async def run():
        xml = feed(entry("2410.12345"), entry("2410.12346"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=xml))) as client:
            return await source.search_arxiv("memory", 1, settings=settings, client=client)

    assert len(asyncio.run(run())) == 1


@pytest.mark.parametrize("url", [
    "https://[arxiv.org/abs/2410.12345v2",
    "https://[not-an-ip]/abs/2410.12345v2",
    "https://arxiv.org\uff1a443/abs/2410.12345v2",
])
def test_malformed_id_url_raises_structured_source_error(gate, settings, url):
    xml = feed(entry().replace("http://arxiv.org/abs/2410.12345v2", url))

    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, text=xml))
        async with httpx.AsyncClient(transport=transport) as client:
            return await source.search_arxiv("memory", 1, settings=settings, client=client)

    with pytest.raises(source.ArxivSourceError) as raised:
        asyncio.run(run())
    assert raised.value.error_info.code == "invalid_entry"
    assert raised.value.error_info.stage == "arxiv_search"
    assert raised.value.error_info.retryable is False
    assert url not in str(raised.value)
    assert gate.intervals == [3.0]


@pytest.mark.parametrize("url", [
    "https://[arxiv.org/pdf/2410.12345v2",
    "https://arxiv.org\uff1a443/pdf/2410.12345v2",
])
@pytest.mark.parametrize("valid_fallback", [False, True])
def test_malformed_pdf_link_is_skipped_without_losing_papers(url, valid_fallback):
    links = f'<link title="pdf" href="{url}"/>'
    if valid_fallback:
        links += '<link title="pdf" href="https://arxiv.org/pdf/2410.12345v2"/>'
    papers = source.parse_feed(feed(
        entry(pdf=False, extra=links),
        entry("2410.12346v1", title="Other paper"),
    ))
    assert [paper.paper_id for paper in papers] == [
        "arxiv:2410.12345", "arxiv:2410.12346",
    ]
    assert papers[0].title == "Agent Memory"
    assert papers[0].abstract == "Memory for agents"
    expected_pdf = "https://arxiv.org/pdf/2410.12345v2" if valid_fallback else None
    assert papers[0].pdf_url == expected_pdf
    assert papers[0].pdf_urls == ([expected_pdf] if expected_pdf else [])
    assert papers[0].source_records[0]["pdf_url"] == expected_pdf
    assert papers[1].pdf_url == "https://arxiv.org/pdf/2410.12346v1"


@pytest.mark.parametrize("value", ["inf", "+inf", "-inf", "Infinity", "1e309", "NaN"])
def test_nonfinite_retry_after_uses_finite_backoff(gate, settings, monkeypatch, value):
    assert source._retry_after(httpx.Response(429, headers={"Retry-After": value})) == 0
    waits = []
    statuses = iter([429, 503, 200])

    async def capture_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(source.asyncio, "sleep", capture_sleep)

    def handle(_):
        return httpx.Response(
            next(statuses), text=feed(entry()), headers={"Retry-After": value},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv(
                "memory", 1, settings=replace(settings, http_retry_backoff=0.5), client=client,
            )

    assert len(asyncio.run(run())) == 1
    assert waits == [0.5, 1.0]
    assert gate.intervals == [3.0, 3.0, 3.0]


@pytest.mark.parametrize(("value", "expected"), [
    ("Mon, 26 Oct 2026 00:02:00 GMT", 120),
    ("Mon, 26 Oct 2026 02:02:00 +0200", 120),
    ("Mon, 26 Oct 2026 00:02:00", 120),
    ("Sun, 25 Oct 2026 23:58:00 GMT", 0),
    ("bad date", 0),
])
def test_retry_after_dates_use_fixed_clock(monkeypatch, value, expected):
    fixed_now = datetime(2026, 10, 26, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(source, "datetime", FixedDateTime)
    assert source._retry_after(httpx.Response(429, headers={"Retry-After": value})) == expected


@pytest.mark.parametrize(("limit", "available", "expected_requests"), [
    (1, 1, [(0, 1)]),
    (10, 10, [(0, 10)]),
    (40, 40, [(0, 40)]),
    (41, 41, [(0, 40), (40, 1)]),
    (80, 80, [(0, 40), (40, 40)]),
    (81, 81, [(0, 40), (40, 40), (80, 1)]),
    (40, 3, [(0, 40)]),
    (40, 0, [(0, 40)]),
    (2000, 2000, [(start, 40) for start in range(0, 2000, 40)]),
])
def test_requested_scale_and_short_results(gate, settings, limit, available, expected_requests):
    identifiers = [f"2410.{index:05d}" for index in range(available)]
    entries = [entry(identifier) for identifier in identifiers]
    requests = []

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        return httpx.Response(200, text=feed(*entries[start:start + size]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", limit, settings=settings, client=client)

    papers = asyncio.run(run())
    assert [paper.paper_id for paper in papers] == [
        f"arxiv:{identifier}" for identifier in identifiers
    ]
    assert requests == expected_requests
    assert gate.intervals == [3.0] * len(expected_requests)


@pytest.mark.parametrize(("available", "expected"), [
    (43, [(0, 40), (40, 40)]),
    (40, [(0, 40), (40, 40)]),
])
def test_pagination_stops_at_short_or_empty_later_page(gate, settings, available, expected):
    requests = []
    entries = [entry(f"2410.{index:05d}") for index in range(available)]

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        return httpx.Response(200, text=feed(*entries[start:start + size]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 80, settings=settings, client=client)

    assert len(asyncio.run(run())) == available
    assert requests == expected
    assert gate.intervals == [3.0] * len(expected)


def test_pagination_offsets_count_raw_entries_before_deduplication(gate, settings):
    first = [entry(f"2410.{index:05d}") for index in range(20) for _ in range(2)]
    entries = first + [entry(f"2410.{index:05d}") for index in range(20, 41)]
    requests = []

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        return httpx.Response(200, text=feed(*entries[start:start + size]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 41, settings=settings, client=client)

    papers = asyncio.run(run())
    assert [p.arxiv_id for p in papers] == [f"2410.{i:05d}" for i in range(41)]
    assert requests == [(0, 40), (40, 21)]


def test_pagination_preserves_all_versions_and_uses_latest_metadata(gate, settings):
    entries = [entry("2410.00000v1", title="Old")]
    entries += [entry(f"2410.{index:05d}") for index in range(1, 40)]
    entries += [
        entry("2410.00000v3", title="Newest"),
        entry("2410.00000v2", title="Middle"),
        entry("2410.00040", title="Last"),
    ]
    requests = []

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        return httpx.Response(200, text=feed(*entries[start:start + size]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 41, settings=settings, client=client)

    papers = asyncio.run(run())
    assert len(papers) == 41
    newest = papers[0]
    assert newest.paper_id == "arxiv:2410.00000"
    assert newest.title == "Newest" and newest.arxiv_version == "v3"
    assert newest.pdf_url == "https://arxiv.org/pdf/2410.00000v3"
    assert newest.sources == ["arxiv"]
    assert [r["id"] for r in newest.source_records] == [
        "2410.00000v1", "2410.00000v3", "2410.00000v2",
    ]
    assert requests == [(0, 40), (40, 1), (41, 1), (42, 1)]


def test_pagination_deduplicates_identical_records_across_pages(gate, settings):
    entries = [entry(f"2410.{index:05d}") for index in range(40)]
    entries += [entry("2410.00000"), entry("2410.00040")]
    requests = []

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        return httpx.Response(200, text=feed(*entries[start:start + size]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 41, settings=settings, client=client)

    papers = asyncio.run(run())
    assert len(papers) == 41 and len(papers[0].source_records) == 1
    assert requests == [(0, 40), (40, 1), (41, 1)]


def test_pagination_later_page_retries_same_offset_then_succeeds(gate, settings, monkeypatch):
    requests, waits = [], []
    later_statuses = iter([429, 503, 200])

    async def capture_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(source.asyncio, "sleep", capture_sleep)

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        if start == 0:
            return httpx.Response(200, text=feed(*(entry(f"2410.{i:05d}") for i in range(40))))
        return httpx.Response(
            next(later_statuses), text=feed(entry("2410.00040")),
            headers={"Retry-After": "2"},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv(
                "memory", 41, settings=replace(settings, http_retry_backoff=0.5), client=client,
            )

    assert len(asyncio.run(run())) == 41
    assert requests == [(0, 40), (40, 1), (40, 1), (40, 1)]
    assert waits == [2, 2]
    assert gate.intervals == [3.0] * 4


@pytest.mark.parametrize(("failure", "code", "attempts"), [
    (429, "rate_limited", 3),
    (503, "http_error", 3),
    ("timeout", "timeout", 3),
    ("network", "network_error", 3),
    ("xml", "invalid_xml", 1),
    ("api", "api_error", 1),
])
def test_pagination_later_failure_never_returns_partial_success(
    gate, settings, failure, code, attempts,
):
    requests = []

    def handle(request):
        start = int(request.url.params["start"])
        requests.append(start)
        if start == 0:
            return httpx.Response(200, text=feed(*(entry(f"2410.{i:05d}") for i in range(40))))
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if failure == "network":
            raise httpx.ConnectError("fixture network error", request=request)
        if failure == "xml":
            return httpx.Response(200, text="<feed>")
        if failure == "api":
            return httpx.Response(200, text=feed(
                '<entry><id>http://arxiv.org/api/errors#bad_query</id>'
                '<title>Error</title><summary>Bad query</summary></entry>',
            ))
        return httpx.Response(failure)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 41, settings=settings, client=client)

    with pytest.raises(source.ArxivSourceError) as raised:
        asyncio.run(run())
    assert raised.value.error_info.code == code
    assert raised.value.error_info.stage == "arxiv_search"
    assert requests == [0] + [40] * attempts
    assert gate.intervals == [3.0] * (attempts + 1)


def test_pagination_repeated_pages_stop_with_structured_error(gate, settings):
    requests = []
    xml = feed(*(entry(f"2410.{i:05d}") for i in range(40)))

    def handle(request):
        requests.append(int(request.url.params["start"]))
        assert len(requests) <= 4, "Repeated pages must not loop indefinitely"
        return httpx.Response(200, text=xml)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 80, settings=settings, client=client)

    with pytest.raises(source.ArxivSourceError) as raised:
        asyncio.run(run())
    assert raised.value.error_info.code == "pagination_stalled"
    assert requests == [0, 40, 80, 120]


def test_pagination_scan_budget_stops_slow_progress(gate, settings):
    requests = []

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        assert len(requests) <= 3
        if start == 0:
            entries = [entry("2410.00000")] * 40
        else:
            entries = [entry("2410.00000")] * (size - 1) + [entry(f"2410.{start:05d}")]
        return httpx.Response(200, text=feed(*entries))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 41, settings=settings, client=client)

    with pytest.raises(source.ArxivSourceError) as raised:
        asyncio.run(run())
    assert raised.value.error_info.code == "pagination_limit"
    assert requests == [(0, 40), (40, 40), (80, 2)]


def test_pagination_all_pages_share_spacing_and_release_gate_after_cancellation(monkeypatch, settings):
    now, starts, requests = [100.0], [], []
    sleeping = asyncio.Event()
    cancel_spacing = [False]
    cancel_after_first_page = [False]

    async def fake_sleep(delay):
        if cancel_spacing[0]:
            sleeping.set()
            await asyncio.Future()
        now[0] += delay

    gate = source._RequestGate(clock=lambda: now[0], sleep=fake_sleep)
    monkeypatch.setattr(source, "_REQUEST_GATE", gate)

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        starts.append(now[0])
        if cancel_after_first_page[0]:
            cancel_spacing[0] = True
        return httpx.Response(200, text=feed(
            *(entry(f"2410.{i:05d}") for i in range(start, start + size)),
        ))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            papers = await source.search_arxiv("memory", 81, settings=settings, client=client)
            assert len(papers) == 81
            cancel_after_first_page[0] = True
            task = asyncio.create_task(source.search_arxiv("memory", 41, settings=settings, client=client))
            await asyncio.wait_for(sleeping.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert gate._lock.acquire(blocking=False)
            gate._lock.release()
            assert not client.is_closed

    asyncio.run(run())
    assert starts == [100, 103, 106, 109]
    assert requests == [(0, 40), (40, 40), (80, 1), (0, 40)]


def test_pagination_cancelling_later_inflight_request_releases_gate(monkeypatch, settings):
    now, requests = [100.0], []
    later_started = asyncio.Event()

    async def fake_sleep(delay):
        now[0] += delay

    gate = source._RequestGate(clock=lambda: now[0], sleep=fake_sleep)
    monkeypatch.setattr(source, "_REQUEST_GATE", gate)

    async def handle(request):
        start = int(request.url.params["start"])
        requests.append(start)
        if start:
            later_started.set()
            await asyncio.Future()
        return httpx.Response(200, text=feed(*(entry(f"2410.{i:05d}") for i in range(40))))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            task = asyncio.create_task(source.search_arxiv("memory", 41, settings=settings, client=client))
            await asyncio.wait_for(later_started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert gate._lock.acquire(blocking=False)
            gate._lock.release()
            assert not client.is_closed

    asyncio.run(run())
    assert requests == [0, 40]


def test_pagination_limit_2000_compensates_duplicate_beyond_first_50_pages(gate, settings):
    entries = [entry(f"2410.{i:05d}") for i in range(2000)]
    entries.insert(40, entry("2410.00000"))
    requests = []

    def handle(request):
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        requests.append((start, size))
        return httpx.Response(200, text=feed(*entries[start:start + size]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.search_arxiv("memory", 2000, settings=settings, client=client)

    papers = asyncio.run(run())
    assert len(papers) == 2000 and len({p.paper_id for p in papers}) == 2000
    assert len(papers[0].source_records) == 1
    assert requests == [(start, 40) for start in range(0, 2000, 40)] + [(2000, 1)]
    assert gate.intervals == [3.0] * 51


def test_owned_client_ignores_ambient_proxy(monkeypatch, gate, settings):
    captured = {}
    original_client = httpx.AsyncClient

    def factory(**kwargs):
        captured.update(kwargs)
        return original_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text=feed(entry()))),
            **kwargs,
        )

    monkeypatch.setattr(source.httpx, "AsyncClient", factory)
    papers = asyncio.run(source.search_arxiv("memory", 1, settings=settings))
    assert [paper.paper_id for paper in papers] == ["arxiv:2410.12345"]
    assert captured["trust_env"] is False
