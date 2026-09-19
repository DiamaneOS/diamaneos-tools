# DiamaneOS build host

This recipe prepares a dedicated online host for source synchronization and
untrusted OS compilation. It is not a release signer: never place production
private keys, signing tokens, recovery material or offline-signer credentials
on it.

## Reference environment

- x86_64 Debian 13 installed in UEFI mode
- headless `multi-user.target`
- key-only SSH for one named management account, with password-gated `sudo`
- a separate `diamaneos-build` identity with neither SSH nor `sudo`
- explicitly scheduled system updates rather than unattended package changes

GrapheneOS currently lists Debian 12, Ubuntu 24.04/24.10 and Arch Linux as
supported build hosts. Debian 13 use is a project-selected compatibility
deviation and requires an actual clean-build result before host acceptance.

## Bootstrap

Read `bootstrap-debian13` before use. Copy an exact reviewed revision to the
new host and invoke it once from a verified console or SSH session:

```sh
sudo ./bootstrap-debian13 builder-admin
```

The bootstrap:

- updates Debian and installs distribution-owned GrapheneOS/AOSP host
  dependencies and hardware diagnostics;
- enables synchronized time and persistent magic-packet Wake-on-LAN;
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

## Thermal and power acceptance

Do not accept a builder only because a short workload exits successfully.
Record idle and sustained-load temperatures, fan response, memory and storage
health, and remote power behavior. A firmware fan curve that approaches the
CPU thermal limit under the intended workload is a blocker even if the CPU can
throttle safely.

Any Linux fan policy must be fail-safe, operate only verified installed fan
channels and survive service failure at a safe speed. Validate it against a
temperature-bounded load before enabling unattended builds. Keep firmware
power-loss recovery and Wake-on-LAN behavior as separate checks.

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

Invoke `diamaneos-builder-fan-check` immediately before accepting a build
lease or starting a build process. It validates service activity, status
freshness, package temperature, selected mode and the mode-specific fan RPM
floor. The later build-job wrapper must call this same executable rather than
reimplementing or bypassing the policy.

## Resource qualification

Run `qualify-builder` only after the fan guard, build identity, time service
and Wake-on-LAN profile are installed. It records CPU, ECC-memory and NVMe
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

## Remote-power verification

Wake-on-LAN is a real powered-off test, not only an `nmcli` configuration
check. Record the boot ID, power the host off while leaving Ethernet and AC
connected, send a magic packet from an authorized host on the LAN, and wait up
to three minutes for SSH. On this class of workstation, a timeout shorter than
the observed firmware and boot interval can produce a false failure. After SSH
returns, require a changed boot ID, zero failed units, active SSH/time sync/fan
guard, and the expected toolchain.

If packets were sent from more than one source before the host became
reachable, record Wake-on-LAN as successful without claiming which path caused
the wake. Wake-on-LAN does not establish firmware AC-loss recovery; test and
record power-loss behavior separately when that behavior is required.
