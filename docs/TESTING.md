# DiamaneOS Testing (living doc; full harness arrives in hardware harness integration / compatibility harness integration)

## baseline capture baseline collectors (fixture-only, no hardware)

```sh
python3 -m unittest discover -s tests/baseline -v
python3 src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
python3 src/diamaneos_tools/baseline.py --dry-run
```

5 fixtures prove the contract: valid→ok, truncated/timeout→error (never
averaged as zero), missing command→unsupported , sensitive→IMEI/serial/
ICCID redacted with context preserved ; ambiguous target refuses before any
adb command . Live capture (`--target`) stays UNRUN until a real FP6 is
connected. Host timeout bound: 20s per adb call (safety bound, not a device
claim). Large traces/samples stay outside git with hashes. CLI exposure as
`bin/diamaneos baseline capture` arrives with the first CLI-wiring task.

## compatibility planning compatibility target (provisional, design only; no device evidence)

Target: GOS branch-17 proposal vs FP6 Android-16 vendor (UNPROVEN pairing);
launch API 35 (shipped Android 15, verify on device); custom API unresolved
until sync. High-risk requirements + fixtures in
`tests/requirements/early-risks.json` (9 risks, all UNRESOLVED-assigned);
coverage ledger in `tests/requirements/coverage.json` (full CDD enumeration
completes in release test gates / manual compatibility qualification). Suite revisions bind at compatibility harness integration harness setup.
No hardware pass claimed; no Google-private suites assumed.
