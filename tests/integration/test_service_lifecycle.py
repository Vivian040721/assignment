"""Loopback service lifecycle without .env loading or live network work."""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from paper_agent.config import Settings
from paper_agent.service_control import descriptor_path
from paper_agent.service_daemon import serve


def test_isolated_service_publishes_private_endpoint_and_stops_cleanly(tmp_path):
    root = (tmp_path / "isolated-user-data").resolve()
    settings = Settings(mode="demo", data_dir=root, output_dir=root / "test-runs",
                        llm_api_key="lifecycle-test-placeholder",
                        openalex_api_key="lifecycle-openalex-placeholder")

    async def scenario():
        daemon = asyncio.create_task(serve(root, 0, settings=settings))
        descriptor_file = descriptor_path(root)
        deadline = time.monotonic() + 8
        endpoint = None
        while time.monotonic() < deadline:
            if descriptor_file.is_file():
                try:
                    endpoint = json.loads(descriptor_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    endpoint = None
                if endpoint:
                    break
            if daemon.done():
                await daemon
            await asyncio.sleep(0.05)
        assert endpoint is not None
        headers = {"Authorization": "Bearer " + endpoint["token"]}
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            info_response = await client.get(endpoint["url"] + "/api/service", headers=headers)
            assert info_response.status_code == 200
            info = info_response.json()
            assert info == {
                "version": "0.2.2", "api_version": 3, "pid": endpoint["pid"],
                "active_jobs": 0, "data_root": str(root), "config_path": str(root / "config.json"),
            }
            unauthenticated = await client.get(endpoint["url"] + "/api/service")
            assert unauthenticated.status_code == 401
            stopped = await client.post(endpoint["url"] + "/api/service/shutdown", headers=headers)
            assert stopped.status_code == 200
        await asyncio.wait_for(daemon, timeout=8)
        assert not descriptor_file.exists()

    asyncio.run(scenario())
