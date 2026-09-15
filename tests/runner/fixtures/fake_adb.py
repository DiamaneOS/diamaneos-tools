#!/usr/bin/env python3
"""Environment-controlled fake ADB used only by synthetic runner tests."""

import json
import os
from pathlib import Path
import sys
import time


def record(argv):
    path = os.environ.get("FAKE_ADB_LOG")
    if path:
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(argv) + "\n")


def main():
    argv = sys.argv[1:]
    record(argv)
    if argv == ["version"]:
        print("Android Debug Bridge version 1.0.41")
        print("Version synthetic-runner-fixture")
        return 0
    if argv == ["devices"]:
        print("List of devices attached")
        for entry in json.loads(os.environ.get("FAKE_ADB_DEVICES", "[]")):
            print(entry["serial"] + "\t" + entry.get("state", "device"))
        return 0
    if len(argv) >= 4 and argv[0] == "-s" and argv[2] == "shell":
        key = " ".join(argv[3:])
        state_value = os.environ.get("FAKE_ADB_REMOTE_STATE")
        state = Path(state_value) if state_value else None
        if argv[3] == "cat" and state is not None:
            delay = float(os.environ.get("FAKE_ADB_TEMP_READ_SLEEP", "0"))
            if delay:
                time.sleep(delay)
            if not state.exists():
                print("cat: no such file", file=sys.stderr)
                return 1
            sys.stdout.buffer.write(state.read_bytes())
            return 0
        if argv[3:5] == ["rm", "-f"] and state is not None:
            state.unlink(missing_ok=True)
            return 0
        if len(argv) >= 7 and argv[3:6] == ["test", "!", "-e"] and state is not None:
            return 0 if not state.exists() else 1
        responses_file = os.environ.get("FAKE_ADB_RESPONSES_FILE")
        if responses_file:
            with open(responses_file, encoding="utf-8") as stream:
                responses = json.load(stream)
        else:
            responses = json.loads(os.environ.get("FAKE_ADB_RESPONSES", "{}"))
        response = responses.get(key, {})
        if response.get("sleep"):
            time.sleep(response["sleep"])
        if response.get("stdout") is not None:
            sys.stdout.write(response["stdout"])
        if response.get("stderr") is not None:
            sys.stderr.write(response["stderr"])
        return int(response.get("returncode", 0))
    if len(argv) == 5 and argv[0] == "-s" and argv[2] == "push":
        state_value = os.environ.get("FAKE_ADB_REMOTE_STATE")
        if not state_value:
            print("fixture state path is unavailable", file=sys.stderr)
            return 1
        Path(state_value).write_bytes(Path(argv[3]).read_bytes())
        print("1 file pushed")
        return 0
    print("unsupported fake adb invocation", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
