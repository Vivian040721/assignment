"""Shared model adapter. Owner 08; used by main and task agents.

Chat Completions compatible endpoint; no vendor SDK or GPU is required.
"""

import json
from typing import Any

import httpx

from paper_agent.config import Settings


class ChatClient:
    def __init__(self, settings: Settings):
        settings.require_llm()
        self.settings = settings

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout) as client:
            response = await client.post(
                self.settings.llm_base_url + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + self.settings.llm_api_key},
            )
        if response.is_error:
            raise RuntimeError(f"Model endpoint returned HTTP {response.status_code}")
        body = response.json()
        if not body.get("choices"):
            raise RuntimeError("Model endpoint returned no choices")
        choice = body["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise RuntimeError("Model response was truncated or filtered")
        return choice["message"]

    async def text(self, system: str, user: str) -> str:
        message = await self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        content = message.get("content") or ""
        if not content.strip():
            raise ValueError("Model returned empty text")
        return content

    async def json(self, system: str, user: str) -> dict:
        raw = await self.text(system + " Return only one JSON object, no code fences.", user)
        if raw.strip().startswith("```"):
            raw = "\n".join(raw.strip().splitlines()[1:-1])
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Model must return a JSON object")
        return parsed
