# DiamaneOS build host

This recipe prepares a dedicated online host for source synchronization and
untrusted OS compilation. It is not a release signer: never place production
private keys, signing tokens, recovery material or offline-signer credentials
on it.

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
before host acceptance. Exact package versions are verified by preflight; a
future long-term rebuild also needs an independently retained Debian package
source or snapshot, which is not yet supplied by this repository.

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
  https://codeberg.org/DiamaneOS/diamaneos-tools.git \
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

`finalize-debian13` is currently bound to the official Node.js v24.21.0 Linux
x64 archive, its clear-signed checksum manifest and the Node.js release
keyring. All three staging-file hashes are embedded in the script. The script
first verifies those transport hashes, authenticates the manifest with
`gpgv`, and then checks the archive against the authenticated manifest. It
installs under `/opt/nodejs/v24.21.0` and exposes root-owned links from
`/usr/local/bin`.

Stage `pubring.kbx`, `SHASUMS256.txt.asc`, and
`node-v24.21.0-linux-x64.tar.xz` in
`/tmp/diamaneos-node-v24.21.0`, then run:

```sh
sudo ./finalize-debian13 builder-admin /tmp/diamaneos-node-v24.21.0
```

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
entry point may assume Dell fan names.

## Dell Precision reference adapter

On the qualified Dell builder, the firmware automatic curve approached the
CPU thermal limit under the intended all-core workload. The bounded
`diamaneos-builder-fan-guard` therefore controls only the two physically
installed and qualified channels, `dell-smm-fan3` and `dell-smm-fan4`. It
uses the verified low state only while the CPU package is cool, requests
maximum cooling at 45 C, and requires 60 continuous seconds at or below 38 C
before returning low. Sensor, state or tachometer uncertainty requests
maximum cooling and fails the guard.

The service checks the controller every second, publishes current health in
`/run/diamaneos-builder-fan-guard/status`, restarts on failure, and leaves
maximum cooling requested on exit. Do not generalize its fan names, RPM
limits or temperature boundaries to different hardware. Every build entry
point must require a fresh healthy status and acceptable fan tachometers
before starting work. A failed guard or low-RPM result blocks the build; it
is not an advisory warning.

On this qualified adapter, invoke `diamaneos-builder-fan-check` immediately
before accepting a build lease or starting a build process. It validates service activity, status
freshness, package temperature, selected mode and the mode-specific fan RPM
floor. The later build-job wrapper must call this same executable rather than
reimplementing or bypassing the policy.

## Reference-hardware resource qualification

`qualify-builder` records the acceptance profile of the current high-capacity
Dell reference host; it is not the portable minimum and must not be copied to
different hardware as though fan channels, worker count or storage controller
names were universal. Run it only after the Dell fan guard, build identity,
time service and any required Wake-on-LAN profile are installed. It records
CPU, ECC-memory and NVMe
inventory; verifies at least 90 GiB RAM and one decimal terabyte of free build
workspace; exercises 48 CPU workers and an 80 GiB verified memory workload;
checks EDAC counters; runs an NVMe short self-test; and measures a disposable
4 GiB direct-I/O file as the build identity. The benchmark file is removed on
success and interruption.

The report deliberately distinguishes CPU-package power reported by
`turbostat` from whole-system AC input. If no plug-in power meter is available,
retain `NOT_MEASURED_NO_METER`; package power must not be relabelled as wall
power. Likewise, an operator observation without a sound meter is qualitative
and must not be expressed as a dBA measurement.

Run the qualification as a transient service so a terminal disconnect does
not interrupt the memory or storage tests:

```sh
sudo systemd-run --unit=diamaneos-builder-resource-qualification \
  --collect /usr/local/sbin/diamaneos-builder-qualify-resources
```

An accepted report ends with `BUILDER_RESOURCE_QUALIFICATION=PASS`. Preserve
the complete report privately with its SHA-256; a successful exit alone is not
the evidence.

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
`builder-service.env.example` to both named environment files, replace its
placeholders with the reviewed commit and the host's qualified thermal check,
then keep both files root-owned and mode `0644`.

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

The physical output directory remains outside the source checkout. The runner
exports it as a path relative to the source root because the pinned Siso
release requires its generated configuration repository to be addressed
relative to the source execution root. This is a path representation
constraint, not permission to mix source and output trees.

## Remote-power verification

When Wake-on-LAN is required by a deployment, it is a real powered-off test,
not only an `nmcli` configuration check. Record the boot ID, power the host off
while leaving Ethernet and AC
connected, send a magic packet from an authorized host on the LAN, and wait up
to three minutes for SSH. On this class of workstation, a timeout shorter than
the observed firmware and boot interval can produce a false failure. After SSH
returns, require a changed boot ID, zero failed units, active SSH/time sync,
a passing thermal-safety check, and the expected toolchain.

If packets were sent from more than one source before the host became
reachable, record Wake-on-LAN as successful without claiming which path caused
the wake. Wake-on-LAN does not establish firmware AC-loss recovery; test and
record power-loss behavior separately when that behavior is required.
