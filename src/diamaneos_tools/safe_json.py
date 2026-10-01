"""Strict JSON loading and schema checks for tools configuration.

Rejects duplicate keys, non-finite numbers, oversized or deeply nested
documents, private-looking keys, credential-bearing URLs and private keys.
Error messages never echo values from the document.
"""
import hashlib
import json
from pathlib import Path
import re

try:
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None

ROOT = Path(__file__).resolve().parents[2]
MAX_FILE_BYTES = 67108864
MAX_DEPTH = 24
MAX_NODES = 2000000
MAX_ERRORS = 40
FORBIDDEN_KEYS = {
    "password", "privatekey", "token", "secret", "seed", "mnemonic",
    "imei", "imsi", "iccid", "serial", "custody",
}


class JsonError(ValueError):
    """Safe diagnostic which never includes document-provided values."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JsonError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise JsonError("non-finite JSON number")


def _guard(value):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise JsonError("document exceeds structural limits")
        if isinstance(item, (dict, list)) and len(item) > MAX_NODES:
            raise JsonError("document exceeds structural limits")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise JsonError("JSON object keys must be strings")
                if re.sub(r"[-_]", "", key).lower() in FORBIDDEN_KEYS:
                    raise JsonError("private field is not allowed")
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif not isinstance(item, (str, int, float, bool, type(None))):
            raise JsonError("value is not JSON data")
        if isinstance(item, str):
            if len(item) > MAX_FILE_BYTES:
                raise JsonError("document exceeds string limit")
            if re.search(r"[a-z][a-z0-9+.-]*://[^\s/]*@", item, re.I):
                raise JsonError("credential-bearing URL is not allowed")
            if re.search(r"-----BEGIN (?:[A-Z ]* )?PRIVATE KEY-----", item):
                raise JsonError("private key material is not allowed")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise JsonError("document is not finite UTF-8 JSON") from None
    if len(encoded) > MAX_FILE_BYTES:
        raise JsonError("document exceeds byte limit")


def loads(data):
    if not isinstance(data, bytes):
        raise JsonError("JSON input must be bytes")
    if len(data) > MAX_FILE_BYTES:
        raise JsonError("document exceeds byte limit")
    try:
        value = json.loads(data.decode("utf-8"),
                           object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
    except JsonError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise JsonError("invalid UTF-8 JSON") from None
    _guard(value)
    return value


def load_json(path):
    try:
        with Path(path).open("rb") as stream:
            return loads(stream.read(MAX_FILE_BYTES + 1))
    except OSError:
        raise JsonError("unable to read JSON input") from None


def file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        raise JsonError("unable to hash input") from None


def schema_errors(value, filename):
    try:
        _guard(value)
    except JsonError as error:
        return [str(error)]
    if Draft7Validator is None:
        return ["missing jsonschema; install requirements-dev.txt in a virtual environment"]
    try:
        schema = json.loads((ROOT / "schemas" / filename).read_text(
            encoding="utf-8"))
        Draft7Validator.check_schema(schema)
    except (OSError, ValueError):
        return ["schema is unavailable or invalid"]
    errors = []
    for error in Draft7Validator(schema).iter_errors(value):
        # jsonschema messages may echo untrusted or private values.
        errors.append("schema constraint failed: " + str(error.validator))
        if len(errors) == MAX_ERRORS:
            break
    return errors
