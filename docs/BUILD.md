# DiamaneOS host tooling

The host tools are Python 3 programs and do not require an Android source
checkout or compilation. The device runner itself uses only the Python
standard library. Schema conformance tests use the pinned development
dependencies in `requirements-dev.txt`.

## Prepare a development checkout

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
```

The accepted test host runs a detached, root-owned revision from
`/opt/diamaneos/tools`; the unprivileged runner must not be able to modify that
checkout. Deploy an exact reviewed commit using the procedure in
`deploy/test-host/README.md`, then run the same tests as the runner account.

## Device-suite interface

Device suites are reviewed JSON data under `tests/device/suites/`. They select
only adapters implemented in `src/diamaneos_tools/test_runner.py`; suite text is
never evaluated as a shell command. The initial `smoke` suite can invoke only
the runner's exact read-only ADB allowlist.

Every case declares:

- a stable case ID and affected requirement IDs;
- one stage: `inspect`, `smoke`, `security` or `destructive`;
- preconditions, an oracle and the expected result;
- required or optional applicability with a reason for an optional skip; and
- a bounded per-command timeout.

Adding an ADB command requires changing the source allowlist and tests, not
only editing suite JSON. The separate `adb-temp-file-roundtrip` adapter uses a
generated path under `/data/local/tmp`, verifies the generated payload and
attempts removal plus absence verification even after timeout or interruption.
Its host-side input is retained as raw evidence; its device copy is test data
and must not remain. The committed `smoke` suite does not select this mutating
adapter.

Destructive entries use the `installer-runbook` adapter. The v1 runner enforces
explicit destructive mode and a disposable target but deliberately does not
execute that adapter. Flash and wipe recipes remain owned by the reviewed
installer/runbook.

The current executable source binding is the official `adb` command-line
interface over the accepted USB path. Fastboot and UI-automation adapters are
intentionally absent until a reviewed suite defines their target, timeout,
cleanup and evidence contracts.

The run envelope is defined by `schemas/test-run.schema.json`. Changing a
required field or meaning requires a schema-version change and migration or
explicit rejection of old retry checkpoints.

## Private device mapping

Real execution binds the non-identifying public role to exactly one private ADB
serial. Keep this JSON outside public repositories with directory mode `0750`
and file mode `0640` or stricter:

```json
{
  "schema_version": 1,
  "devices": [
    {
      "role": "harness",
      "adb_serial": "<private-adb-serial>",
      "disposable": true
    }
  ]
}
```

Roles and serials must both be unique. `disposable` authorizes only the
runner's destructive-stage gate; it does not itself flash, wipe or approve a
particular candidate.

## Lifecycle and extension rules

One non-blocking file lock serializes each role beneath the selected private
output root. A run ID creates `<run-id>.partial`, checkpoints `result.json`
atomically after each completed case, then renames the directory to `<run-id>`.
Existing partial or final names are immutable collisions. A failed or
interrupted case stops later execution; retained results name the cases to
rerun. `--rerun-from` verifies the suite/candidate identity, target/build
identity and referenced raw-file hashes before selecting unresolved cases.

Raw command streams can contain private device state and stay in the private
run directory. The structured report contains the target role and relevant
model/build/firmware data but no ADB serial. Fixture, emulator, static-review
and real-device results are explicitly different evidence kinds; `userdebug`
results are labelled diagnostic evidence and never substitute for a final
`user` build.

Do not install a scheduled `device-regression` timer merely because the local
command exists. A timer becomes appropriate only after its exact suite,
candidate trigger, maximum runtime, private paths and owner failure route have
been exercised on the accepted host. Destructive stages always retain their
operator gate.
