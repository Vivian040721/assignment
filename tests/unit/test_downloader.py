"""Downloader tests use generated PDFs and explicit mock DNS; no live network."""

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pymupdf
import pytest

from paper_agent.config import Settings
from paper_agent.models import Paper
from paper_agent.services.downloader import Downloader, download_pdf


def pdf_bytes():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Mock test fixture: a readable PDF, not a research paper.")
        return document.tobytes()


async def public_dns(host, port):
    return ["93.184.216.34"]


def configuration(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path, http_max_retries=1, http_retry_backoff=0, **kwargs)


def paper(**kwargs):
    return Paper(paper_id="../../unsafe:paper/1", title="Mock fixture", **kwargs)


def run_download(tmp_path, handler, document=None, **settings):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await Downloader(
                settings=configuration(tmp_path, **settings), client=client, resolver=public_dns
            ).download(document or paper(pdf_url="https://papers.example/one.pdf"))
            assert not client.is_closed
            return result

    return asyncio.run(execute())


def test_success_copy_atomic_safe_filename_and_tls_host_pinning(tmp_path):
    original = paper(pdf_url="https://papers.example/one.pdf?token=not-logged")
    payload = pdf_bytes()

    def handler(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "papers.example"
        assert request.extensions["sni_hostname"] == "papers.example"
        assert request.url.params["token"] == "not-logged"
        return httpx.Response(200, content=payload)

    result = run_download(tmp_path, handler, original)
    assert result.status == "downloaded"
    assert result.acquisition_status == "downloaded"
    assert result.conversion_status == "not_attempted"
    assert result.reading_status == "metadata_only"
    assert result.pdf_artifact_id == hashlib.sha256(
        f"pdf:{original.paper_id}".encode("utf-8")
    ).hexdigest()[:20]
    assert result.markdown_artifact_id is None
    assert original.pdf_path is None and original.status == "metadata_only"
    assert original.metadata == {}
    path = Path(result.pdf_path)
    assert path.parent == tmp_path / "pdfs"
    assert path.name == hashlib.sha256(original.paper_id.encode()).hexdigest() + ".pdf"
    assert path.read_bytes() == payload
    assert not list(path.parent.glob("*.tmp"))
    assert result.metadata["downloads"]["request_count"] == 1
    assert result.metadata["downloads"]["pages"] == 1
    assert "token" not in json.dumps(result.metadata["downloads"])


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_unavailable_status_is_explicit(tmp_path, status):
    result = run_download(tmp_path, lambda request: httpx.Response(status))
    assert result.status == "no_open_access" and result.pdf_path is None
    assert result.acquisition_status == "unavailable"
    assert result.conversion_status == "not_attempted"
    assert result.reading_status == "metadata_only"
    assert result.pdf_artifact_id is None
    assert result.errors[-1].code == "access_unavailable"
    assert result.errors[-1].details["status_code"] == status
    assert result.metadata["downloads"]["request_count"] == 1
    assert not list(tmp_path.rglob("*.pdf"))


def test_failed_refresh_preserves_existing_pdf_artifact(tmp_path):
    path = tmp_path / "existing.pdf"
    payload = pdf_bytes()
    path.write_bytes(payload)
    original = paper(
        pdf_url="https://papers.example/removed.pdf",
        pdf_path=str(path),
        acquisition_status="downloaded",
    )

    result = run_download(tmp_path, lambda request: httpx.Response(404), original)

    assert result.status == "no_open_access"
    assert result.acquisition_status == "unavailable"
    assert result.pdf_path == str(path)
    assert result.pdf_artifact_id == hashlib.sha256(
        f"pdf:{original.paper_id}".encode("utf-8")
    ).hexdigest()[:20]
    assert path.read_bytes() == payload


@pytest.mark.parametrize("content", [b"<html>Sign in</html>", b"%PDF-1.7\nthis is not a PDF"])
def test_fake_and_broken_pdf_never_saved(tmp_path, content):
    result = run_download(tmp_path, lambda request: httpx.Response(200, content=content))
    assert result.status == "download_failed"
    assert result.errors[-1].code == "invalid_pdf"
    assert not list(tmp_path.rglob("*.pdf"))


def test_fallback_preserves_errors_and_final_candidate(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/bad.pdf":
            return httpx.Response(404)
        return httpx.Response(200, content=pdf_bytes())

    result = run_download(tmp_path, handler, paper(
        pdf_url="https://papers.example/bad.pdf",
        pdf_urls=["https://papers.example/bad.pdf", "https://papers.example/good.pdf"],
    ))
    assert seen == ["/bad.pdf", "/good.pdf"]
    assert result.status == "downloaded" and result.error is None
    assert result.errors[0].code == "access_unavailable"
    assert result.metadata["downloads"]["candidate"].endswith("/good.pdf")
    assert result.metadata["downloads"]["request_count"] == 2


def test_timeout_retries_are_bounded(tmp_path):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("sensitive URL must not be logged", request=request)

    result = run_download(tmp_path, handler)
    assert result.status == "download_failed"
    assert result.acquisition_status == "failed"
    assert result.conversion_status == "not_attempted"
    assert count == 2 and result.metadata["downloads"]["retries"] == 1
    assert result.errors[-1].code == "timeout"
    assert "sensitive" not in result.model_dump_json()


def test_retryable_http_then_success(tmp_path):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(503) if count == 1 else httpx.Response(200, content=pdf_bytes())

    result = run_download(tmp_path, handler)
    assert result.status == "downloaded"
    assert result.metadata["downloads"]["request_count"] == 2


def test_candidate_timeout_falls_back_to_next_candidate(tmp_path):
    async def handler(request):
        if request.url.path == "/slow.pdf":
            await asyncio.sleep(0.2)
            return httpx.Response(200, content=pdf_bytes())
        return httpx.Response(200, content=pdf_bytes())

    document = paper(pdf_url="https://papers.example/slow.pdf",
                     pdf_urls=["https://papers.example/slow.pdf", "https://papers.example/good.pdf"])
    result = run_download(tmp_path, handler, document, download_timeout=1,
                          download_candidate_timeout=0.05, http_timeout=0.2)
    assert result.status == "downloaded"
    assert result.metadata["downloads"]["request_count"] == 2
    assert any(error["code"] == "candidate_timeout" for error in result.metadata["downloads"]["errors"])


def test_total_timeout_covers_all_attempts(tmp_path):
    async def handler(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, content=pdf_bytes())

    result = run_download(tmp_path, handler, download_timeout=0.05)
    assert result.status == "download_failed"
    assert result.errors[-1].code == "download_timeout"
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("declared", [False, True])
def test_byte_limit_streamed_and_content_length(tmp_path, declared):
    body = pdf_bytes()
    headers = {"Content-Length": str(len(body))} if declared else {"Content-Length": "unknown"}
    result = run_download(
        tmp_path, lambda request: httpx.Response(200, content=body, headers=headers),
        max_pdf_bytes=10,
    )
    assert result.status == "download_failed" and result.errors[-1].code == "pdf_too_large"
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://papers.example/a.pdf", "http://localhost/a.pdf",
    "https://user:password@papers.example/a.pdf", "http://127.0.0.1/a.pdf",
    "http://10.1.2.3/a.pdf", "http://169.254.169.254/latest/meta-data",
    "http://[::1]/a.pdf", "http://[::ffff:127.0.0.1]/a.pdf", "http://service.internal/a.pdf",
    "http://papers.example/a\r\nHost:localhost", "https://[fe80::1%25eth0]/a.pdf",
])
def test_unsafe_candidates_never_reach_transport(tmp_path, url):
    def handler(request):
        pytest.fail("Unsafe URL was requested")

    result = run_download(tmp_path, handler, paper(pdf_url=url))
    assert result.status == "download_failed"
    assert result.errors[-1].code == "unsafe_url"
    assert result.metadata["downloads"]["request_count"] == 0


