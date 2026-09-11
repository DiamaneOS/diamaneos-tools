# DiamaneOS test host

This recipe prepares a dedicated, always-available Linux host for read-only
device capture and later regression harness work. Host-specific addresses,
hardware identities, ADB serials, raw diagnostics and power measurements stay
in protected operational state outside Git.

The host is an online test runner, not a release-signing system. Do not place
production signing keys, shares, recovery material or signer credentials on
it, and do not give it a route or forwarding path to an offline signer.

## Reference environment

- Debian 13 stable, amd64, installed and fully patched
- headless `multi-user.target`
- key-only SSH management with password-gated `sudo`
- dedicated non-login `diamaneos-test` account for ADB and test evidence
- official Android SDK Platform-Tools 37.0.1
- a root-owned, detached `diamaneos-tools` checkout with no usable push URL

An unattended boot can omit full-disk encryption only when the host's physical
location and stored evidence are accepted for that risk. Never keep the sole
copy of irreplaceable evidence on the test host.

## Base system

Install a minimal Debian system, then patch it and install the host tools:

```sh
sudo apt update
sudo apt full-upgrade
sudo apt install --no-install-recommends \
  openssh-server sudo git ca-certificates curl unzip rsync python3 \
  smartmontools usbutils pciutils systemd-timesyncd \
  android-sdk-platform-tools-common android-udev-rules
sudo systemctl enable --now ssh NetworkManager systemd-timesyncd
sudo timedatectl set-ntp true
```

Verify `NTPSynchronized=yes` before accepting timestamped evidence:

```sh
timedatectl show -p CanNTP -p NTP -p NTPSynchronized
systemctl --failed --no-pager --plain
```

If a live-image installer copied a desktop environment, first simulate and
review its removal. Keep SSH, networking, the kernel, `sudo` and required host
tools installed:

```sh
sudo apt-mark manual openssh-server network-manager
sudo systemctl set-default multi-user.target
sudo systemctl disable --now lightdm
sudo apt purge task-xfce-desktop task-desktop
apt-get -s autoremove --purge
```

Only run the real autoremove after confirming the simulation does not remove a
required package. Reboot and prove that key-only SSH returns before removing
the local monitor permanently.

## Identities and management boundary

Create the runner without an interactive login or `sudo` membership:

```sh
sudo adduser --system --group \
  --home /var/lib/diamaneos-test \
  --shell /usr/sbin/nologin diamaneos-test
sudo usermod -aG plugdev diamaneos-test
sudo install -d -o diamaneos-test -g diamaneos-test -m 0750 \
  /var/lib/diamaneos-test/runs
```

The management account may join `diamaneos-test` only for protected evidence
review. It retains password-gated `sudo`; the runner has neither an SSH login
nor `sudo`. Use a dedicated management SSH key, disable agent forwarding, and
verify a second key-only session before disabling password authentication.

A minimal server hardening drop-in includes:

```text
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
AllowUsers <management-account>
DisableForwarding yes
X11Forwarding no
PermitUserEnvironment no
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
```

Validate with `sshd -t`, reload SSH, keep the original session open, and prove
the replacement session before closing it.

## Android Platform-Tools

The accepted Linux archive for Platform-Tools 37.0.1 has:

```text
URL: https://dl.google.com/android/repository/platform-tools-latest-linux.zip
Size: 9054187 bytes
SHA-256: d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1
Pkg.Revision: 37.0.1
ADB build: 37.0.1-15733141
```

The `latest` URL is mutable. Retain the verified archive in protected cache;
never assume a later download still matches this pin. Verify the byte count,
SHA-256, ZIP integrity and embedded revision before installing it under a
versioned, root-owned path:

```sh
test ! -e /opt/android/platform-tools/37.0.1
sudo install -d -o root -g root -m 0755 \
  /opt/android/platform-tools/.staging-37.0.1
sudo unzip platform-tools_r37.0.1-linux.zip \
  -d /opt/android/platform-tools/.staging-37.0.1
sudo mv /opt/android/platform-tools/.staging-37.0.1/platform-tools \
  /opt/android/platform-tools/37.0.1
sudo rmdir /opt/android/platform-tools/.staging-37.0.1
sudo chmod -R go-w /opt/android/platform-tools/37.0.1
sudo ln -sfn /opt/android/platform-tools/37.0.1 \
  /opt/android/platform-tools/current
sudo install -o root -g root -m 0755 \
  deploy/test-host/adb-usb-only /usr/local/bin/adb
sudo ln -sfn /opt/android/platform-tools/current/fastboot /usr/local/bin/fastboot
```

Adjust the extraction move if the selected archive layout differs; never
overwrite an existing versioned installation. Confirm both the management and
runner accounts report the expected `adb version`. The wrapper sets
`ADB_MDNS=0`: this USB-only host does not listen on UDP 5353 for wireless ADB
discovery. After starting ADB as the runner, require `adb server-status` to
report `mdns_enabled: false` while the approved USB device remains available.

## USB mapping and explicit device trust

Use one labelled, repeatable chassis port and cable. Record its physical label,
`lsusb -t` path, negotiated speed, device-node owner/group/mode and applicable
udev rule privately. USB 2.0 at 480 Mbit/s is sufficient for ordinary ADB
capture; performance traces may justify a faster path later.

Start ADB as the runner and connect one approved disposable test device. Before
the owner accepts the phone prompt, `adb devices -l` must report
`unauthorized`, not `device`; this proves a new device was not silently
trusted. Confirm the ADB server and private key belong to `diamaneos-test`, then
the owner may explicitly approve that device. Never paste its serial into
public evidence.

