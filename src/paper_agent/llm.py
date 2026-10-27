"""Async model adapter. Logs include counters, never prompts or server bodies."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from paper_agent.config import Settings

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class LLMError(RuntimeError):
    """A sanitized endpoint, response, or model-budget failure."""


class LLMBudgetError(LLMError):
    pass


class LLMOutputError(LLMError):
    """A sanitized output problem with a stable machine-readable reason."""

    def __init__(self, message: str, *, reason: str = "invalid_output"):
        super().__init__(message)
        self.reason = reason


@dataclass
class LLMUsage:
    logical_calls: int = 0
    requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    retries: int = 0
    invalid_outputs: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_missing_responses: int = 0

    @property
    def request_count(self) -> int:
        return self.requests

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _parse_json_object(raw: str) -> dict:
    """Parse exactly one JSON object; fences/prose may not hide another value."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.startswith("["):
        raise ValueError("Top-level JSON value must be an object")
    decoder = json.JSONDecoder()
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        start = text.find("{")
        if start < 0:
            raise
        prefix = text[:start].rstrip()
        if prefix.endswith("["):
            raise ValueError("JSON object must not be nested in an array")
        parsed, end = decoder.raw_decode(text[start:])
        remainder = text[start + end:]
        if remainder.lstrip().startswith("]"):
            raise ValueError("JSON object must not be nested in an array")
        # A deterministic prefix/suffix explanation is allowed, but a second
        # JSON object or array after the first value is ambiguous and rejected.
        scan = 0
        while True:
            positions = [index for index in (remainder.find("{", scan), remainder.find("[", scan))
                         if index >= 0]
            if not positions:
                break
            position = min(positions)
            try:
                value, _ = decoder.raw_decode(remainder[position:])
            except ValueError:
                scan = position + 1
                continue
            if isinstance(value, (dict, list)):
                raise ValueError("Multiple JSON values are not allowed")
            scan = position + 1
    if not isinstance(parsed, dict):
        raise ValueError("JSON value must be an object")
    return parsed


@runtime_checkable
class LLMClientProtocol(Protocol):
    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict: ...

    async def text(self, system: str, user: str) -> str: ...

    async def json(self, system: str, user: str) -> dict: ...


class ChatClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        settings.validate()
        settings.require_llm()
        url = urlsplit(settings.llm_base_url)
        if (
            url.scheme not in {"http", "https"} or not url.hostname
            or url.username or url.password or url.query or url.fragment
        ):
            raise ValueError("LLM_BASE_URL must be HTTP(S) without credentials or query")
        self.settings = settings
        # DeepSeek exposes the thinking toggle on its own OpenAI-compatible API.
        # Keep custom OpenAI-compatible endpoints free of provider-specific fields.
        self._is_deepseek = url.hostname.lower() == "api.deepseek.com" or url.hostname.lower().endswith(".deepseek.com")
        self._client = client
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(settings.llm_concurrency)
        self.usage = LLMUsage()
        self.call_log: list[dict[str, Any]] = []

    def _check_budget(self) -> None:
        if self.usage.requests >= self.settings.llm_max_calls:
            raise LLMBudgetError("LLM request budget exhausted")
        if self.usage.total_tokens >= self.settings.llm_max_total_tokens:
            raise LLMBudgetError("LLM observed token budget exhausted")

    async def chat(
        self, messages: list[dict], tools: list[dict] | None = None,
        *, response_format: dict[str, Any] | None = None,
    ) -> dict:
        """Return an assistant message including genuine tool_calls.

        Requests counts physical HTTP attempts. Token counts use the provider's
        usage response; missing usage is explicitly counted, never estimated.
        """
        if not messages:
            raise ValueError("At least one model message is required")
        self.usage.logical_calls += 1
        async with self._semaphore:
            self._check_budget()
            payload: dict[str, Any] = {
                "model": self.settings.llm_model, "messages": messages, "temperature": 0,
                "max_tokens": min(
                    self.settings.llm_max_output_tokens,
                    self.settings.llm_max_total_tokens - self.usage.total_tokens,
                ),
            }
            if self._is_deepseek:
                payload["thinking"] = {
                    "type": "enabled" if self.settings.llm_thinking_enabled else "disabled"
                }
            if tools:
                payload.update(tools=tools, tool_choice="auto")
            if response_format:
                payload["response_format"] = response_format
            if self._client is not None:
                return await self._request(self._client, payload)
            async with httpx.AsyncClient(
                timeout=self.settings.llm_timeout, proxy=self.settings.http_proxy or None,
                follow_redirects=False,
            ) as client:
                return await self._request(client, payload)

    async def _request(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> dict:
        for attempt in range(self.settings.llm_max_retries + 1):
            self._check_budget()
            self.usage.requests += 1
            entry: dict[str, Any] = {
                "request_number": self.usage.requests, "attempt": attempt + 1, "status": "pending"
            }
            self.call_log.append(entry)
            retry_after = 0.0
            try:
                response = await client.post(
                    self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                    json=payload,
                    headers={"Authorization": "Bearer " + self.settings.llm_api_key},
                    timeout=self.settings.llm_timeout, follow_redirects=False,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self.usage.failed_requests += 1
                entry.update(status="transport_error", error_type=type(exc).__name__)
                if attempt >= self.settings.llm_max_retries:
                    raise LLMError("Model request failed after bounded transport retries") from None
            else:
                entry["http_status"] = response.status_code
                if response.status_code >= 300:
                    self.usage.failed_requests += 1
                    entry["status"] = "http_error"
                    retryable = response.status_code in {408, 429} or response.status_code >= 500
                    if not retryable or attempt >= self.settings.llm_max_retries:
                        raise LLMError(f"Model endpoint returned HTTP {response.status_code}") from None
                    try:
                        retry_after = max(0.0, float(response.headers.get("Retry-After", "0")))
                    except ValueError:
                        pass
                else:
                    try:
                        body = response.json()
                    except (ValueError, UnicodeDecodeError):
                        self.usage.failed_requests += 1
                        entry["status"] = "invalid_response"
                        raise LLMOutputError("Model endpoint returned invalid JSON",
                                             reason="invalid_json_response") from None
                    self._record_tokens(body, entry)
                    try:
                        message = self._message(body)
                    except LLMOutputError:
                        self.usage.failed_requests += 1
                        entry["status"] = "invalid_response"
                        raise
                    self.usage.successful_requests += 1
                    entry["status"] = "success"
                    return message
            self.usage.retries += 1
            delay = min(
                self.settings.llm_max_backoff,
                max(self.settings.llm_retry_backoff * 2**attempt, retry_after),
            )
            await self._sleep(delay)
        raise AssertionError("Retry loop must return or raise")

    def _record_tokens(self, body: Any, entry: dict[str, Any]) -> None:
        usage = body.get("usage") if isinstance(body, dict) else None
        if not isinstance(usage, dict):
            self.usage.usage_missing_responses += 1
            return
        counts = {}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(name)
            counts[name] = value if isinstance(value, int) and value >= 0 else 0
        if "total_tokens" not in usage:
            counts["total_tokens"] = counts["prompt_tokens"] + counts["completion_tokens"]
        for name, value in counts.items():
            setattr(self.usage, name, getattr(self.usage, name) + value)
        entry["usage"] = counts

    @staticmethod
    def _message(body: Any) -> dict:
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMOutputError("Model endpoint returned no choices", reason="missing_choices")
        choice = choices[0]
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise LLMOutputError("Model response was truncated or filtered",
                                 reason="output_truncated")
        message = choice.get("message")
        if not isinstance(message, dict):
            raise LLMOutputError("Model endpoint returned no assistant message",
                                 reason="missing_message")
        if not message.get("content") and not message.get("tool_calls"):
            raise LLMOutputError("Model returned empty content and no tool calls",
                                 reason="empty_content")
        # Return the complete assistant message. In DeepSeek thinking mode this
        # includes reasoning_content, which must be echoed in later tool turns.
        return message

    async def text(self, system: str, user: str) -> str:
        message = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            self.usage.invalid_outputs += 1
            raise LLMOutputError("Model returned no text content", reason="empty_text")
        return content

    async def json(self, system: str, user: str) -> dict:
        message = await self.chat(
            [
                {"role": "system", "content": system + " Return one JSON object, no fences."},
                {"role": "user", "content": user},
            ], response_format={"type": "json_object"},
        )
        raw = message.get("content")
        if not isinstance(raw, str):
            self.usage.invalid_outputs += 1
            raise LLMOutputError("Model returned no JSON text", reason="missing_json_text")
        try:
            parsed = _parse_json_object(raw)
        except (ValueError, TypeError):
            self.usage.invalid_outputs += 1
            raise LLMOutputError("Model text was not valid JSON",
                                 reason="json_parse_failed") from None
        return parsed

    async def structured(
        self, system: str, user: str, response_model: type[ResponseModel]
    ) -> ResponseModel:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        parsed = await self.json(system + " Required JSON schema: " + schema, user)
        try:
            return response_model.model_validate(parsed)
        except ValidationError:
            self.usage.invalid_outputs += 1
            raise LLMOutputError("Model JSON did not satisfy the response schema",
                                 reason="schema_mismatch") from None
