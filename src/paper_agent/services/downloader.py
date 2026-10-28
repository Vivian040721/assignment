"""Bounded, public-network-only PDF acquisition with verified on-disk caching.

``Downloader`` accepts an explicit DNS resolver for deterministic tests. Resolution
is never skipped: requests connect to a validated IP, retain the original Host
header and TLS SNI, and every redirect is validated again. Injected clients remain
owned by their caller and must use their normal certificate-verification policy.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import pymupdf

from paper_agent.config import Settings
from paper_agent.models import ErrorInfo, Paper
from paper_agent.services.pdf_runtime import (
    PDF_PARSE_LOCK,
    refresh_reading_status,
    stable_artifact_id,
)
from paper_agent.sources.arxiv_source import _REQUEST_GATE

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_UNAVAILABLE_STATUS = {401, 403, 404, 410}
_MAX_CANDIDATES = 8


class DownloadError(RuntimeError):
    """A safe, structured acquisition failure, without raw response bodies."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, **details) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details


async def resolve_public_host(host: str, port: int) -> list[str]:
    """Resolve without blocking the event loop; validation happens in the caller."""
    rows = await asyncio.get_running_loop().getaddrinfo(
        host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
    )
    return list(dict.fromkeys(row[4][0] for row in rows))


def _display_url(raw: str) -> str:
    """Keep audit links useful without copying credentials, query tokens or fragments."""
    try:
        value = urlsplit(raw)
        host = value.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        return urlunsplit((value.scheme, host, value.path, "", ""))[:2000]
    except ValueError:
        return "<invalid URL>"


def _pdf_details(data: bytes) -> dict[str, object]:
    if not data.startswith(b"%PDF-"):
        raise DownloadError("invalid_pdf", "响应内容不是有效的 PDF 文件。")
    try:
        with PDF_PARSE_LOCK, pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass or document.page_count < 1:
                raise DownloadError("invalid_pdf", "PDF 已加密或没有可用页面。")
            document.load_page(0)
            document.load_page(document.page_count - 1)
            pages = document.page_count
    except DownloadError:
        raise
    except Exception:
        raise DownloadError("invalid_pdf", "PDF 解析器无法打开该文件。") from None
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "pages": pages}


def _read_cache(pdf_path: Path, manifest_path: Path, paper_id: str, max_bytes: int):
    try:
        if pdf_path.is_symlink() or manifest_path.is_symlink():
            return None
        if pdf_path.stat().st_size > max_bytes or manifest_path.stat().st_size > 65536:
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != 1 or manifest.get("paper_id") != paper_id:
            return None
        data = pdf_path.read_bytes()
        if len(data) > max_bytes:
            return None
        details = _pdf_details(data)
        if any(manifest.get(key) != details[key] for key in ("sha256", "bytes")):
            return None
        return {**details, "candidate": manifest.get("candidate")}
    except (OSError, ValueError, TypeError, AttributeError, DownloadError):
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pdf-download-", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _downloaded(result: Paper, path: Path) -> Paper:
    result.pdf_path = str(path)
    result.pdf_artifact_id = stable_artifact_id("pdf", result.paper_id)
    result.acquisition_status = "downloaded"
    if result.conversion_status is None:
        result.conversion_status = "not_attempted"
    result.status, result.error = "downloaded", None
    refresh_reading_status(result)
    return result


@asynccontextmanager
async def _source_gate(host: str, interval: float) -> AsyncIterator[None]:
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        # Shared with the search adapter: one request in flight, >=3 seconds
        # between starts, across clients and asyncio loops within this process.
        async with _REQUEST_GATE.request(max(3.0, interval)):
            yield
    else:
        yield


