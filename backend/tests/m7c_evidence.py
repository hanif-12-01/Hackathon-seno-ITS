import hashlib
import json
import platform
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


CONTROL = {"manifest.json", "manifest.sha256"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(directory):
    directory = Path(directory)
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink not permitted: {path}")
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = sha256(path)
    return result


def output(directory, name, content):
    directory = Path(directory).resolve()
    if any((directory / control).exists() for control in CONTROL):
        raise ValueError("Run is finalized or finalization is incomplete")
    path = directory / name
    if Path(name).is_absolute() or ".." in Path(name).parts or path.parent != directory:
        raise ValueError("Output must be a direct child")
    with path.open("xb") as stream:
        stream.write(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def json_output(directory, name, value):
    return output(directory, name, json.dumps(value, indent=2, sort_keys=True) + "\n")


def create_run(root, run_id=None):
    run_id = run_id or "RUN-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex
    if not re.fullmatch(r"RUN-\d{8}T\d{12}Z-[0-9a-f]{32}", run_id):
        raise ValueError("Invalid UTC/UUID run ID")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / run_id
    directory.mkdir()
    return directory


def finalize(directory, metadata, schema="m7c-p0a-v1"):
    directory = Path(directory)
    if any((directory / name).exists() for name in CONTROL):
        raise FileExistsError("Finalization cannot be repeated")
    payload = {"schema": schema, "run_id": directory.name, "finalized_utc": utc(),
               "metadata": metadata, "files": files(directory)}
    manifest = json_output(directory, "manifest.json", payload)
    with (directory / "manifest.sha256").open("x", encoding="ascii") as stream:
        stream.write(sha256(manifest) + "  manifest.json\n")
    return verify(directory)


def verify(directory):
    directory = Path(directory)
    manifest = directory / "manifest.json"
    if (directory / "manifest.sha256").read_text(encoding="ascii") != sha256(manifest) + "  manifest.json\n":
        raise ValueError("Manifest SHA256 mismatch")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    actual = files(directory)
    for name in CONTROL:
        actual.pop(name)
    if payload["schema"] not in ("m7c-p0a-v1", "m7d-baseline-v1") or payload["run_id"] != directory.name or actual != payload["files"]:
        raise ValueError("Finalized run changed: content, deletion or addition")
    return sha256(manifest)


def inventory(root, excluded=()):
    root = Path(root).resolve()
    excluded = [Path(path).resolve() for path in excluded]
    result = {}
    for relative in ("backend/tests/logs", "frontend/evidence", "backend/artifacts", "data", "models"):
        for path in sorted((root / relative).rglob("*")):
            if any(path == item or item in path.parents for item in excluded) or "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                raise ValueError(f"Protected symlink: {path}")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def assert_inventory(root, expected, excluded=()):
    actual = inventory(root, excluded)
    if actual != expected:
        raise ValueError("Protected historical inventory changed")
    return len(actual)


def git_state(directory):
    def run(*args):
        try:
            return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain") if commit else None
    return {"available": commit is not None, "commit": commit, "dirty": bool(status) if status is not None else None}


def runtime_metadata(root):
    return {"versions": {"python": sys.version.split()[0], "platform": platform.platform()},
            "git": {name: git_state(Path(root) / sub) for name, sub in (("root", ""), ("backend", "backend"), ("frontend", "frontend"))},
            "environment": {"policy": "allowlist only; no inherited environment values recorded",
                            "PANGANNADI_DATABASE_URL": "isolated run-local SQLite",
                            "unset": ["M5A_EVIDENCE_DB", "M5A1_EVIDENCE_DIR", "M5B_EVIDENCE_DB", "M6_EVIDENCE_DB"]}}
