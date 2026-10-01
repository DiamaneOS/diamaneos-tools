# Official Android compatibility harness

How DiamaneOS runs the official Android suites (CTS, CTS Verifier, VTS) on a
test phone. The adapter binds official suite packages, the private device role
and immutable raw results for early testing; it claims no Android
compatibility, never substitutes a diagnostic GSI (generic system image) for
the shipping system and never makes an incomplete result pass.

## Registry and host

[`config/test-suites.json`](../config/test-suites.json) selects Android 17
API 37, ARM64, `user` builds and official CTS suite release `17_r2` (not an AOSP
Git tag), binding candidate-source work to immutable build environment
`fp6-android17-grapheneos-2026091000-debian13-v4`. A separate Android 16 R6
entry exists only for a labelled stock-phone trial, proving the adapter and
collection path, not the DiamaneOS candidate.

The official CTS guide currently requires a 64-bit x86 Linux host with at least
32 GiB RAM and 256 GiB free disk, glibc 2.17 or newer, English locale, FFmpeg
5.1.3 or newer and current `adb` and `aapt2`. The DiamaneOS builder is the
accepted host (the test runner is too small); any accepted x86-64 Linux host
meeting the minimums works, as the adapter has no Dell-, CPU- or
firmware-specific dependency. Smaller machines run only fixture-only parser
tests. Conformance is checked and recorded at every real-suite deployment:

```sh
bin/diamaneos test compatibility --check-host \
  --host-root /var/lib/diamaneos-build
```

## Suite packages

Use only registry URLs, record the SHA-256 of the bytes staged and keep the
official top-level directory name:

```text
/var/lib/diamaneos-build/compatibility/packages/<package-id>/
├── <official-archive-name>
└── extracted/
    └── <official-top-level-directory>/
```

Extract an approved archive into a new package directory, or verify an
independently obtained archive's committed hash and layout:

```sh
bin/diamaneos test compatibility --extract-package \
  --package-id PACKAGE_ID --archive /absolute/path/to/approved.zip \
  --package-root /absolute/path/to/new-package-directory
```

```sh
bin/diamaneos test compatibility --inspect-package \
  --package-id <package-id> \
  --archive <official-archive> \
  --package-root <directory-containing-the-official-top-level-directory>
```

The adapter rejects an uncommitted hash, renamed root, missing launcher, unsafe
symlink, special file, unsafe path or oversized inventory, without a device;
the extractor rejects traversal, duplicate members, special files and
size/count overflows. Of the relative links in `jdk/legal`,
`android-cts-v-host/jdk/legal` and `CameraITS/tests`, only those to regular
files in the same approved subtree become copies; others fail, so the tree has
no symlinks. Inspection compares every input's contents, size and executable
bit with the approved ZIP, excluding only top-level `results` and `logs` (which
may not be symlinks). Use one owner-controlled tree per concurrent trial with
inputs unchanged during runs; results and logs do not change package identity.

## Device preparation and fixtures

CTS preparation changes the device and can erase removable media: use only the
privately mapped disposable `harness` role. The registry records the minimum
setup and every known fixture. A full run must resolve the BLE beacons, GNSS,
isolated IPv4/IPv6 Wi-Fi, conditional dual-Wi-Fi and Wi-Fi RTT equipment, CTS
media and applicable manual/peer fixtures; a missing fixture blocks only its
scope and never counts as passed. CTS Verifier is manual: Android 17 needs the
documented browser role setup, and applicable cases a compatible peer Android
device, router control, measured-distance fixtures and NFC/audio equipment.
Export its report with the suite-supported workflow before teardown; never
infer a Verifier pass from screenshots or automated CTS.

Before a real trial, create an owner-controlled, mode `0640` private setup
record with no serial or account data:

```json
{
  "schema_version": 1,
  "profile_id": "stock16-harness-trial",
  "candidate_fingerprint_sha256": "<sha256-of-ro.build.fingerprint>",
  "prepared_at_utc": "2026-09-20T12:00:00Z",
  "fixture_ids": [],
  "operator_attestations": {
    "device_changes_authorized": true,
    "device_contains_no_daily_data": true,
    "result_storage_is_private": true,
    "teardown_understood": true
  }
}
```

The profile defines required fixture IDs; the wrapper recomputes the live
fingerprint hash and refuses stale or incomplete setup.

## Target and result interlocks

