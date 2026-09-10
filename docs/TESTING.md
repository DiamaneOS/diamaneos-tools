# DiamaneOS Testing (living doc; full harness arrives in hardware harness integration / compatibility harness integration)

## baseline capture baseline collectors (fixture-only, no hardware)

```sh
python3 -m unittest discover -s tests -t .
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
43 baseline + 3 CLI tests green; device runs stay UNRUN.

5 fixtures + fake-adb live-path tests prove the contract: valid→ok,
truncated/timeout/overflow→error (never averaged as zero),
missing-service→unsupported while bare-`unknown` operator values stay ok ,
sensitive→IMEI/IMSI/ICCID/EID/phone/account/MAC/serial redacted with context
preserved (full-report checked, not just case fields); ambiguous target
refuses before any adb command . Over-producers are killed at the byte
cap (termination proven, not just detected); failed captures keep partial
stdout+stderr evidence with hashes; device-gone stays error/partial, never
unsupported-complete. Live capture (`--target`) stays UNRUN until a real FP6
is connected. Host bounds: 20s per adb call plus 256KB streaming byte cap
(byte-exact, invalid UTF-8 kept visible). Large traces/samples stay outside
git with hashes. 38 baseline + 3 CLI tests green via `python3 -m unittest
discover -s tests -t .`.

## compatibility planning compatibility target (provisional, design only; no device evidence)

Target: GOS branch-17 proposal vs FP6 Android-16 vendor (UNPROVEN pairing);
launch API 35 (shipped Android 15, verify on device); custom API unresolved
until sync. High-risk requirements + fixtures in
`tests/requirements/early-risks.json` (9 risks, all UNRESOLVED-assigned);
coverage ledger in `tests/requirements/coverage.json` (full CDD enumeration
completes in release test gates / manual compatibility qualification). Suite revisions bind at compatibility harness integration harness setup.
No hardware pass claimed; no Google-private suites assumed.