def test_dns_private_or_mixed_answer_is_rejected_even_with_mock_client(tmp_path):
    async def execute():
        async def mixed_dns(host, port):
            return ["93.184.216.34", "10.0.0.1"]

        def handler(request):
            pytest.fail("DNS validation must not be bypassed for MockTransport")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await Downloader(
                settings=configuration(tmp_path), client=client, resolver=mixed_dns
            ).download(paper(pdf_url="https://papers.example/a.pdf"))

    result = asyncio.run(execute())
    assert result.status == "download_failed"
    assert result.errors[-1].code == "unsafe_url"


def test_redirect_revalidates_dns_and_blocks_private_destination(tmp_path):
    async def execute():
        count = 0

        async def resolver(host, port):
            return ["127.0.0.1"] if host == "internal.example" else ["93.184.216.34"]

        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(302, headers={"Location": "https://internal.example/private.pdf"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await Downloader(
                settings=configuration(tmp_path), client=client, resolver=resolver
            ).download(paper(pdf_url="https://papers.example/a.pdf"))
        assert count == 1
        return result

    result = asyncio.run(execute())
    assert result.errors[-1].code == "unsafe_url"
    assert result.metadata["downloads"]["request_count"] == 1


def test_relative_redirect_and_redirect_limit(tmp_path):
    def handler(request):
        if request.url.path == "/one.pdf":
            return httpx.Response(302, headers={"Location": "/final.pdf"})
        return httpx.Response(200, content=pdf_bytes())

    result = run_download(tmp_path, handler)
    assert result.status == "downloaded" and result.metadata["downloads"]["redirects"] == 1
    other = run_download(tmp_path / "limited", handler, max_redirects=0)
    assert other.errors[-1].code == "too_many_redirects"
    assert other.metadata["downloads"]["request_count"] == 1


def test_cache_is_hashed_and_readability_verified(tmp_path):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, content=pdf_bytes())

    first = run_download(tmp_path, handler)
    second = run_download(tmp_path, handler)
    assert count == 1 and second.metadata["downloads"]["cache_hit"] is True
    assert second.metadata["downloads"]["request_count"] == 0
    Path(first.pdf_path).write_bytes(b"%PDF-1.7\ncorrupt")
    third = run_download(tmp_path, handler)
    assert count == 2 and third.metadata["downloads"]["cache_hit"] is False
    assert third.status == "downloaded"
    manifest = Path(first.pdf_path).with_suffix(".json")
    content = json.loads(manifest.read_text(encoding="utf-8"))
    content["sha256"] = "0" * 64
    manifest.write_text(json.dumps(content), encoding="utf-8")
    fourth = run_download(tmp_path, handler)
    assert count == 3 and fourth.status == "downloaded"


@pytest.mark.parametrize("mode", ["off", "read_only"])
def test_nonwrite_cache_modes_still_write_artifact_without_manifest(tmp_path, mode):
    result = run_download(
        tmp_path, lambda request: httpx.Response(200, content=pdf_bytes()), cache_mode=mode
    )
    assert result.status == "downloaded" and Path(result.pdf_path).exists()
    assert not list(tmp_path.rglob("*.json"))


def test_module_entry_no_candidate_and_original_unchanged(tmp_path):
    original = paper()
    result = asyncio.run(download_pdf(original, settings=configuration(tmp_path)))
    assert result.status == "no_open_access"
    assert result.acquisition_status == "unavailable"
    assert result.conversion_status == "not_attempted"
    assert result.reading_status == "metadata_only"
    assert result.errors[-1].code == "no_open_access"
    assert original.status == "metadata_only" and not original.errors


def test_unavailable_pdf_does_not_hide_abstract_reading_level(tmp_path):
    result = asyncio.run(download_pdf(
        paper(abstract="A real abstract remains available for limited analysis."),
        settings=configuration(tmp_path),
    ))
    assert result.acquisition_status == "unavailable"
    assert result.reading_status == "abstract_only"


def test_dns_failure_becomes_per_paper_error(tmp_path):
    async def execute():
        async def broken_dns(host, port):
            raise OSError("DNS unavailable")

        return await Downloader(
            settings=configuration(tmp_path), resolver=broken_dns
        ).download(paper(pdf_url="https://papers.example/a.pdf"))

    result = asyncio.run(execute())
    assert result.status == "download_failed" and result.errors[-1].code == "dns_error"


def test_search_and_download_share_the_same_arxiv_gate():
    from paper_agent.services import downloader
    from paper_agent.sources import arxiv_source

    assert downloader._REQUEST_GATE is arxiv_source._REQUEST_GATE


def test_cache_read_only_preserves_existing_cache_after_hit(tmp_path):
    def handler(request):
        return httpx.Response(200, content=pdf_bytes())

    first = run_download(tmp_path, handler)
    path = Path(first.pdf_path)
    before = path.stat().st_mtime_ns
    second = run_download(tmp_path, handler, cache_mode="read_only")
    assert second.metadata["downloads"]["cache_hit"]
    assert path.stat().st_mtime_ns == before


def test_public_literal_does_not_call_dns(tmp_path):
    async def execute():
        async def forbidden_dns(host, port):
            pytest.fail("Literal addresses do not need DNS")

        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=pdf_bytes())
        )) as client:
            return await Downloader(
                settings=replace(configuration(tmp_path), cache_enabled=False),
                client=client, resolver=forbidden_dns,
            ).download(paper(pdf_url="https://93.184.216.34/a.pdf"))

    assert asyncio.run(execute()).status == "downloaded"


