# DiamaneOS Testing

## Stock hardware observations

The [stock hardware report](../reports-public/stock-capabilities.json) records
observed component results for one Fairphone 6 on the stated stock build.
It combines operator-observed stock diagnostics and ordinary app use with
selected ADB identity, charging and disposable-file transfer checks. Each
component has its own result; removable storage remains untested because no
spare test card was available.

The stock build remained unchanged and the bootloader remained locked.
Performance/battery measurements, custom-OS qualification, restoration and
unlock/relock acceptance require their own evidence. This report also does
not establish acceptance of the baseline collector CLI described below.

## Stock recovery inputs

The [stock-input inventory](../config/stock-inputs.json) binds the observed FP6
product/build to official factory-package URLs, exact byte sizes and published
SHA-256 values. The final Android 15 package and the EU Android 16 package
explicitly offered by the phone are verified recovery inputs: for each, two
complete reads reproduced Fairphone's outer hash, the full ZIP CRC passed,
required recovery members were present and all 76 files declared by the
embedded checksum list matched. The phone remains on its received Android 15
build. This is archive verification, not a successful update, restore or
relock.

Recovery copies must be read from two independent storage locations and match
the recorded byte count and SHA-256 before destructive work. Two directories on
one physical volume do not meet that requirement. The inventory records a
verified private primary copy plus an owner-confirmed independent cloud copy;
both cloud-copy SHA-256 values reproduced the manifest. A path or filename
never substitutes for content verification.

The official factory script wipes user data by default and requires both normal
and critical bootloader unlock. It also contains a fallback that continues when
no checksum utility is found; that fallback is prohibited by the project
recovery procedure. Verify the complete archive against the independently read
official hash and verify its embedded declared files before execution. The
regional Android 16 package must match the build explicitly offered by the
phone; do not infer EU/US selection from a maintainer's location. For this
device the observed offer is FP6.QREL.16.100.0, so the US 16.104.0 package is
excluded.

Raw partition bodies, per-partition device hashes and rollback-index values
were not available under the accepted locked, non-root capture. They remain
unknown. Package contents are separately derived inputs and must not be
misreported as device dumps.

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
unsupported-complete. Live capture (`--target`) remains UNRUN; standalone ADB
checks do not establish collector acceptance. Host bounds: 20s per adb call plus 256KB streaming byte cap
(byte-exact, invalid UTF-8 kept visible). Large traces/samples stay outside
git with hashes. Use the current full-suite command below; test counts are recorded in the acceptance evidence for the exact tree.

## Compatibility target (provisional, design only; no device evidence)

Target: GOS branch-17 proposal vs FP6 Android-16 vendor (UNPROVEN pairing);
launch API 35 (shipped Android 15, verify on device); custom API unresolved
until sync. High-risk requirements + fixtures in
`tests/requirements/early-risks.json` (9 risks, all UNRESOLVED-assigned);
coverage ledger in `tests/requirements/coverage.json` (full CDD enumeration
completes during release-gate integration and manual compatibility qualification). Suite revisions are bound when the compatibility harness is configured.
No custom-OS compatibility pass is claimed; no Google-private suites are assumed.

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