class Downloader:
    """Download service with injectable HTTP and DNS; all limits remain enforced."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = resolve_public_host,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.client = client
        self.resolver = resolver

    async def _target(self, raw: str) -> tuple[httpx.URL, dict[str, str], dict[str, object], str]:
        if len(raw) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise DownloadError("unsafe_url", "PDF 地址包含不支持的字符。")
        try:
            parsed = urlsplit(raw)
            url = httpx.URL(raw)
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except (ValueError, httpx.InvalidURL):
            raise DownloadError("unsafe_url", "PDF 地址格式不正确。") from None
        if url.scheme not in {"http", "https"} or not url.host:
            raise DownloadError("unsafe_url", "仅允许 HTTP(S) PDF 地址。")
        if parsed.username is not None or parsed.password is not None or url.userinfo:
            raise DownloadError("unsafe_url", "PDF 地址中不能包含账号或密码。")
        host = url.host.rstrip(".").lower()
        if (
            not host or "%" in host or host == "localhost"
            or host.endswith((".localhost", ".local", ".internal"))
        ):
            raise DownloadError("unsafe_url", "不允许访问本机或内网地址。")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        try:
            addresses = [str(literal)] if literal else list(await self.resolver(host, port))
        except (OSError, ValueError):
            raise DownloadError("dns_error", "PDF 域名解析失败。", retryable=True) from None
        if not addresses:
            raise DownloadError("dns_error", "PDF 域名没有可用地址。", retryable=True)
        normalized: list[str] = []
        for address in addresses:
            try:
                value = ipaddress.ip_address(address)
            except ValueError:
                raise DownloadError("unsafe_url", "PDF 域名解析返回了无效 IP。") from None
            # Reject mixed public/private answers too. An IPv4-mapped IPv6
            # address is checked as IPv4 to avoid an address-family bypass.
            effective = value.ipv4_mapped if isinstance(value, ipaddress.IPv6Address) else None
            if not (effective or value).is_global:
                raise DownloadError("unsafe_url", "PDF 域名解析到非公网地址。")
            normalized.append(str(value))
        # Pin the connection to the checked address to prevent DNS rebinding.
        # httpcore uses sni_hostname for TLS hostname verification as well as SNI.
        target = url.copy_with(host=normalized[0], fragment=None)
        authority = url.netloc.decode("ascii")
        tls_host = url.raw_host.decode("ascii")

        async def tls_trace(event: str, info: dict) -> None:
            # httpcore 1.x's CONNECT implementation does not propagate its
            # sni_hostname extension into tunnel TLS. Its trace callback receives
            # the actual start_tls kwargs before the handshake, so retain the
            # original certificate hostname without unpinning the CONNECT IP.
            if event == "proxy.start_tls.started":
                info["server_hostname"] = tls_host
            elif event == "connection.start_tls.started" and self.settings.http_proxy:
                proxy = urlsplit(self.settings.http_proxy)
                if proxy.scheme == "https" and proxy.hostname:
                    info["server_hostname"] = proxy.hostname

        return target, {"Host": authority}, {
            "sni_hostname": tls_host, "trace": tls_trace,
        }, host

    async def _fetch(self, client: httpx.AsyncClient, candidate: str, audit: dict) -> bytes:
        settings = self.settings
        current = candidate
        for redirect in range(settings.max_redirects + 1):
            target, host_header, extensions, host = await self._target(current)
            response: httpx.Response | None = None
            for attempt in range(settings.http_max_retries + 1):
                try:
                    async with _source_gate(host, settings.arxiv_request_interval):
                        audit["request_count"] += 1
                        async with client.stream(
                            "GET", target,
                            headers={
                                **host_header,
                                "User-Agent": "assignment-paper-agent/0.2 (public PDF)",
                                "Connection": "close",
                            },
                            extensions=extensions,
                            timeout=httpx.Timeout(settings.http_timeout, connect=5.0),
                            follow_redirects=False,
                        ) as response:
                            if response.status_code in _REDIRECT_STATUS:
                                break
                            if response.status_code in _RETRYABLE_STATUS:
                                raise DownloadError(
                                    "http_error", f"PDF 服务返回 HTTP {response.status_code}",
                                    retryable=True, status_code=response.status_code,
                                    retry_after=_retry_after(response),
                                )
                            if response.status_code != 200:
                                raise DownloadError(
                                    "access_unavailable" if response.status_code in _UNAVAILABLE_STATUS
                                    else "http_error",
                                    f"PDF 服务返回 HTTP {response.status_code}",
                                    status_code=response.status_code,
                                )
                            length = response.headers.get("Content-Length", "")
                            if length.isdigit() and int(length) > settings.max_pdf_bytes:
                                raise DownloadError("pdf_too_large", "PDF 超过允许的最大字节数。")
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(body) + len(chunk) > settings.max_pdf_bytes:
                                    raise DownloadError("pdf_too_large", "PDF 超过允许的最大字节数。")
                                body.extend(chunk)
                            return bytes(body)
                except httpx.RequestError as error:
                    failure = DownloadError(
                        "timeout" if isinstance(error, httpx.TimeoutException) else "network_error",
                        "PDF 请求失败。", retryable=True,
                        exception_type=type(error).__name__,
                    )
                except DownloadError as error:
                    failure = error
                if not failure.retryable or attempt >= settings.http_max_retries:
                    raise failure
                audit["retries"] += 1
                await asyncio.sleep(min(30.0, max(
                    failure.details.get("retry_after", 0.0),
                    settings.http_retry_backoff * (2**attempt),
                )))
            if response is None or response.status_code not in _REDIRECT_STATUS:
                raise DownloadError("network_error", "PDF 请求没有收到响应。")
            location = response.headers.get("Location")
            if not location:
                raise DownloadError("invalid_redirect", "PDF 重定向缺少 Location。")
            if redirect >= settings.max_redirects:
                raise DownloadError("too_many_redirects", "PDF 重定向次数超限。")
            current = urljoin(current, location)
            audit["redirects"] += 1
            audit["final_url"] = _display_url(current)
        raise DownloadError("too_many_redirects", "PDF 重定向次数超限。")

    async def download(self, paper: Paper) -> Paper:
        result = paper.model_copy(deep=True)
        started = time.monotonic()
        audit: dict = {
            "request_count": 0, "cache_hit": False, "candidate": None,
            "retries": 0, "redirects": 0, "errors": [],
        }
        result.metadata["downloads"] = audit
        settings = self.settings
        key = hashlib.sha256(paper.paper_id.encode("utf-8")).hexdigest()
        root = Path(settings.pdf_dir).resolve()
        cache_path, manifest_path = root / f"{key}.pdf", root / f"{key}.json"
        candidates = list(dict.fromkeys(
            value for value in [paper.pdf_url, *paper.pdf_urls]
            if isinstance(value, str) and value.strip()
        ))
        audit["candidate_count"] = len(candidates)
        audit["candidates_truncated"] = max(0, len(candidates) - _MAX_CANDIDATES)
        deadline = time.monotonic() + settings.download_timeout
        try:
            async with asyncio.timeout(settings.download_timeout):
                if settings.cache_enabled and settings.cache_mode != "off":
                    cached = await asyncio.to_thread(
                        _read_cache, cache_path, manifest_path, paper.paper_id, settings.max_pdf_bytes
                    )
                    if cached is not None:
                        audit.update(cached, cache_hit=True)
                        return _downloaded(result, cache_path)
                if not candidates:
                    raise DownloadError("no_open_access", "未找到可公开获取的 PDF 地址。")
                if self.client is None:
                    async with httpx.AsyncClient(
                        proxy=settings.http_proxy or None,
                        timeout=httpx.Timeout(settings.http_timeout, connect=5.0),
                        follow_redirects=False, trust_env=False,
                        limits=httpx.Limits(
                            max_connections=settings.download_concurrency,
                            max_keepalive_connections=0,
                        ),
                    ) as client:
                        return await self._candidates(result, candidates, client, audit, key, root, deadline)
                return await self._candidates(result, candidates, self.client, audit, key, root, deadline)
        except TimeoutError:
            self._record(result, audit, DownloadError(
                "download_timeout", "PDF 获取总时长超限（已尝试全部候选）。", retryable=True
            ))
        except DownloadError as error:
            self._record(result, audit, error)
        except OSError:
            self._record(result, audit, DownloadError("storage_error", "PDF 保存失败。"))
        finally:
            audit["duration_seconds"] = round(time.monotonic() - started, 6)
        preserved_pdf = False
        if result.pdf_path:
            preserved_pdf = await asyncio.to_thread(Path(result.pdf_path).is_file)
        if preserved_pdf:
            result.pdf_artifact_id = stable_artifact_id("pdf", result.paper_id)
        else:
            result.pdf_path = None
            result.pdf_artifact_id = None
        unavailable = bool(audit["errors"]) and all(
            error["code"] in {"no_open_access", "access_unavailable"} for error in audit["errors"]
        )
        result.acquisition_status = "unavailable" if unavailable else "failed"
        if result.conversion_status is None:
            result.conversion_status = "not_attempted"
        result.status = "no_open_access" if unavailable else "download_failed"
        result.error = audit["errors"][-1]["message"]
        refresh_reading_status(result)
        return result

    async def _candidates(self, result, candidates, client, audit, key, root, deadline):
        for candidate in candidates[:_MAX_CANDIDATES]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            audit["candidate"] = _display_url(candidate)
            audit["final_url"] = _display_url(candidate)
            try:
                async with asyncio.timeout(min(self.settings.download_candidate_timeout, remaining)):
                    data = await self._fetch(client, candidate, audit)
                details = await asyncio.to_thread(_pdf_details, data)
                cache_write = self.settings.cache_enabled and self.settings.cache_mode == "read_write"
                filename = f"{key}.pdf" if cache_write else f"{key}-{details['sha256'][:16]}.pdf"
                path = root / filename
                await asyncio.to_thread(_atomic_write, path, data)
                if cache_write:
                    manifest = {
                        "schema": 1, "paper_id": result.paper_id,
                        "candidate": audit["candidate"], **details,
                    }
                    await asyncio.to_thread(
                        _atomic_write, root / f"{key}.json",
                        json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
                    )
                audit.update(details)
                return _downloaded(result, path)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise
                self._record(result, audit, DownloadError(
                    "candidate_timeout", "候选 PDF 获取超时，已尝试下一个候选。", retryable=True))
            except DownloadError as error:
                self._record(result, audit, error)
        raise DownloadError("candidates_exhausted", "所有已知开放获取 PDF 候选均失败。")

    @staticmethod
    def _record(result: Paper, audit: dict, error: DownloadError) -> None:
        # The final aggregate marker must not hide the real candidate failures.
        if error.code == "candidates_exhausted":
            return
        info = ErrorInfo(
            code=error.code, message=str(error), stage="download", paper_id=result.paper_id,
            retryable=error.retryable,
            details={"candidate": audit["candidate"], **error.details},
        )
        result.errors.append(info)
        audit["errors"].append(info.model_dump(mode="json"))


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(30.0, max(0.0, float(response.headers.get("Retry-After", "0"))))
    except ValueError:
        return 0.0


async def download_pdf(
    paper: Paper,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> Paper:
    """Return a Paper copy with a real PDF or explicit per-paper failure state."""
    return await Downloader(settings=settings, client=client).download(paper)
