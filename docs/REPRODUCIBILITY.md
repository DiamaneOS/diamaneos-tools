# Reproduce the current generic build

This reproduces the selected generic x86_64 Android build and its input
evidence on the reference builder's managed setup. It does not build or
qualify a Fairphone 6 image, create a release, or authorize production
signing. To build a Fairphone 6 test image, follow [BUILDING.md](BUILDING.md).

[`config/build-environment.json`](../config/build-environment.json) is the
authority: its tags, commits, package and tool versions, hashes, workspace names
and targets describe one immutable environment. A newer release gets a new
environment identifier; never update an accepted identifier's pins in place.

## 1. Establish trust and inspect the snapshot

Choose your own paths:

```sh
export WORK_ROOT=/absolute/path/with-enough-space
export TOOLS_ROOT=/absolute/path/to/diamaneos-tools
export REVIEWED_TOOLS_COMMIT=<reviewed-40-hex-commit>
export MAINTAINER_ALLOWED_SIGNERS=/absolute/path/to/independently-trusted-allowed-signers
```

Authenticate the tools commit before installing it:

```sh
git -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile="$MAINTAINER_ALLOWED_SIGNERS" \
  -C "$TOOLS_ROOT" verify-commit "$REVIEWED_TOOLS_COMMIT"
test "$(git -C "$TOOLS_ROOT" rev-parse HEAD)" = "$REVIEWED_TOOLS_COMMIT"
test -z "$(git -C "$TOOLS_ROOT" status --porcelain=v1 --untracked-files=all)"
PYTHONDONTWRITEBYTECODE=1 \
  "$TOOLS_ROOT/bin/diamaneos" build preflight --inputs-only
```

A key shipped inside the checkout cannot authenticate it: get the
allowed-signers record and expected commit through the project's published
trust channel. Without an independently authenticated channel, stop and record
the bootstrap gap; a commit ID is no identity proof.

## 2. Prepare a supported build host

Use an x86_64 Debian 13 host meeting the environment's memory and free-space
minimums, with the exact declared Debian packages at verified versions. Follow
[`deploy/builder/README.md`](../deploy/builder/README.md) to create the
unprivileged `diamaneos-build` identity and install the hash- and
signature-verified toolchain, running `stage-node`, the finalizer and
`prepare-yarn` in that order from the authenticated checkout. The Yarn helper
uses the recorded npm integrity and needs an empty Corepack cache.

The bootstrap's distribution update only prepares the host. Before
qualification, configure an authenticated, reviewed Debian snapshot supplying
the declared versions and their dependency closure, then derive exact install
arguments from the environment and review the transaction before accepting it:

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

Keep the repository's signed Release/InRelease metadata, Packages indexes and
complete `.deb` dependency closure, with a SHA-256 inventory and installed
`dpkg-query` manifest; a list of top-level versions is no retained snapshot.
Missing versions stop the process: never bypass repository authentication or
substitute versions under the same environment ID. Publishing the snapshot and
an independently authenticated maintainer trust record stay maintainer
prerequisites these scripts do not create.

Every host needs an absolute, root-controlled, no-argument thermal safety check
that exits zero only while cooling is safe for a build. Configure it
explicitly; there is no machine default:

```sh
export DIAMANEOS_THERMAL_CHECK=/absolute/path/to/qualified-thermal-check
test -x "$DIAMANEOS_THERMAL_CHECK"
"$DIAMANEOS_THERMAL_CHECK"
```

The Dell files in `deploy/builder/` are a reference adapter, not a prerequisite;
other hosts may use a validated firmware curve, BMC policy or other controller
with the same exit-status contract.

## 3. Install the reviewed tools revision

Install a root-owned, non-writable detached checkout at
`/opt/diamaneos/tools-$REVIEWED_TOOLS_COMMIT` and point `/opt/diamaneos/tools`
at it (commands and ownership checks are in the builder README). Copy
`deploy/builder/builder-service.env.example` separately to
`/etc/diamaneos/builder-source-sync.env`,
`/etc/diamaneos/builder-generic-qualification.env`,
`/etc/diamaneos/builder-signing-discovery.env` and, when exercising signing,
`/etc/diamaneos/builder-dummy-signing.env`. Replace both placeholders in each;
keep them root-owned, mode `0644` (no secrets). Install only the services the
current operation needs and never enable build or signing units at boot.

## 4. Sync and verify source

Start the networked source-sync service once. It takes the release, repo-tool,
allowed-signers and workspace pins from the reviewed environment file and
carries no release tag or hash of its own.

```sh
sudo systemctl start diamaneos-builder-source-sync.service
systemctl show diamaneos-builder-source-sync.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus
```

Accept only `Result=success`, `ExecMainStatus=0` and `SubState=exited`. The
service authenticates the manifest tag, pins the `repo` implementation, syncs
all projects, rejects moving or dirty source and records the resolved project
map. Download caches are performance inputs, never source or release identity.

## 5. Run the clean generic qualification

The first accepted output directory must be empty. The offline build service
repeats preflight, builds the exact generic target from the environment file
and denies network access while compiling:

```sh
sudo systemctl start diamaneos-builder-generic-qualification.service
systemctl show diamaneos-builder-generic-qualification.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus
```

The systemd result alone is not enough. Success ends with
`GENERIC_QUALIFICATION_BUILD=PASS` and an immutable directory below
`$WORK_ROOT/evidence/generic-qualification/` with at least `preflight.json`,
`postflight.json`, `resolved-manifest.xml`, `product-out.sha256`, `result.json`
and `result.json.sha256`. Verify the result checksum and every path the report
names, and keep the run ID, tools commit and result/archive hash. A generic
emulator build is host, source and toolchain evidence only, not device
compatibility, release-signing or FP6 hardware evidence.

## 6. Independent reproduction

A second build is independent only if it starts from the declared inputs on a
separately prepared host or clean environment, without the first host's output
or release intermediates; download and compiler caches only as the environment
file allows. Compare resolved source, environment identity and final artifact
hashes and explain every difference rather than weakening the identity. The
snapshot binds exact Debian versions but ships no archival repository yet; if
those versions leave ordinary mirrors, use a reviewed retained package
snapshot, never newer packages under the same identifier.
