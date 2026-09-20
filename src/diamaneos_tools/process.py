"""POSIX child ownership with bounded streams and process-group cleanup."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


def _signal_group(process, signum):
    # Darwin can report EPERM while the group's last member is a zombie.
    # Reap our leader and retry that race; never suppress a persistent denial.
    for attempt in range(3):
        process.poll()
        try:
            os.killpg(process.pid, signum)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.005)
    return False


def terminate_group(process, grace_seconds=0.25):
    """Stop the owned group even when its original leader has already exited."""
    if _signal_group(process, signal.SIGTERM):
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline and _signal_group(process, 0):
            time.sleep(0.01)
        _signal_group(process, signal.SIGKILL)
    process.wait()


def run(argv, timeout_seconds, max_output_bytes=262_144, *, cwd=None,
        env=None, input_data=None, log_path=None, capture_bytes=None):
    """Return bytes and one transport status; never return a live owned child.

    Small commands retain their bounded streams. With log_path, both streams
    are merged and streamed to a private exclusive file; capture_bytes may keep
    only a bounded tail for diagnostics. Output limits still apply to the full
    stream, not just the retained tail. stdin is a temporary file, so input and
    output cannot deadlock each other. Callers own signal-handler policy.
    """
    if timeout_seconds <= 0 or max_output_bytes < 1:
        raise ValueError("process bounds must be positive")
    keep = max_output_bytes if capture_bytes is None else capture_bytes
    if not 0 < keep <= max_output_bytes:
        raise ValueError("invalid process capture bound")
    started = time.monotonic()
    buffers = [bytearray(), bytearray()]
    totals = [0, 0]
    status = None
    process = None
    log = None
    with tempfile.TemporaryFile() as stdin:
        if input_data is not None:
            stdin.write(input_data)
            stdin.seek(0)
        try:
            if log_path is not None:
                fd = os.open(Path(log_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
                log = os.fdopen(fd, "wb")
            process = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if log else subprocess.PIPE,
                start_new_session=True)
            streams = [s for s in (process.stdout, process.stderr) if s is not None]
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
            eof = set()
            deadline = started + timeout_seconds
            while True:
                for index, stream in enumerate(streams):
                    if index in eof:
                        continue
                    try:
                        chunk = os.read(stream.fileno(), min(65_536, max_output_bytes + 1 - totals[index]))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        eof.add(index)
                        continue
                    totals[index] += len(chunk)
                    if log:
                        log.write(chunk[:max(0, max_output_bytes - (totals[index] - len(chunk)))])
                    buffers[index].extend(chunk)
                    if len(buffers[index]) > keep:
                        if capture_bytes is None:
                            del buffers[index][keep:]
                        else:
                            del buffers[index][:-keep]
                    if totals[index] > max_output_bytes:
                        status = "overflow"
                        break
                if status:
                    break
                if process.poll() is not None and len(eof) == len(streams):
                    status = "ok" if process.returncode == 0 else "error"
                    break
                if time.monotonic() >= deadline:
                    status = "timeout"
                    break
                time.sleep(0.005)
        except FileNotFoundError:
            status = "tool-missing"
        except KeyboardInterrupt:
            status = "interrupted"
        except OSError:
            status = "error"
        finally:
            if process is not None:
                terminate_group(process)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            if log:
                log.flush()
                os.fsync(log.fileno())
                log.close()
    result = {
        "transport": status,
        "stdout": bytes(buffers[0]), "stderr": bytes(buffers[1]),
        "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
    }
    if process is not None:
        result["returncode"] = process.returncode
    if status != "ok":
        result["reason"] = {
            "tool-missing": "required executable is unavailable",
            "timeout": "command exceeded its timeout",
            "overflow": "command exceeded its output limit",
            "interrupted": "run interrupted while command was active",
        }.get(status, "command did not complete successfully")
    return result


def text_result(result):
    return {**result, **{name: result[name].decode("utf-8", "replace")
                        for name in ("stdout", "stderr")}}


def run_bounded(argv, timeout_seconds, max_output_bytes=262_144):
    """Text-result adapter retaining the device runner's interruption contract."""
    from .errors import CommandInterrupted
    result = text_result(run(argv, timeout_seconds, max_output_bytes))
    if result["transport"] == "interrupted":
        raise CommandInterrupted(result)
    return result
