# Official Android compatibility harness

How DiamaneOS runs the official Android compatibility suites on a test phone,
and what their results can and cannot show. For anyone preparing a suite
package, test host or trial.

| Suite | What it is |
| --- | --- |
| CTS | Compatibility Test Suite: automated checks against the Android CDD (Compatibility Definition Document). |
| CTS Verifier | The manual CTS cases, run by a person on the phone. |
| VTS | Vendor Test Suite: checks of the vendor side, such as hardware interfaces and the kernel. |
| CTS-on-GSI | CTS on a generic system image (GSI), to check the vendor implementation. |
| MTS, STS | Mainline Test Suite (updatable modules); Security Test Suite (bulletin fixes). |

Tradefed (Trade Federation) is the harness that runs CTS and VTS.

The DiamaneOS adapter binds the official suite packages, the private physical
device role and immutable raw results. It prepares early testing; it does not
claim Android compatibility, substitute a diagnostic GSI for the shipping
system, or make an incomplete test result pass.

## Suite registry

The machine-readable registry,
[`config/test-suites.json`](../config/test-suites.json), currently selects
Android 17 API 37, ARM64, `user` builds and the official CTS suite release
`17_r2` (not presented as an AOSP Git tag), and binds candidate-source work to
immutable build environment `fp6-android17-grapheneos-2026091000-debian13-v4`.
A separately versioned Android 16 R6 entry exists only for a labelled harness
trial on a stock phone; a stock result proves the adapter and collection path,
not the future DiamaneOS candidate.

## Test host

The official CTS setup guide currently requires a 64-bit x86 Linux host with at
least 32 GiB RAM, at least 256 GiB free disk, glibc 2.17 or newer, English
locale, FFmpeg 5.1.3 or newer, and current `adb` and `aapt2` tools.

The DiamaneOS builder is the accepted compatibility host, because the current
test runner is below the official CTS memory and storage minimums. Other
projects can use any accepted x86-64 Linux host meeting the recorded resource
and tool requirements; the public adapter has no Dell-, CPU- or
firmware-specific dependency. A smaller machine can run fixture-only parser
tests but cannot produce a labelled real-harness or full-suite result. Host
conformance is checked and recorded at every real-suite deployment.

Check the live host without contacting a device:

```sh
bin/diamaneos test compatibility --check-host \
  --host-root /var/lib/diamaneos-build
```

## Suite packages

Use only the URLs recorded in the registry, and record the SHA-256 of the bytes
actually staged. Do not rename the extracted top-level suite directory. Stage
each package in a private directory of this form:

```text
/var/lib/diamaneos-build/compatibility/packages/<package-id>/
├── <official-archive-name>
└── extracted/
    └── <official-top-level-directory>/
```

After approving an official archive hash, extract it into a new package
directory:

```sh
bin/diamaneos test compatibility --extract-package \
  --package-id PACKAGE_ID --archive /absolute/path/to/approved.zip \
  --package-root /absolute/path/to/new-package-directory
```

Verify an independently obtained archive's exact committed hash and extracted
layout:

```sh
bin/diamaneos test compatibility --inspect-package \
  --package-id <package-id> \
  --archive <official-archive> \
  --package-root <directory-containing-the-official-top-level-directory>
```

- The adapter rejects an uncommitted hash, renamed root, missing launcher,
  unsafe symlink, special file, unsafe path or oversized inventory. Package
  discovery does not contact a device.
- The extractor rejects traversal, duplicate members, special files and
  size/count overflows. Official archives use relative links in `jdk/legal`,
  `android-cts-v-host/jdk/legal` and `CameraITS/tests`; only links to regular
  files within the same approved subtree are materialized as copies. Escaping,
  dangling, chained and other symlinks fail, so the extracted tree has no
  symlinks.
- Inspection compares every input's contents, size and executable bit with the
  approved ZIP. Only top-level `results` and `logs` are excluded as generated
  outputs; replacing those roots with symlinks is rejected.
