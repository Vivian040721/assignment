import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import BaseModel

from paper_agent.config import Settings
from paper_agent.llm_client import ChatClient, LLMBudgetError, LLMError, LLMOutputError


def settings(**kwargs):
    return replace(
        Settings(llm_base_url="https://model.test/v1", llm_api_key="test-secret", llm_model="mock"),
        **kwargs,
    )


def completion(content="text", **kwargs):
    return {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}, **kwargs,
    }


def test_structured_json_and_injected_transport_not_closed():
    class Output(BaseModel):
        result: str

    async def run():
        def respond(request):
            payload = json.loads(request.content)
            assert payload["response_format"] == {"type": "json_object"}
            assert payload["model"] == "mock"
            return httpx.Response(200, json=completion('{"result":"valid"}'))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            llm = ChatClient(settings(), client=http)
            output = await llm.structured("system", "user", Output)
            assert output.result == "valid"
            assert not http.is_closed
            assert llm.usage.total_tokens == 14
            assert llm.usage.request_count == 1
            assert "test-secret" not in repr(llm.call_log)
    asyncio.run(run())


def test_rate_limit_retry_count_and_backoff():
    async def run():
        calls, delays = [], []

        async def fake_sleep(delay):
            delays.append(delay)

        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "2"}, text="sensitive error")
            return httpx.Response(200, json=completion())

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            llm = ChatClient(settings(), client=http, sleep=fake_sleep)
            assert await llm.text("s", "u") == "text"
            assert len(calls) == 2
            assert delays == [2.0]
            assert llm.usage.requests == 2
            assert llm.usage.failed_requests == 1
            assert llm.usage.retries == 1
            assert "sensitive error" not in repr(llm.call_log)
    asyncio.run(run())


def test_auth_failure_sanitized_and_never_retried():
    async def run():
        def respond(request):
            return httpx.Response(401, text="test-secret and private provider details")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            llm = ChatClient(settings(), client=http)
            with pytest.raises(LLMError, match="HTTP 401") as failure:
                await llm.text("s", "u")
            assert "test-secret" not in str(failure.value)
            assert llm.usage.requests == 1
            assert llm.usage.retries == 0
    asyncio.run(run())


def test_transport_retries_are_bounded():
    async def run():
        async def fake_sleep(delay):
            pass
        def respond(request):
            raise httpx.ReadTimeout("test-secret", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            llm = ChatClient(settings(llm_max_retries=1), client=http, sleep=fake_sleep)
            with pytest.raises(LLMError, match="bounded") as failure:
                await llm.text("s", "u")
            assert "test-secret" not in str(failure.value)
            assert llm.usage.requests == 2
            assert llm.usage.failed_requests == 2
    asyncio.run(run())


@pytest.mark.parametrize("content", ["not json", "[]"])
def test_invalid_structured_response_not_silently_accepted(content):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion(content)))
        ) as http:
            llm = ChatClient(settings(), client=http)
            with pytest.raises(LLMOutputError):
                await llm.json("s", "u")
            assert llm.usage.invalid_outputs == 1
            assert llm.usage.requests == 1
    asyncio.run(run())


def test_tool_calls_preserved_and_request_budget_shared():
    async def run():
        tool_calls = [{"id": "call_1", "type": "function", "function": {
            "name": "search_papers", "arguments": '{"query":"memory"}'
        }}]
        body = completion(None)
        body["choices"][0]["message"]["tool_calls"] = tool_calls
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        ) as http:
            llm = ChatClient(settings(llm_max_calls=1), client=http)
            message = await llm.chat([{"role": "user", "content": "research"}], tools=[{
                "type": "function", "function": {"name": "search_papers", "parameters": {}}
            }])
            assert message["tool_calls"] == tool_calls
            with pytest.raises(LLMBudgetError):
                await llm.chat([{"role": "user", "content": "more"}])
            assert llm.usage.requests == 1
    asyncio.run(run())
