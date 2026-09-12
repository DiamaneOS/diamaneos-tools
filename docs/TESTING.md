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

## Baseline collector

The reusable test-host setup and isolation boundary are documented in the
[test-host deployment recipe](../deploy/test-host/README.md).

Fixture and dry-run checks do not touch hardware:

```sh
python3 -m unittest discover -s tests/baseline -t .
python3 src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
bin/diamaneos baseline capture --dry-run
```

Live command:

```sh
bin/diamaneos baseline capture --target <serial> --conditions "<env>" \
  --raw-dir <PRIVATE_ROOT>/runs/<run-id>/ --output report.json
```

Raw storage: per-run subdirectory, reuse refused, per-file sha256 in
evidence refs; without --raw-dir the run is ephemeral (not accepted
evidence). Public reports carry a device alias only; serials stay private.
Graphics capture is fixed to `dumpsys gfxinfo com.android.systemui`. An
unscoped `gfxinfo` query can enumerate enough installed-package state to
exceed the bounded collector output, while SystemUI provides a stable,
non-personal host-readiness target. The 256 KiB per-command cap still applies.
The full-suite command below includes baseline, CLI and endpoint validation
tests; device runs remain separate hardware evidence.

5 fixtures + fake-adb live-path tests prove the contract: valid→ok,
truncated/timeout/overflow→error (never averaged as zero),
missing-service→unsupported while bare-`unknown` operator values stay ok,
sensitive→IMEI/IMSI/ICCID/EID/phone/account/MAC/serial redacted with context
preserved (full-report checked, not just case fields); ambiguous target
refuses before any adb command. Over-producers are killed at the byte
cap (termination proven, not just detected); failed captures keep partial
stdout+stderr evidence with hashes; device-gone stays error/partial, never
unsupported-complete. At collector revision
`21ec91587ce47917cd92ab1c0d1277e26f643bef`, one stock Android 15 FP6 host
acceptance run produced five `ok` cases, one explicit `unsupported` service and
zero errors; its raw bundle and device identity remain private. That proves the
bounded read-only capture path, not custom-OS compatibility or comparative
performance. Standalone ADB checks do not establish collector acceptance. Host
bounds: 20s per adb call plus 256KB streaming byte cap
(byte-exact, invalid UTF-8 kept visible). Large traces/samples stay outside
git with hashes. Use the current full-suite command below; test counts are recorded in the acceptance evidence for the exact tree.

## Stock performance protocol and pilot

`config/baseline.json` is the consumed FP6 stock-performance and camera
protocol. Validate it without contacting a device or creating output:

```sh
bin/diamaneos baseline protocol validate
bin/diamaneos baseline pilot --dry-run
```

The connected pilot is shorter than the declared series and is always labelled
`PILOT_ONLY_NOT_BASELINE_EVIDENCE`. It checks an exact private device-role
mapping before ADB, requires the operator to confirm an unlocked phone and a
visible 50% brightness setting, and independently verifies the remaining
display, radio, SIM, battery and build controls. The live power-service reading
must also report an awake display; an earlier operator confirmation is not
treated as current state. A mismatch stops before the launch, frame or thermal
workload.

The live pilot records one cold and one warm launch for each bound stock app.
Cold requires a force-stopped package and Android's `COLD` launch state; warm
finishes the cold activity with BACK, verifies that its process remains
resident, and requires Android's `WARM` state. Both require a positive reported
time. The 10-second package-scoped Settings frame sample must record at least
one rendered frame per completed swipe. The remaining connected sequence is a
bounded 30-second four-worker CPU load plus 30-second cooldown, before/after
memory signals, and an ending battery/charging snapshot. It force-stops only
the named packages, never clears app data, and uses Android's existing thermal
policy. Current HAL battery/skin values enforce conservative stop thresholds;
read-only sysfs thermal-zone type/temp pairs are retained as additional raw
observations.

On the locked stock user build, `/proc/pressure/memory` is not readable by the
shell user. The pilot retains that permission failure as `UNSUPPORTED` and uses
bounded `dumpsys meminfo`, selected `/proc/vmstat` deltas, and run-bounded LMKD
and ActivityManager event logs. Activity kill events are not called LMKD kills
without corroborating evidence. Unsupported and failed measurements are never
converted to numeric zero.

Live connected-pilot shape (private values substituted by the operator):