Invoke ADB through `/usr/local/bin/adb`, not the versioned executable directly,
so the USB-only discovery boundary remains in effect. Verify the ADB daemon is
owned by `diamaneos-test`, TCP 5037 is loopback-only and UDP 5353 is absent.

Do not install a switchable hub speculatively. Manual unplug/reconnect is the
supported initial power-control path. A hub is justified only by a concrete
scheduled test that requires remotely controlled VBUS and by a verified
per-port-power implementation; USB data deauthorization is not equivalent to
removing bus power.

## Immutable tools deployment

Use the public HTTPS remote and an exact reviewed commit. The host receives no
Codeberg credentials:

```sh
sudo git clone --no-checkout \
  https://codeberg.org/DiamaneOS/diamaneos-tools.git \
  /opt/diamaneos/tools
sudo git -C /opt/diamaneos/tools checkout --detach <reviewed-commit>
sudo git -C /opt/diamaneos/tools remote set-url --push origin \
  disabled://test-host-does-not-push
sudo chown -R root:root /opt/diamaneos/tools
sudo chmod -R go-w /opt/diamaneos/tools
sudo -u diamaneos-test -H git config --global --add \
  safe.directory /opt/diamaneos/tools
```

The exact `safe.directory` entry permits read-only Git inspection by the
runner without making the checkout runner-writable. Do not use the wildcard
`safe.directory '*'`.

Validate the pinned tree before a hardware run:

```sh
sudo -u diamaneos-test -H env PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover \
  -s /opt/diamaneos/tools/tests/baseline \
  -t /opt/diamaneos/tools
sudo -u diamaneos-test -H \
  /opt/diamaneos/tools/bin/diamaneos baseline capture --dry-run
```

After deploying a revision that contains the staged device runner, create its
private roots and validate the committed plan. Do not place the map in the
root-owned public checkout:

```sh
sudo install -d -o diamaneos-test -g diamaneos-test -m 0750 \
  /var/lib/diamaneos-test/devices \
  /var/lib/diamaneos-test/test-runs
sudo -u diamaneos-test -H \
  /opt/diamaneos/tools/bin/diamaneos test run \
  --suite smoke --dry-run
```

Populate `/var/lib/diamaneos-test/devices/test-host.json` using the private-map
contract in `docs/BUILD.md`, owned by `diamaneos-test:diamaneos-test` at mode
`0640`. Bind the `harness` role to the already approved USB serial without
printing it into logs. Mark it disposable only when that phone is genuinely
reserved for tests and contains no data requiring preservation.

## Live capture and evidence

Run from Bash under the protected runner identity with `umask 027`. Require
exactly one authorized device, bind its serial to the explicit `--target`
argument without printing it, and record truthful conditions:

```sh
mapfile -t TEST_DEVICE_TARGETS < <(
  adb devices | awk '$2 == "device" { print $1 }'
)
test "${#TEST_DEVICE_TARGETS[@]}" -eq 1
TEST_DEVICE_TARGET=${TEST_DEVICE_TARGETS[0]}
TEST_RUN_ID=$(date -u +live-%Y%m%dT%H%M%SZ)

/opt/diamaneos/tools/bin/diamaneos baseline capture \
  --target "$TEST_DEVICE_TARGET" \
  --run-id "$TEST_RUN_ID" \
  --conditions "<build, cable, network, power and ambient conditions>" \
  --raw-dir /var/lib/diamaneos-test/runs \
  --output "/var/lib/diamaneos-test/runs/$TEST_RUN_ID.report.json"
```

The corresponding initial staged smoke run is read-only:

```sh
TEST_RUN_ID=$(date -u +smoke-%Y%m%dT%H%M%SZ)

/opt/diamaneos/tools/bin/diamaneos test run \
  --suite smoke \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map /var/lib/diamaneos-test/devices/test-host.json \
  --evidence-kind real-device \
  --run-id "$TEST_RUN_ID" \
  --conditions "<build, cable, network, power and ambient conditions>" \
  --output /var/lib/diamaneos-test/test-runs
```

The result is `/var/lib/diamaneos-test/test-runs/$TEST_RUN_ID/result.json`.
Check its full expected inventory and per-case statuses; exit zero alone is not
acceptance. Keep the whole directory private, verify its hashes before any
retry, and review the structured report for publication separately.

Raw files may contain device and network identifiers. Keep them outside Git at
`0640` beneath a `0750` run directory. Share only the sanitized report after a
privacy review. Hash every retained report and raw evidence file.

The graphics readiness query is deliberately scoped to
`dumpsys gfxinfo com.android.systemui`; do not raise the 256 KiB limit to make
an unscoped all-package query pass.

For disconnect validation, retain the selected target privately, unplug the
cable manually, and invoke the collector behind an outer 30-second timeout.
It must return the documented no-device error without reaching the outer
timeout or exposing the serial. Reconnect the same cable and confirm exactly
one already-approved device returns. This is the required no-hub fallback.

## Operational acceptance

Maintain these private operational facts. If a non-gating metric is
unavailable, record it as `NOT_RUN` rather than substituting an estimate:

- OS, kernel, BIOS, installed ADB version and immutable tools revision
- USB controller/root-hub topology and the selected physical port/cable
- trace filesystem, free bytes, ownership and mount options
- wall-meter idle power after services settle; do not substitute a PSU rating
- NTP synchronization and update-timer state
- normal reboot-to-SSH and the firmware's restore-after-AC-loss behavior
- listening network services and the absence of signer credentials/routes
- initial unauthorized state, explicit pairing, live collector result,
  bounded disconnect result and manual reconnect result

Re-run affected checks after OS, Platform-Tools, collector, cable/port or device
changes. Acceptance follows the task's criteria; a failed, incomplete or unrun
mandatory check remains visible and non-gating bookkeeping is not rewritten as
a pass.