@pytest.mark.parametrize("proxy", ["http://proxy.example:8080", "https://proxy.example:8080"])
def test_real_httpcore_proxy_tunnel_keeps_original_tls_hostname(tmp_path, proxy):
    """Exercise HTTPX/httpcore proxy code with fake sockets, no network or TLS bypass."""
    import httpcore

    payload = pdf_bytes()
    tls_names, writes = [], []

    class Socket(httpcore.AsyncNetworkStream):
        def __init__(self):
            self.phase = "connect"
            self.read_phases = set()

        async def read(self, max_bytes, timeout=None):
            if self.phase in self.read_phases:
                return b""
            self.read_phases.add(self.phase)
            if self.phase == "connect":
                return b"HTTP/1.1 200 Connection established\r\n\r\n"
            return (
                b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n" + payload
            )

        async def write(self, buffer, timeout=None):
            writes.append(buffer)
            if buffer.startswith(b"GET "):
                self.phase = "get"

        async def aclose(self):
            pass

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            import ssl

            assert ssl_context.check_hostname and ssl_context.verify_mode == ssl.CERT_REQUIRED
            tls_names.append(server_hostname)
            return self

        def get_extra_info(self, info):
            return None

    class Backend(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            assert host == "proxy.example" and port == 8080
            return Socket()

    async def execute():
        transport = httpx.AsyncHTTPTransport(proxy=proxy)
        # A fake network backend exercises actual CONNECT/TLS orchestration,
        # unlike MockTransport which never runs the HTTPcore TLS path.
        transport._pool._network_backend = Backend()
        async with httpx.AsyncClient(transport=transport) as client:
            return await Downloader(
                settings=configuration(tmp_path, http_proxy=proxy),
                client=client, resolver=public_dns,
            ).download(paper(pdf_url="https://papers.example/a.pdf"))

    result = asyncio.run(execute())
    assert result.status == "downloaded", result.error
    assert tls_names == (["proxy.example", "papers.example"] if proxy.startswith("https:")
                         else ["papers.example"])
    assert any(b"CONNECT 93.184.216.34:443" in value for value in writes)
    assert any(b"Host: papers.example" in value for value in writes)
