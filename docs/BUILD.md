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

OS compilation uses a separate online build host and unprivileged build
identity. The builder setup and trust boundary are documented in
[`deploy/builder/README.md`](../deploy/builder/README.md). Production release
keys never enter that host. Host acceptance requires an actual clean build in
addition to hardware, capacity, thermal and remote-management checks.

The builder's resource qualification is intentionally narrower than a clean
build. Its passing report establishes the observed host resources, bounded
load behavior, ECC counters, storage health and management configuration. It
does not establish source compatibility, reproducibility or release
eligibility. Whole-system AC power and acoustic results also remain unmeasured
unless their dedicated external meters were actually used.

## Pinned Android environment

`config/build-environment.json` is the build-input authority for FP6-033. It
binds the selected stable GrapheneOS tag, tag object, peeled manifest commit,
official signer-list hash, signer identity, tagged `default.xml`, canonical
1,057-project commit map, the GPG-verified `repo` v2.65 tag object/commit, host
packages, external tools and project/device input hashes. The Debian `repo`
2.54 package is only the launcher; the self-updating implementation is a
separate input and is pinned to commit
`35bbf701d04de5c6a71937279bc3d16f6ce36808` instead of its moving `stable`
branch. The selected `2026091000` release is explicitly published for generic
and other targets. A branch name, a GitHub verification badge or an existing
download cache is not a substitute for the local signature checks.

The project-selected Debian 13 host is newer than the operating systems listed
by the upstream build guide. This is a declared compatibility deviation. The
builder is not finally accepted until this exact environment completes an
actual clean generic qualification build. That build proves only the host and
generic source path; it does not prove that the unfinished FP6 target boots or
meets the device requirements.

The portable cold-environment check requires no source tree or private cache:

```sh
bin/diamaneos build preflight --inputs-only
```

It validates all committed input records and emits a declared build identity.
Changing a tag, project-map pin, tool record, stock input or patch inventory
changes that identity. It deliberately reports the generated FP6 device-input
manifest as pending until the generator has produced and verified it.

Run source synchronization as the unprivileged build identity from an exact,
reviewed tools checkout:

```sh
deploy/builder/sync-pinned-source /opt/diamaneos/tools
```

The script downloads the current official signer list, verifies its pinned
hash, initializes only `refs/tags/2026091000`, fixes the internal `repo`
implementation to the signed v2.65 tag commit, verifies the `repo` tag through
the launcher's GPG keyring, verifies the GrapheneOS tag with OpenSSH, runs
`repo sync -j8` without a fallback, and then invokes the full preflight. Any
fetch, signature, revision, clean-tree or package mismatch terminates the
operation. The initial recipe also requires an empty output root. Preserve the
complete stdout/stderr and its SHA-256 as private build evidence.

Before downloading source, the same script runs `--host-only` to enforce the
exact package/tool pins, memory and free-space floors, separated workspace,
empty clean-build output and live fan-guard preflight. A host mismatch therefore
fails before consuming a large sync.

The source revision uses a Yarn v1 lockfile. Prepare the declared Yarn
`1.22.22` through the already pinned Corepack installation in a build-owned
tool directory, then put only that directory ahead of the fixed system path:

```sh
install -d -m 0750 /var/lib/diamaneos-build/toolbin
corepack enable --install-directory /var/lib/diamaneos-build/toolbin yarn
corepack install --global yarn@1.22.22
PATH=/var/lib/diamaneos-build/toolbin:/usr/local/bin:/usr/bin:/bin \
  yarn --version
```

The preflight rejects another Yarn version. Its source lockfile hash and npm
registry integrity are part of the environment record; dependency installation
still uses the checked-in lockfile and must not rewrite it.

The accepted workspace separates state as follows:

- `src/grapheneos-2026091000` contains only the repo checkout;
- `cache` contains reusable downloads and compiler cache, which may improve
  performance but cannot define a release input;
- `out/grapheneos-2026091000` contains the clean build intermediates and is
  never imported from another host.

Use `OUT_DIR` and `CCACHE_DIR` to enforce those boundaries. The first generic
qualification run starts with an empty output root:

```sh
export OUT_DIR=/var/lib/diamaneos-build/out/grapheneos-2026091000
export CCACHE_DIR=/var/lib/diamaneos-build/cache/ccache
source build/envsetup.sh
lunch sdk_phone64_x86_64-cur-userdebug
m
```

Before this or any later build, the full preflight must pass and the builder
fan-guard check must remain healthy. A clean generic result does not authorize
production signing material on the online builder. The FP6 release-purpose
preflight remains fail-closed until the generated exact-stock device-input
manifest and FP6 product target are verified.

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