Each run names an exact private role and ADB serial: authorized, disposable and
the only authorized device attached. Tradefed (the suite harness) always gets
`-s`. A role lock covers the run; rig mode also holds a persistent maintenance
inhibitor, direct USB mode does no hub control. Only the exact module and test
in an approved trial profile may run.

The adapter captures bounded stdout/stderr and exactly one new result
directory and parses the official `test_result.xml`. `PASS` needs transport
success, matching suite version, the selected module/test, explicit completed
modules and a consistent summary (so standalone `--parse-result` also needs
`--profile` and a matching `--package-id`); missing XML, no tests, unfinished
modules or unknown completion is `INCOMPLETE`; malformed, mixed-version or
transport-conflicting output `HARNESS_ERROR`; any non-pass test `FAIL`. A
retry never turns these into `PASS`: it names the prior immutable
`result.json`, with matching profile and package proof and status `FAIL`,
`INCOMPLETE` or `HARNESS_ERROR`, and records the parent run ID. Validate the
XML format against the pinned official package before accepting the first
stock trial; synthetic fixtures prove only failure handling.

```sh
bin/diamaneos test compatibility --dry-run
```

## Service-owned trials on the builder

For an approved profile and package, install
`deploy/builder/diamaneos-builder-adb.service` and
`deploy/builder/diamaneos-builder-compatibility@.service`, and put only the
reviewed public tools commit in `/etc/diamaneos/builder-compatibility.env`
(mode `0644`):

```ini
DIAMANEOS_EXPECTED_TOOLS_COMMIT=<40-hex-reviewed-tools-commit>
```

Private job and setup files (owner `diamaneos-build`, mode `0640`) go at
`compatibility/jobs/<run-id>.json` and `<run-id>.setup.json`:

```json
{
  "schema_version": 1,
  "run_id": "<same-as-systemd-instance>",
  "profile_id": "<approved-profile>",
  "timeout_seconds": 7200,
  "retry_result": null,
  "connection_mode": "direct-usb"
}
```

Start with `systemctl start diamaneos-builder-compatibility@<run-id>.service`.
`run-compatibility-job` fixes the role, maps its serial privately, fixes all
roots, validates the clean tools commit and runs the ordinary adapter, with no
arbitrary command or path option; raw results stay under
`/var/lib/diamaneos-build/compatibility/runs`.

