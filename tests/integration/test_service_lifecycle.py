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


def test_demo_research_recovers_brief_delivery_and_report_through_api_v3(tmp_path):
    root = (tmp_path / "api-v3-user-data").resolve()
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
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            submitted = await client.post(endpoint["url"] + "/api/desktop/jobs", headers=headers, json={
                "idea": "证据契约的离线集成验证", "query": "", "top_k": 3,
                "mode": "demo", "execution_mode": "auto",
            })
            assert submitted.status_code == 202
            initial = submitted.json()
            assert initial["status"] == "pending"
            assert initial["delivery_status"] is None
            assert initial["requested_execution_mode"] == "auto"
            job_url = endpoint["url"] + f"/api/jobs/{initial['job_id']}"
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                response = await client.get(job_url, headers=headers)
                assert response.status_code == 200
                job = response.json()
                if job["status"] not in {"pending", "queued", "running"}:
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Offline demo research did not reach a terminal state")

            assert job["status"] == "completed"
            # Synthetic demo evidence may leave readable notes, never a formal result.
            assert job["delivery_status"] in {"preliminary_notes", "no_result"}
            assert job["result"]["delivery_status"] == job["delivery_status"]
            assert job["result"]["requested_execution_mode"] == "auto"
            assert job["result"]["brief"] is not None
            assert job["result"]["brief"]["next_step"]["action"]
            assert job["events"]
            report_id = job["result"]["brief"]["report_artifact_id"]
            assert report_id == job["result"]["report"]["report_artifact_id"]
            assert report_id
            artifact = await client.get(
                endpoint["url"] + f"/api/jobs/{job['job_id']}/artifacts/{report_id}/text",
                headers=headers,
            )
            assert artifact.status_code == 200
            assert artifact.json()["kind"] == "report"
            assert endpoint["token"] not in response.text + artifact.text
            stopped = await client.post(endpoint["url"] + "/api/service/shutdown", headers=headers)
            assert stopped.status_code == 200
        await asyncio.wait_for(daemon, timeout=8)

    asyncio.run(scenario())
