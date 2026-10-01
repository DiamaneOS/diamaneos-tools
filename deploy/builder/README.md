# DiamaneOS build host

This recipe prepares a dedicated online host for source synchronization and
untrusted OS compilation. It is not a release signer: never place production
private keys, signing tokens, recovery material or offline-signer credentials
on it.

## Compatibility-suite host role

The accepted builder may also run official CTS, CTS Verifier and VTS work
because it satisfies the recorded x86-64, memory and free-storage minimums.
This is a non-secret testing role; it does not make the builder a signer and
does not permit release keys on the host. See
[`docs/COMPATIBILITY.md`](../../docs/COMPATIBILITY.md) for the version-bound
package, target and result contract.

Install `diamaneos-builder-adb.service` and the non-recurring
`diamaneos-builder-compatibility@.service` only after the exact public tools
commit and official suite archives are reviewed. The builder identity must be
in `plugdev`, and current platform-tools, `aapt2`, FFmpeg and an English locale
must pass the live host gate. Compatibility state belongs under
`/var/lib/diamaneos-build/compatibility`; private serials, setup attestations
and raw reports never enter public Git.

The powered rig normally remains on the test host. Before a compatibility
session, disable test-host maintenance through its fixed operator control,
move the rig physically, and create a builder-specific device map and rig
configuration from live topology. Never reuse a tester USB path by assumption.
After the final report and teardown are exported, stop builder ADB, move the
rig back, verify both tester roles and explicitly restore battery maintenance.

## Portable contract and reference environment

- x86_64 Debian 13 installed in UEFI mode
- headless `multi-user.target`
- key-only SSH for one named management account, with password-gated `sudo`
- a separate `diamaneos-build` identity with neither SSH nor `sudo`
- explicitly scheduled system updates rather than unattended package changes

These are software and isolation requirements, not a workstation model. Any
x86_64 host may be used when it meets the memory and storage floors in
`config/build-environment.json`, supplies a fail-closed thermal-safety check,
and completes the same clean generic build. ECC, NVMe, Wake-on-LAN and firmware
AC-loss recovery improve the reference deployment but are not silently treated
as universal build requirements.

The pinned environment record declares Debian 13 as a project-selected
compatibility deviation. It therefore requires an actual clean-build result
before host acceptance. Exact package versions are verified by preflight; this
repository does not supply the independently retained Debian package source or
snapshot that a long-term rebuild needs.

## Bootstrap

Read `bootstrap-debian13` before use. Copy an exact reviewed revision to the
new host and invoke it once from a verified console or SSH session:

```sh
sudo ./bootstrap-debian13 builder-admin
```

Wake-on-LAN is optional by default. A deployment which requires it must opt in
and will fail bootstrap if a usable active Ethernet profile cannot be
configured:

```sh
sudo DIAMANEOS_REQUIRE_WOL=1 ./bootstrap-debian13 builder-admin
```

The bootstrap:

- updates Debian and installs distribution-owned GrapheneOS/AOSP host
  dependencies and hardware diagnostics;
- enables synchronized time and, when requested or available, configures
  persistent magic-packet Wake-on-LAN;
- creates protected source, output and cache directories under
  `/var/lib/diamaneos-build`;
- hardens SSH after requiring an existing management public key;
- removes the installer desktop only after a simulated autoremove proves that
  protected packages remain; and
- leaves `sudo` password-gated and automatic package updates disabled.

When an XFCE installer was used, follow the bootstrap with the reviewed
`finalize-debian13` revision. It removes the explicitly named desktop,
printing and local-discovery packages after repeating the protected-package
simulation. It also installs the exact Node.js distribution described below.

The build identity has a usable shell solely for locally delegated build
processes. It is excluded by SSH `AllowUsers`, has no authorized key and is not
a member of `sudo`.

## Reviewed tools deployment

Deploy a detached, root-owned tools checkout. The directory name and both
service environment files bind the exact reviewed commit; changing the moving
`main` branch cannot change an in-progress or scheduled build.

```sh
TOOLS_COMMIT=REPLACE_WITH_REVIEWED_40_HEX_COMMIT
sudo git clone --no-checkout \
  https://github.com/DiamaneOS/diamaneos-tools.git \
  "/opt/diamaneos/tools-$TOOLS_COMMIT"
sudo git -C "/opt/diamaneos/tools-$TOOLS_COMMIT" checkout --detach "$TOOLS_COMMIT"
sudo git -C "/opt/diamaneos/tools-$TOOLS_COMMIT" fsck --full
sudo chown -R root:root "/opt/diamaneos/tools-$TOOLS_COMMIT"
sudo chmod -R go-w "/opt/diamaneos/tools-$TOOLS_COMMIT"
sudo ln -sfn "tools-$TOOLS_COMMIT" /opt/diamaneos/tools
```

Verify the selected commit under the project's trusted maintainer-key policy
before installing it. A commit ID protects against branch movement but does not
by itself authenticate who selected or produced the commit.

## Dependency boundary

The Debian `repo` package comes from the Debian `contrib` component and retains
its GPG-verified self-update behavior. The source tree provides most build
tools itself. The bootstrap also installs the remaining host dependencies
listed by the upstream build guide.

Vendor extraction currently requires Node.js 24 LTS. Debian 13 provides an
older system Node.js, so the bootstrap deliberately installs neither Node.js
nor Yarn. Bind and verify the exact Node.js 24 distribution before vendor
generation instead of treating an older executable as satisfied evidence.