The builder owns the USB-only ADB server during a trial; records name the
connection mode. A `direct-usb` job (CLI `--direct-usb`), for a directly
attached disposable phone with no separate power controller, uses the identity
map, single-target checks, candidate/setup binding and role lock without hub
control (a USB topology node alone is no qualified rig). A `rig` job (default
for existing jobs) keeps the rig guard and maintenance inhibitor; move the rig
and phone as in the
[builder recipe](../deploy/builder/README.md#compatibility-suite-host-role),
with a builder-specific device map and rig configuration, never a tester USB
path or serial. The ADB service stops only its own foreground process, never a
global `adb kill-server`, and restarts at most three times per minute. Before a
trial, verify it owns the listening server (a client can otherwise auto-start an
unmanaged one); resolve a conflict only after all trials stop and the other
process's executable and owner are verified.

## Android 16 R6 stock trial

The pinned ARM archive reports CTS `16_r6`, build `15835701`, target `arm64`,
and bundles a Linux JDK (system Java not needed); hashes are in the registry.
Its launcher defaults to ATS but supports the Tradefed console; the adapter
fixes `USE_ATS=false` and disables the dynamic downloader to use that inspected
contract. `adb`, `aapt` and `aapt2` are prerequisites (the launcher also calls
legacy `aapt`; expose both from the pinned build-tools, as the host gate
checks). Tradefed's HOME and Java `user.home` are confined to
`compatibility/home` in the service workspace. The report writer's
`results/latest` symlink is followed only to a directory directly in the same
results root, and only real session directories count, not ZIP exports.

The trial runs `CtsOsTestCases` with
`android.os.cts.SystemClockTest#testUptimeMillis` (found in the APK DEX table
and the official Tradefed inventory; extraction and host gate pass). The
profile is approved for this labelled trial but needs a matching private
candidate/setup record. It fixes the registry ABI and disables parameterized
variants (no implicit work-profile or secondary-user runs). The module installs
and removes its APK; the CTS plan temporarily changes package verification
settings, which must be captured and restored. A timeout may skip teardown:
keep evidence and restore settings before retrying or returning the device to
ordinary use. No media, SIM or manual fixtures; not a full CTS qualification.

## Android 17 package preparation

Official ARM CTS17 R2 and Verifier17 R2 archives are hash-pinned in registry
revision `android17-compatibility-20260921-v2`. CTS expands to 35,137,411,158
bytes before notice materialization, so extraction allows a bounded 64 GiB.
Verifier's JDK notices sit under `android-cts-v-host/jdk/legal`; those trees
and `CameraITS/tests` allow bounded regular-file materialization, no escaping
links. Package availability is no evidence of a final Android 17 candidate or
pass.

## Manual report collection

The Android 16 R6 Verifier APK exports
`com.android.cts.verifier/.CtsVerifierActivity` (`TestListActivity` is
internal); use it or the app drawer. Capture settings first, follow the
official setup and test instructions, record actual outcomes and save the
report. Find the ZIP under `/sdcard/verifierReports` without assuming a
`ctsVerifierReport-` prefix (R6 names start with a timestamp and carry
suite/build labels), record it and its hash privately, and check its
`test_result.xml` suite/version and outcomes before cleanup. Then restore the
original settings (including absent ones) and remove only the known rehearsal
package. A one-test export proves collection only; unexecuted manual cases stay
unqualified. Source discovery is separate from cleanup and must not block
exporting collected reports.

## Source-bound VTS preparation

VTS is built from the exact candidate source and mapped to its VINTF and build
configuration. At `platform/test/vts` revision
`886725543e4078f87fc87aabb90b1871c7536b03`, `tools/vts-core-tradefed/Android.bp`
declares the `vts` suite package and `vts-tradefed` launcher, suite version
`17_r1`, with `res/config/vts.xml` and `res/config/vts-kernel.xml` below it;
never substitute the CTS R2 label. Resolve the Repo projects in
`test/vts-testcase` individually, not as one Git checkout.

The registry pins the derived ARM64 VTS package: 4,715 archive files and 389
configurations, reconciled with the generated test list. Provenance records the
one-line LTP `HAVE_EXECVEAT` correction (pinned Bionic already exports that
function, so LTP must not redeclare its static fallback); no assertion or
configuration was removed. The incremental build passed source checks before
and after compilation but is neither a clean-build qualification nor an
unchanged official suite. Archive, source-map, recipe, evidence and patch
hashes are in the registry, whose acquisition status covers package
preparation only.

Source-built suites get the same archive and tree verification as downloads
but keep `delivery: pinned-source-build` in every package proof; a verified
entry binds its build environment, resolved project-map hash, recipe hash and
reviewed evidence archive hash, and each source change its project, base and
derived commits and patch hash. This records reviewed provenance; it attests no
build and makes no modified suite official, and device results stay separate.
`m vts` produces `android-vts.zip` and `android-vts-tests_list.zip` for the
pinned Android 17 module: select the named suite archive and reconcile its
configurations with the test list (expecting exactly one ZIP would reject this
valid layout). Suite configuration parsing allows the bounded literal internal
entities of VTS LTP/kselftest configurations; external resources, parameter
entities, nested entity references and excessive expansion fail before
publication. Result XML keeps its stricter parser.

Match vendor/VINTF/build configuration before candidate execution. A root or
debug companion for root-dependent cases is a separately labelled diagnostic
that cannot prove locked-user properties
([VTS setup requirements](https://source.android.com/docs/core/tests/vts/setup11)).

## Other suites

[CTS-on-GSI](https://source.android.com/docs/core/tests/vts/gsi) checks the
vendor implementation with a generic system and stays a separate diagnostic
gate. Select MTS (Mainline modules) from the candidate's shipped modules and
the [official MTS source](https://android.googlesource.com/platform/test/mts/).
Available matching STS,
[Security AutoRepro](https://source.android.com/docs/security/test/autorepro)
and public CVE regressions belong to the pre-release gate; record inaccessible
partner suites as unavailable, never as run. None replaces final user-build CTS
and manual evidence. Sources: the official Android
[CTS downloads](https://source.android.com/docs/compatibility/cts/downloads),
[CTS setup guide](https://source.android.com/docs/compatibility/cts/setup),
[CTS Verifier guide](https://source.android.com/docs/compatibility/cts/verifier)
and [VTS systems guide](https://source.android.com/docs/core/tests/vts/systems).
