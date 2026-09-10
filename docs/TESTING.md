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
