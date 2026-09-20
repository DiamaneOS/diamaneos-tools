"""Bounded private evidence IO and provenance for host workflows."""
import hashlib
import json
import os
from pathlib import Path
import re
from .errors import RunnerError
from .process import run_bounded
MAX_CASE_OUTPUT_BYTES = 1_048_576

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def read_bounded(path: Path, cap: int) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(cap + 1)
    except OSError as exc:
        raise RunnerError("input is unreadable", 2) from exc
    if len(data) > cap:
        raise RunnerError("input exceeds its byte limit", 2)
    return data

def load_unique_json(path: Path, cap: int) -> tuple[object, bytes]:
    data = read_bounded(path, cap)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("non-finite JSON number")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=invalid_constant), data
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RunnerError("input is not valid unique-key UTF-8 JSON", 2) from exc

def atomic_json(path: Path, value: dict):
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass

def sync_directory(path: Path):
    """Best-effort directory durability where the selected filesystem allows it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)

def write_evidence(path: Path, content: str) -> str:
    data = content.encode("utf-8", "replace")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return sha256_bytes(data)

def verify_evidence_refs(report_path: Path, refs: list) -> None:
    root = report_path.parent.resolve()
    for ref in refs:
        if not isinstance(ref, str) or "@sha256:" not in ref:
            raise RunnerError("retry report has an invalid evidence reference", 2)
        relative, digest = ref.rsplit("@sha256:", 1)
        relpath = Path(relative)
        if (relpath.is_absolute() or ".." in relpath.parts
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise RunnerError("retry report has an invalid evidence reference", 2)
        evidence_path = (root / relpath).resolve()
        try:
            evidence_path.relative_to(root)
        except ValueError as exc:
            raise RunnerError("retry evidence escapes its run directory", 2) from exc
        data = read_bounded(evidence_path, MAX_CASE_OUTPUT_BYTES)
        if sha256_bytes(data) != digest:
            raise RunnerError("retry evidence hash mismatch", 2)

def git_revision(repo_root: Path, executor=run_bounded) -> str:
    result = executor(["git", "-C", str(repo_root), "rev-parse", "HEAD"], 10)
    if result.get("transport") != "ok":
        return "unknown"
    value = result.get("stdout", "").strip()
    return value if re.fullmatch(r"[0-9a-f]{40,64}", value) else "unknown"
