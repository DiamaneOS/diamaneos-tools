# diamaneos-tools

Host tooling and machine-readable project maps for DiamaneOS (FP6, GrapheneOS-derived).

## Layout

- `WORK_ROOT` = a caller-selected parent directory for related checkouts
- `TOOLS_ROOT` = `WORK_ROOT/tools` (this repo)
- `PRIVATE_ROOT` = a caller-selected private evidence directory (outside all repos; serials, raw logs, tester data; never signing secrets)
- `OFFLINE_ROOT` lives only on the offline signer, never on a development workstation

## Setup

Use Git, Python 3 and the official Android platform tools. Record versions alongside reproducible test results.

```sh
git --version
python3 --version
adb --version
fastboot --version
```

Missing platform-tools only from the official Google distribution.
Full OS sync/build uses the reproducible Linux builder (Linux builder setup / reproducible Linux builds).

## Use

See `CONTRIBUTING.md` for the issue/PR handoff template.
`config/repositories.json` is the single repo/path map (`codeberg_owner: DiamaneOS`).
Security/product boundaries: `docs/THREAT_MODEL.md` (threat modeling, assumptions only).
Additional commands are documented when implemented.
