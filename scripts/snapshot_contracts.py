"""Regenerate the Python core/dev snapshot and shared JSON schema after review.

Qt and watchfiles are legacy desktop dependencies. Filter them explicitly so a
snapshot from an older local environment cannot add them back to the lock file.
Keep reviewed Python-version pins: pip freeze only describes this interpreter.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from paper_agent.models import PipelineResult

root = Path(__file__).resolve().parents[1]
lines = subprocess.check_output(
    [sys.executable, "-m", "pip", "freeze", "--exclude-editable"], text=True
).splitlines()
legacy_desktop_packages = {"watchfiles"}


def canonical_distribution(line: str) -> str:
    name = line.split("==", 1)[0].split("@", 1)[0].strip()
    return re.sub(r"[-_.]+", "-", name).lower()


lock_path = root / "requirements-dev.lock"
python_version_pins = [
    line for line in lock_path.read_text(encoding="utf-8").splitlines()
    if not line.lstrip().startswith("#") and ";" in line
    and re.search(r"\bpython_(?:full_)?version\b", line.split(";", 1)[1])
] if lock_path.exists() else []
version_pinned_packages = {canonical_distribution(line) for line in python_version_pins}

lines = [
    line for line in lines
    if canonical_distribution(line) not in legacy_desktop_packages
    and canonical_distribution(line) not in version_pinned_packages
    and not canonical_distribution(line).startswith(("pyside", "pyqt", "shiboken"))
]
lines = [
    line + '; sys_platform == "win32"' if line.lower().startswith("pywin32==") else line
    for line in lines
]
lines = sorted(lines + python_version_pins, key=str.lower)
lock_path.write_text(
    "# MCP v2 core + PDF + dev snapshot; optional semantic ranker excluded.\n"
    + "\n".join(lines)
    + "\n",
    encoding="utf-8",
)
(root / "docs" / "contracts.schema.json").write_text(
    json.dumps(PipelineResult.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