- Use one owner-controlled package tree per concurrent trial and keep its inputs
  unchanged while it runs. A retry does not change package identity just
  because results/logs now exist.

## Device preparation and fixtures

CTS preparation changes the test device and can erase removable media; use only
the privately mapped disposable `harness` role. The registry records the
minimum setup and every known fixture (equipment or setup a case needs) state.
A full run must resolve the BLE beacons, GNSS, isolated IPv4/IPv6 Wi-Fi,
conditional dual-Wi-Fi and Wi-Fi RTT equipment, CTS media and applicable
manual/peer fixtures. A missing fixture blocks only its affected qualification
scope and is never reported as a successful test.

CTS Verifier is separately manual. Android 17 needs the documented browser role
setup, and applicable cases need a compatible peer Android device, router
control, measured-distance fixtures and feature-specific NFC/audio equipment.
Export the report with the suite-supported workflow and keep it before
teardown. Do not infer a CTS Verifier pass from screenshots or an automated CTS
result.

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

The selected profile defines which fixture IDs must be present. The wrapper
recomputes the live fingerprint hash and refuses a stale or incomplete setup.

## Target and result interlocks

- Every run supplies an exact private role and ADB serial. The serial must be
  authorized, the role marked disposable, and the device the only authorized
  one attached to the host. Tradefed is always invoked with `-s`; enumeration
  order never selects the target.
- A role lock covers the run; configured rig mode also holds a persistent
  maintenance inhibitor. Direct USB mode performs no hub control.
- Only the exact module and test committed in an approved trial profile may
  run.

The adapter captures bounded stdout/stderr and exactly one newly created
Tradefed result directory, validates the suite version and parses the official
`test_result.xml`:

| Result | When |
| --- | --- |
| `PASS` | transport success, matching suite version, the selected module/test, explicit completed modules and a consistent summary |
| `INCOMPLETE` | missing XML, no tests, unfinished modules, or missing or unknown completion |
| `HARNESS_ERROR` | malformed, mixed-version or transport-conflicting output |
| `FAIL` | any test that does not pass |

Standalone `--parse-result` therefore also requires `--profile` and a matching
`--package-id`. Validate the exact XML format against the pinned official
package before accepting the first stock trial; synthetic fixtures establish
failure handling, not acceptance of an official suite format or Android build.

No retry can rename a result to `PASS`. A retry names the prior immutable
`result.json`; its profile and package proof must match and its parent status
must be `FAIL`, `INCOMPLETE` or `HARNESS_ERROR`. The new record stores the
parent run ID.

Review the read-only plan at any time:

```sh
bin/diamaneos test compatibility --dry-run
```

## Service-owned trials on the builder

When a profile and package have been approved:

1. Install `deploy/builder/diamaneos-builder-adb.service` and
   `deploy/builder/diamaneos-builder-compatibility@.service`.
2. Create `/etc/diamaneos/builder-compatibility.env` with mode `0644`,
   containing only the reviewed public tools commit:

```ini
DIAMANEOS_EXPECTED_TOOLS_COMMIT=<40-hex-reviewed-tools-commit>
```

3. Create the private job and setup files, owned by `diamaneos-build` with mode
   `0640`, at `compatibility/jobs/<run-id>.json` and `<run-id>.setup.json`. The
   job looks like this:

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

4. Start it with
   `systemctl start diamaneos-builder-compatibility@<run-id>.service`.

`run-compatibility-job` fixes the device role, maps its serial privately, fixes
all public and private roots, validates the exact clean tools commit, then runs
the ordinary adapter. It exposes no arbitrary command or path option. Raw
results stay under `/var/lib/diamaneos-build/compatibility/runs`.

### Connection modes

The builder owns the USB-only ADB server while a compatibility trial is in
scope. Records identify the connection mode.

- **`direct-usb`** (CLI `--direct-usb`): for a directly attached disposable
  phone with no separate controller managing its power. It uses the private
  identity map, single-target checks, candidate/setup binding and role lock
  without querying or controlling hub power. A USB topology node alone does not
  establish a qualified controllable rig.
