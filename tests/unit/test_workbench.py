"""Workbench job lifecycle: trace events must never be lost to the terminal-status race."""

import asyncio
import json

from paper_agent.config import Settings
from paper_agent.models import PipelineResult
from paper_agent.storage import PipelineStore
from paper_agent.web import Workbench


def _runner(job: dict, settings: Settings, progress):
    async def run() -> dict:
        run = PipelineResult(idea=job["idea"], mode="demo", status="demo_completed", top_k=job["top_k"])
        store = PipelineStore(settings.output_dir)
        directory = store.directory(run.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "trace.jsonl").write_text("\n".join(json.dumps(item) for item in (
            {"stage": "main_agent_tool_call", "tool": "search_papers"},
            {"stage": "main_agent_tool_call", "tool": "generate_research_report"},
        )) + "\n", encoding="utf-8")
        store.save(run)
        progress(f"  status=demo_completed, run_id={run.run_id}")
        return run.model_dump(mode="json")

    return run()


def test_terminal_status_is_visible_only_after_all_trace_events(tmp_path):
    async def scenario() -> dict:
        settings = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "data")
        bench = Workbench(settings, root=tmp_path / "web", config_path=tmp_path / "web" / "config.json",
                          research_runner=_runner)
        session = bench.create_session()
        job = bench.submit(session["session_id"], {"idea": "agent memory", "query": "", "top_k": 3,
                                                   "mode": "demo", "execution_mode": "sequential"})
        await bench.tasks[job["job_id"]]
        return bench.jobs[job["job_id"]]

    job = asyncio.run(scenario())
    assert job["status"] == "completed"
    events = job["events"]
    last_trace = max(index for index, event in enumerate(events) if event["kind"] == "trace")
    final_status = max(index for index, event in enumerate(events)
                       if event["kind"] == "status" and event["data"].get("status") == "completed")
    assert final_status > last_trace
    tools = [event["data"]["tool"] for event in events
             if event["kind"] == "trace" and event["data"].get("stage") == "main_agent_tool_call"]
    assert tools == ["search_papers", "generate_research_report"]


def test_artifact_names_are_safe_for_desktop_open_export():
    assert Workbench._artifact_name("A/B: C?", ".pdf") == "A_B_ C_.pdf"
    assert Workbench._artifact_name("  ..  ", ".md") == "artifact.md"
    assert len(Workbench._artifact_name("x" * 300, ".md")) <= 143


def test_sync_run_reads_trace_before_run_json_exists(tmp_path):
    async def scenario() -> dict:
        settings = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "data")
        bench = Workbench(settings, root=tmp_path / "web", config_path=tmp_path / "web" / "config.json")
        job = {"job_id": "j1", "session_id": "s1", "idea": "agent memory", "query": "", "top_k": 3,
               "mode": "demo", "execution_mode": "sequential", "status": "running", "created_at": "",
               "updated_at": "", "run_id": "run1", "result": None, "events": [], "error": None,
               "artifacts": []}
        bench.jobs[job["job_id"]] = job
        directory = settings.output_dir / "run1"
        directory.mkdir(parents=True)
        (directory / "trace.jsonl").write_text(
            json.dumps({"stage": "selector", "event": "start"}) + "\n", encoding="utf-8")
        await bench._sync_run(job, settings)
        return job

    job = asyncio.run(scenario())
    assert [event["kind"] for event in job["events"]] == ["trace"]
    assert job["events"][0]["data"]["stage"] == "selector"


def test_sync_run_failure_cannot_strand_terminal_status_or_task_slot(tmp_path, monkeypatch):
    async def scenario() -> tuple[dict, Workbench]:
        settings = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "data")
        bench = Workbench(settings, root=tmp_path / "web", config_path=tmp_path / "web" / "config.json",
                          research_runner=_runner)

        async def broken(*_arguments):
            raise RuntimeError("trace directory vanished")

        monkeypatch.setattr(bench, "_sync_run", broken)
        session = bench.create_session()
        job = bench.submit(session["session_id"], {"idea": "agent memory", "query": "", "top_k": 3,
                                                   "mode": "demo", "execution_mode": "sequential"})
        await bench.tasks[job["job_id"]]
        return bench.jobs[job["job_id"]], bench

    job, bench = asyncio.run(scenario())
    assert job["status"] == "completed"
    assert not bench.tasks


def test_persistence_failure_still_releases_the_job_slot(tmp_path, monkeypatch):
    async def scenario() -> tuple[dict, Workbench]:
        settings = Settings(output_dir=tmp_path / "runs", data_dir=tmp_path / "data")
        bench = Workbench(settings, root=tmp_path / "web", config_path=tmp_path / "web" / "config.json",
                          research_runner=_runner)
        session = bench.create_session()
        job = bench.submit(session["session_id"], {"idea": "agent memory", "query": "", "top_k": 3,
                                                   "mode": "demo", "execution_mode": "sequential"})
        monkeypatch.setattr("paper_agent.web.atomic_json",
                            lambda *_arguments: (_ for _ in ()).throw(PermissionError(13, "locked")))
        await bench.tasks[job["job_id"]]
        return bench.jobs[job["job_id"]], bench

    job, bench = asyncio.run(scenario())
    assert job["status"] == "completed"
    assert not bench.tasks
