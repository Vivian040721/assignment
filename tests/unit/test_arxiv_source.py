"""Deterministic source-contract tests; never call the real arXiv service."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

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
