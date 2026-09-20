# Reproduce the current generic build

This is the linear entry point for an independent contributor. It reproduces
the selected generic x86_64 Android build and its input evidence. It does not
build or qualify a Fairphone 6 image, create a release, or authorize production
signing.

The machine-readable authority is
[`config/build-environment.json`](../config/build-environment.json). Exact tags,
commits, package versions, tool versions, hashes, workspace names and targets
in that file describe one immutable environment. A newer release uses a new
environment identifier; never reinterpret an accepted identifier by updating
its pins in place.

## 1. Establish trust and inspect the snapshot

Choose local paths rather than copying another maintainer's layout:

```sh
export WORK_ROOT=/absolute/path/with-enough-space
export TOOLS_ROOT=/absolute/path/to/diamaneos-tools
export REVIEWED_TOOLS_COMMIT=<reviewed-40-hex-commit>
export MAINTAINER_ALLOWED_SIGNERS=/absolute/path/to/independently-trusted-allowed-signers
```

Authenticate the selected tools commit before installation:

```sh
git -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile="$MAINTAINER_ALLOWED_SIGNERS" \
  -C "$TOOLS_ROOT" verify-commit "$REVIEWED_TOOLS_COMMIT"
test "$(git -C "$TOOLS_ROOT" rev-parse HEAD)" = "$REVIEWED_TOOLS_COMMIT"
test -z "$(git -C "$TOOLS_ROOT" status --porcelain=v1 --untracked-files=all)"
PYTHONDONTWRITEBYTECODE=1 \
  "$TOOLS_ROOT/bin/diamaneos" build preflight --inputs-only
```

A key shipped only inside the checkout cannot independently authenticate that
checkout. Obtain the allowed-signers record and expected commit through the
project's published trust channel. If no independently authenticated channel is
available, stop and record that bootstrap gap rather than treating a commit ID
as an identity proof.

## 2. Prepare a supported build host

Use an x86_64 Debian 13 host meeting the memory and free-space minima in the
environment file. Install the exact declared Debian packages and verify their
installed versions. Follow
[`deploy/builder/README.md`](../deploy/builder/README.md) to create the
unprivileged `diamaneos-build` identity and install the hash- and
signature-verified external toolchain.
Run `stage-node`, the finalizer and `prepare-yarn` in that order; all live in
the authenticated tools checkout. The Yarn helper uses the recorded npm
integrity during acquisition and requires an empty Corepack cache.

Bootstrap's distribution update is initial host preparation, not reproduction
of the exact package set. Before qualification, configure an authenticated,
reviewed Debian snapshot that supplies the declared versions and dependency
closure, then derive exact install arguments from the environment:

```sh
mapfile -t packages < <(python3 - "$TOOLS_ROOT/config/build-environment.json" <<'PYTHON'
import json
import sys
with open(sys.argv[1]) as stream:
    packages = json.load(stream)["host"]["required_packages"]
for name, version in sorted(packages.items()):
    print(name + "=" + version)
PYTHON
)
test "${#packages[@]}" -gt 0
sudo apt-get --download-only install "${packages[@]}"
sudo apt-get install "${packages[@]}"
```

Review the proposed transaction before accepting it. Preserve the selected
repository's signed Release/InRelease metadata, Packages indexes and complete
`.deb` dependency closure, plus a SHA-256 inventory and installed `dpkg-query`
manifest. A list of top-level versions alone is not a retained snapshot.
Missing versions are a stop condition; never bypass repository authentication
or substitute versions under the same environment ID. Snapshot publication and
an independently authenticated maintainer trust record remain maintainer-owned
prerequisites; these scripts do not create that external evidence.

Every host must provide an absolute, root-controlled, no-argument thermal safety
check. It exits zero only while the host's current cooling state is safe for a
build. Configure it explicitly; there is no machine-specific default:

```sh
export DIAMANEOS_THERMAL_CHECK=/absolute/path/to/qualified-thermal-check
test -x "$DIAMANEOS_THERMAL_CHECK"
"$DIAMANEOS_THERMAL_CHECK"
```

The Dell files in `deploy/builder/` are a reference adapter for that hardware,
not a portable prerequisite. Another host may use a validated firmware curve,
BMC policy or different controller while preserving the same exit-status
contract.

## 3. Install the reviewed tools revision

Install a root-owned, non-writable detached checkout at
`/opt/diamaneos/tools-$REVIEWED_TOOLS_COMMIT` and point
`/opt/diamaneos/tools` to it. The exact commands and ownership checks are in the
builder README. Copy `deploy/builder/builder-service.env.example` separately to:

- `/etc/diamaneos/builder-source-sync.env`;
- `/etc/diamaneos/builder-generic-qualification.env`;
- `/etc/diamaneos/builder-signing-discovery.env`; and
- `/etc/diamaneos/builder-dummy-signing.env` when exercising signing.

Replace both placeholders in every installed file. Keep them root-owned and
mode `0644`; they contain no secret. Install only the services needed for the
current operation and never enable the build or signing units at boot.

## 4. Sync and verify source

Start the networked source-sync service once. It reads release, repo-tool,
allowed-signers and workspace pins from the reviewed environment file; it must
not carry an independent release tag or hash.

```sh
sudo systemctl start diamaneos-builder-source-sync.service
systemctl show diamaneos-builder-source-sync.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus
```

Accept only `Result=success`, `ExecMainStatus=0` and `SubState=exited`. The
service authenticates the manifest tag, pins the `repo` implementation, syncs
all projects, rejects moving or dirty source and records the resolved project
map. Download caches are performance inputs; they do not become source or
release identity.

## 5. Run the clean generic qualification

The first accepted output directory must be empty. The offline build service
repeats preflight, invokes the exact generic target from the environment file,
and denies network access during compilation:

```sh
sudo systemctl start diamaneos-builder-generic-qualification.service
systemctl show diamaneos-builder-generic-qualification.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus
```

Do not accept the systemd result alone. A successful run ends with
`GENERIC_QUALIFICATION_BUILD=PASS` and creates an immutable directory below
`$WORK_ROOT/evidence/generic-qualification/` containing at least:

- `preflight.json` and `postflight.json`;
- `resolved-manifest.xml`;
- `product-out.sha256`;
- `result.json` and `result.json.sha256`.

Verify the result checksum and every path named by the report. Preserve the
exact run ID, tools commit and result/archive hash. A generic emulator build is
host, source and toolchain evidence only; it is not device compatibility,
release-signing or FP6 hardware evidence.

## 6. Independent reproduction

A second build is independent only when it starts from the declared inputs on
a separately prepared host or clean environment and does not import the first
host's output or release intermediates. Reusable download/compiler caches may
be used only under the policy in the environment file. Compare resolved source,
environment identity and final artifact hashes, and explain every difference
instead of weakening the identity to make two results appear equal.

The current snapshot binds exact Debian package versions but does not yet ship
an archival Debian repository. If those versions leave ordinary mirrors, use a
reviewed retained package snapshot; do not silently substitute newer packages
under the same environment identifier.
