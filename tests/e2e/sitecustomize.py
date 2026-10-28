"""Test-only service_control shim; active solely with PAPER_AGENT_E2E_MOCK=1."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if os.environ.get("PAPER_AGENT_E2E_MOCK") == "1":
    descriptor = Path(os.environ["PAPER_AGENT_E2E_DESCRIPTOR"])
    root = Path(os.environ["PAPER_AGENT_E2E_ROOT"])
    node = os.environ["PAPER_AGENT_E2E_NODE"]
    data_root = os.environ["PAPER_AGENT_DESKTOP_DATA_DIR"]
    log_path = descriptor.with_suffix(".log")
    with log_path.open("ab") as log:
        options = {
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": log,
            "cwd": str(root),
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(
            [node, str(root / "tests" / "e2e" / "mock-service.mjs"),
             "--descriptor", str(descriptor), "--data-root", data_root],
            **options,
        )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if descriptor.exists():
            try:
                value = json.loads(descriptor.read_text(encoding="utf-8"))
                print(json.dumps(value), flush=True)
                os._exit(0)
            except (OSError, json.JSONDecodeError):
                pass
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        process.terminate()
    print("Mock HTTP service did not start; see " + str(log_path), file=sys.stderr, flush=True)
    os._exit(1)
