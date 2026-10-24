"""One directory per run; JSON state survives separate tool invocations."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class PipelineStore:
    """Persist the current PipelineResult contract with safe legacy-compatible IDs."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"(?:\d{8}T\d{6}Z_[0-9a-f]{8}|[0-9a-f]{32})", run_id):
            raise ValueError("Invalid run_id")
        path = self.root / run_id
        path.mkdir(exist_ok=True)
        return path

    def save(self, run) -> None:
        from paper_agent.models import PipelineResult

        if not isinstance(run, PipelineResult):
            raise TypeError("PipelineStore only accepts PipelineResult")
        target = self.directory(run.run_id) / "run.json"
        temp = target.with_name("run." + uuid4().hex + ".tmp")
        temp.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        temp.replace(target)

    def load(self, run_id: str):
        from paper_agent.models import PipelineResult

        return PipelineResult.model_validate_json(
            (self.directory(run_id) / "run.json").read_text(encoding="utf-8")
        )

    def event(self, run_id: str, stage: str, **detail: Any) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(), "stage": stage, **detail}
        with (self.directory(run_id) / "trace.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_json(self, run_id: str, name: str, value: Any) -> Path:
        if Path(name).name != name:
            raise ValueError("Artifact name must be a basename")
        path = self.directory(run_id) / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