The finalizer reads Node version/archive pins from `config/build-environment.json`
and the supplementary authenticated acquisition metadata from
`config/tool-acquisition.json`. Keep it inside the authenticated tools checkout;
do not copy it alone. The latter record supplies the exact signed manifest
hash and a commit-pinned [official Node release keyring](https://github.com/nodejs/release-keys).
It augments acquisition without changing the accepted environment bytes.

Choose a new staging directory, then download and verify as an ordinary user:

```sh
NODE_STAGE=/absolute/path/to/new-node-stage
"$TOOLS_ROOT/deploy/builder/stage-node" "$NODE_STAGE"
sudo "$TOOLS_ROOT/deploy/builder/finalize-debian13" builder-admin "$NODE_STAGE"
```

Staging verifies all three hashes, verifies the signed checksum manifest with
`gpgv`, and checks the archive against the authenticated plaintext. The root
finalizer repeats verification before installation under `/opt/nodejs/`.
Prepare integrity-pinned Yarn next, as the build identity, following
[`docs/BUILD.md`](../../docs/BUILD.md). Both steps precede source-sync preflight.

Do not globally activate whatever Yarn release Corepack happens to resolve.
The Android source revision must bind its Yarn release before vendor
generation, so finalization reports `yarn=SOURCE_PIN_REQUIRED`.

## Portable thermal-safety interface

Do not accept a builder only because a short workload exits successfully.
Record idle and sustained-load temperatures, fan response, memory and storage
health, and remote power behavior. A firmware fan curve that approaches the
CPU thermal limit under the intended workload is a blocker even if the CPU can
throttle safely.

Any Linux fan policy must be fail-safe, operate only verified installed fan
channels and survive service failure at a safe speed. Validate it against a
temperature-bounded load before enabling unattended builds. Keep firmware
power-loss recovery and Wake-on-LAN behavior as separate checks.

Every build host must provide an absolute, root-controlled executable which
takes no arguments and exits zero only when current sensors, cooling policy and
cooling response are safe for a build. Configure its path with
`DIAMANEOS_THERMAL_CHECK`; build preflight executes it with a 30-second bound.
The executable may validate a firmware-controlled curve, a BMC policy or a
host-specific Linux controller. Missing, stale or uncertain state must exit
non-zero. This small interface is the portable dependency; no public build
entry point may assume host-specific fan names.

## Generic build qualification

The committed `run-generic-qualification` entry point performs the clean
development build as the unprivileged build identity. Install the accompanying
service only from the same reviewed tools revision and write its exact 40-hex
commit to `/etc/diamaneos/builder-generic-qualification.env` as:

```text
DIAMANEOS_EXPECTED_TOOLS_COMMIT=<reviewed-tools-commit>
DIAMANEOS_THERMAL_CHECK=/absolute/path/to/qualified-thermal-check
```

Keep the file root-owned and mode `0644`; it contains no secret. The runner
verifies that `/opt/diamaneos/tools` resolves to that exact revision, repeats
the full pinned-input preflight, requires an empty output root and invokes the
configured thermal-safety check before the official x86_64 generic target. The
unit denies network access during compilation and writes only to the declared
build and log roots.

Install the source-sync and build units from the same exact checkout. Copy
`builder-service.env.example` separately to each environment file for a service
you install, replace its placeholders with the reviewed commit and the host's
qualified thermal check, then keep every copy root-owned and mode `0644`. The
known consumers are source sync and generic qualification; neither has a
default thermal check.

```sh
sudo install -o root -g root -m 0644 \
  /opt/diamaneos/tools/deploy/builder/diamaneos-builder-source-sync.service \
  /etc/systemd/system/
sudo install -o root -g root -m 0644 \
  /opt/diamaneos/tools/deploy/builder/diamaneos-builder-generic-qualification.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start diamaneos-builder-source-sync.service
sudo systemctl start diamaneos-builder-generic-qualification.service
```

Neither unit is intended to start a build automatically at boot. Source sync
has network access; the build unit requires its successful result and denies
network access during compilation.

The build unit's systemd sandbox is deliberately composed with Android's
pinned nsjail rather than layered blindly on top of it. It keeps the strongest
verified read-only system mode compatible with nsjail's nested root remount,
does not add overlapping systemd mount, `/proc`, hostname or address-family
filters that prevent nsjail from constructing its own sandbox.
`IPAddressDeny=any` remains the cgroup-enforced no-IP boundary for the service
and all descendants. Changes to these controls require an actual nsjail launch
on the supported host OS; unit-file syntax alone is insufficient.

The output directory is Android's standard source-root `out/` path. The runner
exports it relative to the source root because Soong rejects parent-relative
paths and the pinned Siso resolves its generated configuration repository from
the source execution root. The clean-build guard requires this directory to be
empty; generated output is never treated as source or imported from another
host.

## Remote-power verification

When Wake-on-LAN is required by a deployment, it is a real powered-off test,
not only an `nmcli` configuration check. Record the boot ID, power the host off
while leaving Ethernet and AC connected, send a magic packet from an authorized
host on the LAN, and wait up to three minutes for SSH. A timeout shorter than
the firmware and boot interval can produce a false failure. After SSH returns,
require a changed boot ID, zero failed units, active SSH/time sync, a passing
thermal-safety check, and the expected toolchain.

If packets were sent from more than one source before the host became
reachable, record Wake-on-LAN as successful without claiming which path caused
the wake. Wake-on-LAN does not establish firmware AC-loss recovery; test and
record power-loss behavior separately when that behavior is required.
