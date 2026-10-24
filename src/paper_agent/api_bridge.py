"""Small, private-boundary helpers for the Electron-to-Python HTTP bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_ARTIFACT_TEXT_BYTES = 2 * 1024 * 1024
_PUBLIC_ARTIFACT_SUFFIXES = {".pdf", ".md", ".json"}


class ArtifactAccessError(Exception):
    """An artifact is unknown, outside its job roots, or cannot be served."""

    def __init__(self, message: str, status: int):
        self.message = message
        self.status = status
        super().__init__(message)


def resolve_artifact(job: dict[str, Any], artifact_id: str) -> tuple[dict[str, Any], Path]:
    """Resolve only a registered artifact whose canonical path stays in its roots."""
    if not isinstance(artifact_id, str):
        raise ArtifactAccessError("文件不存在或尚未生成。", 404)
    paths = job.get("_artifact_paths", {})
    roots_value = job.get("_artifact_roots", [])
    if not isinstance(paths, dict) or not isinstance(roots_value, list):
        raise ArtifactAccessError("文件不存在或已移走。", 404)
    artifact = next((item for item in job.get("artifacts", [])
                     if isinstance(item, dict) and item.get("artifact_id") == artifact_id), None)
    filename = paths.get(artifact_id)
    if artifact is None or not isinstance(filename, str) or not filename:
        raise ArtifactAccessError("文件不存在或尚未生成。", 404)
    try:
        path = Path(filename).resolve(strict=True)
        # Optional output roots are created lazily. An unused directory must
        # not prevent reading a file under another valid, registered root.
        roots = [Path(root).resolve() for root in roots_value
                 if isinstance(root, str) and root]
    except (OSError, RuntimeError, ValueError):
        raise ArtifactAccessError("文件不存在或已移走。", 404) from None
    if (not path.is_file() or path.suffix.lower() not in _PUBLIC_ARTIFACT_SUFFIXES
            or not any(path.is_relative_to(root) for root in roots)):
        raise ArtifactAccessError("文件不存在或已移走。", 404)
    return artifact, path


def artifact_text(job: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    artifact, path = resolve_artifact(job, artifact_id)
    if artifact.get("kind") == "pdf" or path.suffix.lower() == ".pdf":
        raise ArtifactAccessError("PDF 文件不能作为文本读取。", 415)
    if path.suffix.lower() not in {".md", ".json"}:
        raise ArtifactAccessError("仅支持 Markdown 和 JSON 文本文件。", 415)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_ARTIFACT_TEXT_BYTES + 1)
    except OSError:
        raise ArtifactAccessError("文件不存在或已移走。", 404) from None
    truncated = len(raw) > MAX_ARTIFACT_TEXT_BYTES
    text = raw[:MAX_ARTIFACT_TEXT_BYTES].decode("utf-8", errors="replace")
    return {"text": text, "name": artifact.get("name", path.name),
            "kind": artifact.get("kind", ""), "truncated": truncated}


def artifact_location(job: dict[str, Any], artifact_id: str) -> dict[str, str]:
    artifact, path = resolve_artifact(job, artifact_id)
    return {"path": str(path), "name": str(artifact.get("name", path.name)),
            "kind": str(artifact.get("kind", ""))}


def redact_secrets(value: Any, secrets: tuple[str, ...] | list[str]) -> Any:
    """Return a JSON-compatible copy with configured secret values removed."""
    safe_secrets = tuple(secret for secret in secrets if isinstance(secret, str) and secret)
    if isinstance(value, str):
        for secret in safe_secrets:
            value = value.replace(secret, "[已隐藏]")
        return value
    if isinstance(value, dict):
        return {key: redact_secrets(item, safe_secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item, safe_secrets) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item, safe_secrets) for item in value]
    return value
