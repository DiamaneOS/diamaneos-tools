# DiamaneOS Testing

## Baseline collectors (fixture-only, no hardware)

```sh
python3 -m unittest discover -s tests/baseline -t .
python3 src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
bin/diamaneos baseline capture --dry-run
```

Live command (UNRUN on hardware; proven via fake-adb transport in
tests/baseline, 13 live-path tests):

```sh
bin/diamaneos baseline capture --target <serial> --conditions "<env>" \
  --raw-dir <PRIVATE_ROOT>/runs/<run-id>/ --output report.json
```

Raw storage: per-run subdirectory, reuse refused, per-file sha256 in
evidence refs; without --raw-dir the run is ephemeral (not accepted
evidence). Public reports carry a device alias only; serials stay private.
The full-suite command below includes baseline, CLI and endpoint validation tests; device runs stay UNRUN.

5 fixtures + fake-adb live-path tests prove the contract: valid→ok,
truncated/timeout/overflow→error (never averaged as zero),
missing-service→unsupported while bare-`unknown` operator values stay ok,
sensitive→IMEI/IMSI/ICCID/EID/phone/account/MAC/serial redacted with context
preserved (full-report checked, not just case fields); ambiguous target
refuses before any adb command. Over-producers are killed at the byte
cap (termination proven, not just detected); failed captures keep partial
stdout+stderr evidence with hashes; device-gone stays error/partial, never
unsupported-complete. Live capture (`--target`) stays UNRUN until a real FP6
is connected. Host bounds: 20s per adb call plus 256KB streaming byte cap
(byte-exact, invalid UTF-8 kept visible). Large traces/samples stay outside
git with hashes. Use the current full-suite command below; test counts are recorded in the acceptance evidence for the exact tree.

## Compatibility target (provisional, design only; no device evidence)

Target: GOS branch-17 proposal vs FP6 Android-16 vendor (UNPROVEN pairing);
launch API 35 (shipped Android 15, verify on device); custom API unresolved
until sync. High-risk requirements + fixtures in
`tests/requirements/early-risks.json` (9 risks, all UNRESOLVED-assigned);
coverage ledger in `tests/requirements/coverage.json` (full CDD enumeration
completes during release-gate integration and manual compatibility qualification). Suite revisions are bound when the compatibility harness is configured.
No hardware pass claimed; no Google-private suites assumed.

## Endpoint contracts

The baseline collector remains stdlib-only. Endpoint schema validation uses the
pinned development dependencies; install them once in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python bin/diamaneos endpoints validate
```

Ordinary tests use `tests/endpoints/fixtures/services.json` and work in a standalone
clone of tools. They never search for a neighbouring infrastructure checkout.
For acceptance of a real service selection, also run this separate integration
gate with its actual explicit path (substitute your checkout location):

```sh
.venv/bin/python bin/diamaneos endpoints validate \
  --services /absolute/path/to/infrastructure/config/services.json
```

The first command reports inventory-only scope; the second validates the actual
two-repository design. The tests exercise the same schema validator as the CLI:
required fields, null/wrong types, reference/ownership/isolation errors, bounded
input, duplicate JSON keys, Unicode byte limits and non-echoing privacy failures.
Wire-shape examples check 204/body/time-unit conventions; they do not simulate
native cryptography or prove device compatibility. Valid schema data still has
owned implementation gates and cannot be called an active deployment. See
[ENDPOINTS.md](ENDPOINTS.md) for their interpretation and maintenance rules.