```sh
bin/diamaneos baseline pilot \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --run-id <run-id> \
  --output <PRIVATE_ROOT>/baseline-runs \
  --ambient-start-c <room-thermometer-reading> \
  --operator-confirmed-display-50 \
  --operator-confirmed-unlocked \
  --conditions "<stock build, USB path and controlled setup>"
```

The command produces immutable private raw files with SHA-256 references and a
serial-redacted result. It covers only the connected pilot. The fixed-scene
camera pilot and physical-disconnect idle pilot remain explicit `NOT_RUN`
phases until their supervised operator steps are completed; a connected-pilot
pass does not complete FP6-022 or authorize an Android-version comparison.

## Staged device runner

The hardware runner consumes reviewed suites, requires an exact target plus a
private target-role map, and produces a checkpointed, schema-versioned run.
First validate the committed smoke plan without contacting ADB or writing an
output directory:

```sh
bin/diamaneos test run --suite smoke --dry-run
```

For a live read-only smoke run, create the private device map described in
[BUILD.md](BUILD.md), select the already authorized USB serial without printing
it, and run under the unprivileged test account:

```sh
bin/diamaneos test run \
  --suite smoke \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --evidence-kind real-device \
  --run-id <run-id> \
  --conditions "<build, USB, network, power and ambient setup>" \
  --output <PRIVATE_ROOT>/test-runs
```

`--stage` is repeatable when an intentional subset is needed. A subset can
exit successfully but is labelled `SELECTED`, with the full expected inventory
still present as `NOT_RUN`; it is not a complete-suite claim. Every case names
its stage, preconditions, oracle, installed build/firmware, duration, evidence
kind, status and redacted/raw references. Optional capabilities are `SKIP` with
a declared reason when genuinely unavailable; a missing required case is never
converted to a pass.

The runner is fail-stop. A timeout or output overflow is `HARNESS_ERROR`, a
device loss is `BLOCKED`, an oracle mismatch is `FAIL`, and later selected
cases remain `NOT_RUN`. On interruption, completed results and partial streams
survive. Repeat only the unresolved cases under a new immutable run ID:

```sh
bin/diamaneos test run \
  --suite smoke \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --evidence-kind real-device \
  --run-id <new-run-id> \
  --rerun-from <PRIVATE_ROOT>/test-runs/<prior-run-id>/result.json \
  --conditions "<repeat setup and any deliberate differences>" \
  --output <PRIVATE_ROOT>/test-runs
```

Retry input and raw hashes, the exact suite, role, and installed build identity
must still agree. Exit codes are `0` complete/explicit selected success, `2`
invalid arguments or data, `3` prerequisite/target/lock blocked, `4` test
rejection and `5` execution failure, timeout or interruption. The exit code
does not replace per-case completeness.

Destructive suites are a separate stage and additionally require
`--destructive` plus a private map entry with `disposable: true`. The v1 runner
contains no flash/wipe implementation: after enforcing those gates, an
`installer-runbook` case remains explicitly `BLOCKED` for the separate reviewed
operator action. Never change such a case to `PASS` merely because the gate was
accepted.

Runner contract tests are synthetic evidence, not hardware results:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover \
  -s tests/runner -t .
```

They cover multiple-device binding, wrong target, unavailable capability,
timeout, device loss, interruption/checkpoint, verified rerun selection,
immutable collisions and the destructive boundary.

FP6-034 hardware-harness acceptance used signed implementation commit
`1aee77342a504fe622c52f6925938d09b7ee0bed` on the accepted test host. The
read-only `smoke` suite ran on the locked stock Android 15 FP6 build
`FP6.QREL.15.176.0` (`VS21`, user build) as
`fp6-034-stock15-20260911T234004Z`. The schema-valid, complete report selected
and completed all six expected cases: five `PASS`, one reasoned `SKIP` for the
optional IMS dumpsys service, and no failure, harness error or unresolved
check. The sanitized report SHA-256 is
`c300c05ca6fcf787a2590aba19844a7d00cf53e397d35ff7a7b40733a56bb4e9`;
all 19 referenced private raw artifacts reproduced their recorded hashes.
This accepts the runner and this stock read-only evidence only. It is not a
custom-OS, compatibility, recovery, destructive-operation, release or signer
qualification, and raw diagnostics/device identifiers remain outside public
Git.

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
