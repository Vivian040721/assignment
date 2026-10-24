"""A snapshot from Python 3.13 must preserve reviewed Python 3.11 pins."""

import runpy
import subprocess
from pathlib import Path

from packaging.requirements import Requirement


def test_snapshot_preserves_python_version_pins(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    script = tmp_path / "scripts" / "snapshot_contracts.py"
    script.parent.mkdir()
    script.write_text((root / "scripts" / script.name).read_text(encoding="utf-8"),
                      encoding="utf-8")
    (tmp_path / "docs").mkdir()
    lock_path = tmp_path / "requirements-dev.lock"
    lock_path.write_text((root / lock_path.name).read_text(encoding="utf-8"),
                         encoding="utf-8")
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: (
        "networkx==3.7\nnumpy==2.5.4\npywin32==312\n"
        "watchfiles==1.0.0\nPySide6==6.8.0\npytest==9.1.1\n"
    ))

    runpy.run_path(str(script), run_name="__main__")

    requirements = [Requirement(line) for line in lock_path.read_text().splitlines()
                    if line and not line.startswith("#")]
    for python_version, expected in (
        ("3.11", {"networkx": "==3.6.1", "numpy": "==2.4.6"}),
        ("3.12", {"networkx": "==3.7", "numpy": "==2.5.4"}),
        ("3.13", {"networkx": "==3.7", "numpy": "==2.5.4"}),
    ):
        selected = [r for r in requirements if r.name in expected and r.marker.evaluate(
            {"python_version": python_version})]
        assert len(selected) == 2
        assert {r.name: str(r.specifier) for r in selected} == expected
    assert all(r.name not in {"watchfiles", "PySide6"} for r in requirements)
    windows = next(r for r in requirements if r.name == "pywin32")
    assert windows.marker.evaluate({"sys_platform": "win32"})
    assert not windows.marker.evaluate({"sys_platform": "linux"})