- **`rig`** (default for existing jobs): keeps the qualified rig guard and
  persistent maintenance inhibitor. Move the qualified rig and disposable FP6 to
  this host only after the test-host battery controller is cleanly disabled. The
  builder uses its own private device map and rig configuration; copying a
  host-specific USB path or serial from the tester is not permitted. Stop the
  builder ADB service and move the rig back before restoring test-host battery
  maintenance.

### The builder ADB service

The dedicated ADB service stops only its own foreground process; it must not
issue a global `adb kill-server` that could stop another listener. Restarts are
limited to three starts per minute. Before starting a trial, verify that the
service owns the listening server, since an ADB client can otherwise auto-start
an unmanaged server after the service exits. Recover that conflict only after
all trials stop and the conflicting process's executable and owner are
verified.

## Android 16 R6 stock trial

The pinned ARM archive reports CTS `16_r6`, build `15835701`, target `arm64`,
and includes its own Linux JDK; package hashes are in the registry.

- Its launcher defaults to ATS (Android Test Station) but also explicitly
  supports the Tradefed console. The adapter fixes `USE_ATS=false` and disables
  the dynamic downloader, so invocation and result parsing use that inspected
  console contract.
- `adb`, `aapt` and `aapt2` are host prerequisites; system Java is not needed
  while the bundled JDK is present. The console launcher also invokes legacy
  `aapt` for APK inspection, so expose both `aapt` and `aapt2` from the pinned
  Android build-tools; the host gate checks both.
- The builder adapter confines Tradefed's HOME and Java `user.home` to its own
  `compatibility/home` directory in the service's writable workspace.
- The official report writer creates a `results/latest` symlink and may create
  ZIP exports beside session directories. Collection counts real session
  directories only and allows `latest` only when it resolves to a directory
  directly within the same results root; escaping or dangling aliases fail.

The stock timing-test candidate is `CtsOsTestCases` with
`android.os.cts.SystemClockTest#testUptimeMillis`. Package inspection found the
class/method binding in the APK DEX table and the module in the official
Tradefed inventory; extraction and the host gate pass. The profile is approved
for this labelled trial, but a matching private candidate/setup record is still
required. The trial fixes the registry ABI and disables parameterized module
variants, so it does not implicitly create work-profile or secondary-user runs.
The module installs its test APK and cleans it up; the CTS plan also changes
package verification settings temporarily, so capture and restore them on the
disposable harness. A timeout may prevent upstream teardown: keep the evidence
and restore captured device settings before retrying or returning the device to
ordinary use. The trial needs no media, SIM or manual hardware fixtures and
does not count as a full CTS qualification.

## Android 17 package preparation

The official ARM CTS17 R2 and Verifier17 R2 archives are hash-pinned in registry
revision `android17-compatibility-20260921-v2`. CTS expands to 35,137,411,158
bytes before notice materialization, so authenticated extraction uses a bounded
64 GiB ceiling. Verifier's bundled host runner places its JDK notices under
`android-cts-v-host/jdk/legal`; those notice trees and `CameraITS/tests` allow
bounded regular-file materialization, and links cannot escape their own tree.
Package availability is not evidence of a final Android 17 candidate or a test
pass.

## Manual report collection

The Android 16 R6 Verifier APK exports its launcher as
`com.android.cts.verifier/.CtsVerifierActivity`; `TestListActivity` is
internal. Use the exported alias or the app drawer.

1. Capture settings before preparation.
2. Follow the matching official setup and individual test instructions, record
   the actual outcome, then save the report.
3. Find the actual ZIP under `/sdcard/verifierReports`; do not assume a
   `ctsVerifierReport-` prefix (the observed R6 name begins with a timestamp and
   includes suite/build labels). Record the selected file and its hash
   privately.
4. Preserve the ZIP and verify its `test_result.xml` suite/version and recorded
   outcomes before cleanup.
