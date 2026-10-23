"""Regenerate core/dev dependency snapshot and shared JSON schema after review."""

import json
import subprocess
import sys
from pathlib import Path

from paper_agent.schemas import RunResult

root = Path(__file__).resolve().parents[1]
lines = subprocess.check_output(
    [sys.executable, "-m", "pip", "freeze", "--exclude-editable"], text=True
).splitlines()
lines = [
    line + '; sys_platform == "win32"' if line.lower().startswith("pywin32==") else line
    for line in lines
]
(root / "requirements-dev.lock").write_text(
    "# Core + dev snapshot; optional fulltext/semantic extras excluded.\n"
    + "\n".join(lines)
    + "\n",
    encoding="utf-8",
)
(root / "docs" / "contracts.schema.json").write_text(
    json.dumps(RunResult.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
