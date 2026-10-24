"""Qt-free discovery and lifecycle control for the independent local service."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

API_VERSION = 3


def default_data_root() -> Path:
    override = os.environ.get("PAPER_AGENT_DESKTOP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Assignment" / "PaperAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Assignment" / "PaperAgent"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Assignment" / "PaperAgent"


def descriptor_path(data_root: Path) -> Path:
    return data_root / "service" / "endpoint.json"


def read_descriptor(data_root: Path) -> dict | None:
    try:
        value = json.loads(descriptor_path(data_root).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        url = urlsplit(value["url"])
        if (isinstance(value.get("pid"), bool) or not isinstance(value.get("pid"), int)
                or value["pid"] <= 0
                or isinstance(value.get("api_version"), bool)
                or not isinstance(value.get("api_version"), int) or value["api_version"] <= 0
                or url.scheme != "http"
                or url.hostname != "127.0.0.1" or not url.port or url.path
                or url.username or url.password or url.query or url.fragment
                or not isinstance(value.get("token"), str) or len(value["token"]) < 32
                or any(char.isspace() for char in value["token"])
                or not isinstance(value.get("data_root"), str)
                or not Path(value["data_root"]).is_absolute()
                or Path(value["data_root"]).expanduser().resolve() != data_root.expanduser().resolve()
                or value.get("mcp_url") != value["url"].rstrip("/") + "/mcp"):
            return None
        return value
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None


def service_info(endpoint: dict) -> dict | None:
    """Fetch authenticated service metadata and verify its process and data root."""
    try:
        response = httpx.get(endpoint["url"] + "/api/service",
                             headers={"Authorization": "Bearer " + endpoint["token"]},
                             timeout=0.7, trust_env=False)
        if response.status_code != 200:
            return None
        info = response.json()
        if not isinstance(info, dict):
            return None
        pid, api_version = info.get("pid"), info.get("api_version")
        active_jobs = info.get("active_jobs")
        root = info.get("data_root")
        config = info.get("config_path")
        if (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
                or pid != endpoint.get("pid")
                or isinstance(api_version, bool) or not isinstance(api_version, int)
                or api_version != endpoint.get("api_version")
                or isinstance(active_jobs, bool) or not isinstance(active_jobs, int) or active_jobs < 0):
            return None
        # API v2's authenticated descriptor is tied to the requested data root,
        # but its service-info response predates the explicit root/config fields.
        # Keep that narrow compatibility path so an old live daemon is detected
        # and never shadowed by a second process.
        if api_version < API_VERSION:
            return info
        if (not isinstance(root, str)
                or Path(root).expanduser().resolve() != Path(endpoint["data_root"]).expanduser().resolve()
                or not isinstance(config, str)
                or Path(config).expanduser().resolve()
                != (Path(root).expanduser().resolve() / "config.json")):
            return None
        return info
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError, RuntimeError):
        return None


def service_is_ready(endpoint: dict) -> bool:
    return service_info(endpoint) is not None


def ensure_service(data_root: Path | None = None) -> dict:
    """Attach or spawn a detached daemon; never start a research task."""
    root = (data_root or default_data_root()).expanduser().resolve()
    endpoint = read_descriptor(root)
    if endpoint and service_is_ready(endpoint):
        if endpoint["api_version"] != API_VERSION:
            raise RuntimeError(
                "检测到不兼容的本地服务 (API {0})。请先等待任务完成，或通过当前运行的应用取消活动任务；"
                "再运行 scripts/manage-service.ps1 -Action stop 并重新打开应用。服务不会强制结束活动任务，"
                "研究历史和配置会保留。".format(endpoint["api_version"])
            )
        return endpoint
    if endpoint:
        # A descriptor can survive briefly while the previous daemon is
        # shutting down. Give it a grace period instead of racing the OS lock.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            time.sleep(0.25)
            latest = read_descriptor(root)
            if latest is None or latest.get("pid") != endpoint.get("pid"):
                break
            if service_is_ready(latest):
                return latest
    directory = root / "service"
    directory.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False):
        name = "PaperAgent-service.exe" if os.name == "nt" else "PaperAgent-service"
        executable = Path(sys.executable).with_name(name)
        if not executable.is_file():
            raise RuntimeError("独立服务程序缺失，请保留完整的 PaperAgent 程序目录。")
        command = [str(executable), "--service", "--data-root", str(root)]
    else:
        command = [sys.executable, "-X", "utf8", "-m", "paper_agent.service_daemon",
                   "--data-root", str(root)]
    options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    }
    with (directory / "service.log").open("ab") as log:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 close_fds=True,
                                 env=os.environ | {"PYINSTALLER_RESET_ENVIRONMENT": "1"}, **options)
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        endpoint = read_descriptor(root)
        if endpoint and endpoint["api_version"] == API_VERSION and service_is_ready(endpoint):
            return endpoint
        # Another concurrent starter may have won the service lock. Wait for
        # its descriptor even when this child exits; don't kill either process.
        if child.poll() not in (None, 0, 2):
            raise RuntimeError("独立服务启动失败，请查看资料目录 service/service.log。")
        time.sleep(0.2)
    raise RuntimeError("等待独立服务超时，请查看资料目录 service/service.log。")


def stop_service(data_root: Path | None = None) -> bool:
    root = (data_root or default_data_root()).expanduser().resolve()
    endpoint = read_descriptor(root)
    if not endpoint or not service_is_ready(endpoint):
        return False
    info = service_info(endpoint)
    active_jobs = info.get("active_jobs") if info else None
    if isinstance(active_jobs, bool) or not isinstance(active_jobs, int) or active_jobs < 0:
        raise RuntimeError("无法确认本地服务是否还有活动任务；服务未停止，请稍后重试。")
    if active_jobs:
        raise RuntimeError("仍有活动任务，服务未停止。请等待任务完成，或在应用中取消任务后再停止服务。")
    with httpx.Client(trust_env=False, timeout=10) as client:
        response = client.post(endpoint["url"] + "/api/service/shutdown",
                               headers={"Authorization": "Bearer " + endpoint["token"]})
        if response.status_code == 409:
            raise RuntimeError("仍有活动任务，服务未停止。请等待任务完成，或在应用中取消任务后再停止服务。")
        # 503 means another stop request already put the daemon into graceful
        # shutdown; waiting below is idempotent and avoids a false failure.
        if response.status_code != 503:
            response.raise_for_status()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        latest = read_descriptor(root)
        if latest is None or latest.get("pid") != endpoint["pid"]:
            return True
        time.sleep(0.2)
    raise RuntimeError("服务仍在停止中，请查看 service/service.log；未强制终止进程。")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the independent Paper Agent service")
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true",
                        help="print a machine-readable private endpoint for the native main process")
    args = parser.parse_args()
    root = (args.data_root or default_data_root()).expanduser().resolve()
    try:
        if args.action == "stop":
            stopped = stop_service(root)
            if args.json:
                print(json.dumps({"stopped": stopped}, separators=(",", ":")))
            else:
                print("Service stopped." if stopped else "Service is not running.")
            return 0
        endpoint = ensure_service(root) if args.action == "start" else read_descriptor(root)
        if not endpoint or not service_is_ready(endpoint):
            if args.json:
                print(json.dumps({"running": False}, separators=(",", ":")))
            else:
                print("Service is not running.")
            return 1
        if args.action == "start" and args.json:
            # This one explicit machine channel is consumed by Electron main;
            # normal CLI output and logs never contain the bearer token.
            print(json.dumps({key: endpoint[key] for key in (
                "url", "mcp_url", "pid", "api_version", "data_root", "token")},
                separators=(",", ":")))
        elif args.json:
            info = service_info(endpoint) or {}
            print(json.dumps({"running": True} | {
                key: info[key] for key in (
                    "version", "api_version", "pid", "active_jobs", "data_root", "config_path")
                if key in info
            }, separators=(",", ":")))
        else:
            print(f"MCP: {endpoint['mcp_url']}  PID: {endpoint['pid']}")
        return 0
    except (RuntimeError, httpx.HTTPError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