5. After keeping the evidence, restore the original setting values (including
   absent settings) and remove only the known rehearsal package.

A one-test export demonstrates collection only; unexecuted manual cases stay
unqualified. Source discovery is separate from device cleanup and must not
prevent exporting already collected reports.

## Source-bound VTS preparation

VTS is built from the exact candidate source and mapped to its VINTF (declared
vendor interfaces) and build configuration. At `platform/test/vts` revision
`886725543e4078f87fc87aabb90b1871c7536b03`:

- the observed build definition `tools/vts-core-tradefed/Android.bp` declares
  the `vts` suite package and `vts-tradefed` launcher, with suite version
  `17_r1`;
- configurations include `res/config/vts.xml` and `res/config/vts-kernel.xml`
  below the same directory.

Do not substitute the CTS R2 download label for this VTS source version.
`test/vts-testcase` contains several Repo projects: resolve their individual
revisions instead of treating the parent as a Git checkout.

The registry now pins the derived ARM64 VTS package built from this source:
4,715 archive files and 389 configurations, reconciled with the generated test
list. Build provenance records the one-line LTP `HAVE_EXECVEAT` correction: the
pinned Bionic already declares and exports that function, so LTP must not
redeclare its static fallback. No test assertion or configuration was removed.
The incremental package build passed source checks before and after
compilation; it is neither a clean-build qualification nor an unchanged official
binary suite. Archive, source-map, recipe, evidence and patch hashes are in the
package registry; its acquisition status describes package preparation only.

### Source-built suite packages

A source-built suite uses the same archive and extracted-tree verification as
an official download, but keeps `delivery: pinned-source-build` in every
package proof. A verified registry entry must bind its build environment,
resolved project-map hash, recipe hash and reviewed evidence archive hash.
Record each source change with its project, base and derived commits and patch
hash. These fields record the maintainer's reviewed build provenance; they do
not independently attest to a build or turn a modified suite into an unchanged
official distribution. Candidate device results stay separate.

For the pinned Android 17 VTS packaging module, `m vts` produces both
`android-vts.zip` and `android-vts-tests_list.zip`. Select the named suite
archive and reconcile its configuration inventory with the generated test list;
requiring exactly one ZIP in the output directory would reject this valid
layout.

Suite configuration parsing allows the bounded literal internal entities used by
VTS LTP/kselftest configurations. External resources, parameter entities,
nested entity references and excessive expansion fail before an extracted
package is published. This allowance applies to authenticated suite inputs;
result XML keeps its separate, stricter parser.

### Before running VTS on a candidate

Match the vendor/VINTF/build configuration before enabling candidate execution.
For root-dependent cases, keep a separately labelled diagnostic companion; a
root/debug companion cannot prove locked-user properties. See the
[VTS setup requirements](https://source.android.com/docs/core/tests/vts/setup11).

## CTS-on-GSI, MTS and security suites

- [CTS-on-GSI](https://source.android.com/docs/core/tests/vts/gsi) checks the
  vendor implementation with a generic system. It is a separate diagnostic
  gate, not final-system acceptance.
- Select matching MTS modules only after the candidate's shipped module
  inventory is known, using the
  [official MTS source](https://android.googlesource.com/platform/test/mts/).
- Available matching STS,
  [Security AutoRepro](https://source.android.com/docs/security/test/autorepro)
  and public CVE regression cases for security bulletin regressions belong to
  the pre-release gate.
- Partner artifacts that are not accessible are recorded as unavailable, never
  claimed as run. Keep those suite gaps explicit.

None of these replace the final user-build CTS and manual evidence.

## Sources

The official Android
[CTS downloads](https://source.android.com/docs/compatibility/cts/downloads),
[CTS setup guide](https://source.android.com/docs/compatibility/cts/setup),
[CTS Verifier guide](https://source.android.com/docs/compatibility/cts/verifier)
and [VTS systems guide](https://source.android.com/docs/core/tests/vts/systems).
