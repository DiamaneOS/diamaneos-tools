# Official Android compatibility harness

The compatibility adapter binds official suite packages, the private physical
device role and immutable raw results. It prepares early testing; it does not
claim Android compatibility, substitute a diagnostic GSI for the shipping
system, or make an incomplete test result pass.

The machine-readable registry is
[`config/test-suites.json`](../config/test-suites.json). It currently selects
Android 17 API 37, ARM64, `user` builds and the official
CTS suite release `17_r2` and binds candidate-source work to immutable build
environment `fp6-android17-grapheneos-2026091000-debian13-v4`. The suite name
is not presented as an AOSP Git tag. The separately versioned Android 16 R6
entry exists only for a labelled stock-device harness trial. A stock result
proves the adapter and collection path, not the future DiamaneOS candidate.

## Official inputs and host gate

Use only the URLs recorded in the registry and record the SHA-256 of the bytes
actually staged. Do not rename the extracted top-level suite directory. The
official CTS setup guide currently requires a 64-bit x86 Linux host with at
least 32 GiB RAM, at least 256 GiB free disk, glibc 2.17 or newer, English
locale, FFmpeg 5.1.3 or newer, and current `adb` and `aapt2` tools.

The DiamaneOS builder is the accepted compatibility host because the current
test runner is below the official CTS memory and storage minimums. Other
projects can use any accepted x86-64 Linux host meeting the recorded resource
and tool requirements; the public adapter has no Dell-, CPU- or firmware-
specific dependency. An under-sized machine can exercise fixture-only parser
tests, but it cannot produce a labelled real-harness or full-suite result.
Host conformance is checked and recorded at every real-suite deployment.

Check the live host without contacting a device:

```sh
bin/diamaneos test compatibility --check-host \
  --host-root /var/lib/diamaneos-build
```

Stage each package in a private directory of this form:

```text
/var/lib/diamaneos-build/compatibility/packages/<package-id>/
├── <official-archive-name>
└── extracted/
    └── <official-top-level-directory>/
```

After independently obtaining the archive, verify its exact committed hash
and extracted layout:

```sh
bin/diamaneos test compatibility --inspect-package \
  --package-id <package-id> \
  --archive <official-archive> \
  --package-root <directory-containing-the-official-top-level-directory>
```

The adapter rejects an uncommitted hash, renamed root, missing launcher,
unsafe symlink, special file, unsafe path or oversized inventory. Package discovery
does not contact a device.

## Device preparation and fixtures

CTS preparation changes the test device and can erase removable media. Use
only the privately mapped disposable `harness` role. The registry records the
minimum setup and every known fixture state. In particular, a full run must
resolve the BLE beacons, GNSS, isolated IPv4/IPv6 Wi-Fi, conditional dual-Wi-Fi
and Wi-Fi RTT equipment, CTS media, and applicable manual/peer fixtures. A
missing fixture blocks only its affected qualification scope; it is never
reported as a successful test.

CTS Verifier is separately manual. Android 17 requires the documented browser
role setup, and applicable cases require a compatible peer Android device,
router control, measured-distance fixtures and feature-specific NFC/audio
equipment. Export the report using the suite-supported workflow and retain it
before teardown. Do not infer a CTS Verifier pass from screenshots or from an
automated CTS result.

Before a real trial, create an owner-controlled mode `0640` private setup
record. It contains no serial or account data:

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
recomputes the live fingerprint hash and refuses stale or incomplete setup.

## Target and result interlocks

Every execution supplies an exact private role and ADB serial. The selected
serial must be authorized, the role must be marked disposable, and it must be
the only authorized device attached to that host. Tradefed is always invoked
with `-s`; enumeration order is never a target selector. A rig role lock and
persistent inhibitor cover the run.

Only the exact module and test committed in an approved trial profile may run.
The adapter captures bounded stdout/stderr and exactly one newly created
Tradefed result directory. It validates the suite version and parses the
official `test_result.xml`. Missing XML, no tests or unfinished modules become
`INCOMPLETE`; malformed, mixed-version or transport-conflicting output becomes
`HARNESS_ERROR`; any non-pass test becomes `FAIL`. None can be renamed to
`PASS` by a retry.

A retry names the prior immutable `result.json`. Its profile and package proof
must match and its parent status must be `FAIL`, `INCOMPLETE` or
`HARNESS_ERROR`. The new record stores the parent run ID.

Review the read-only plan at any time:

```sh
bin/diamaneos test compatibility --dry-run
```

## Service-owned trials

When a profile and package have been approved, install
`deploy/builder/diamaneos-builder-adb.service` and
`deploy/builder/diamaneos-builder-compatibility@.service`, then create
`/etc/diamaneos/builder-compatibility.env` mode `0644` with only the reviewed
public tools commit:

```ini
DIAMANEOS_EXPECTED_TOOLS_COMMIT=<40-hex-reviewed-tools-commit>
```

The private job and setup files are owned by `diamaneos-build`, mode `0640`, at
`compatibility/jobs/<run-id>.json` and `<run-id>.setup.json`. The job shape is:

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

Start it with
`systemctl start diamaneos-builder-compatibility@<run-id>.service`.
`run-compatibility-job` fixes the device role, maps its serial privately,
fixes all public and private roots, validates the exact clean tools commit and
then executes the ordinary adapter. It exposes no arbitrary command or path
option. Raw results remain beneath
`/var/lib/diamaneos-build/compatibility/runs`.

The builder owns the USB-only ADB server while a compatibility trial is in
scope. An explicit `direct-usb` job (CLI `--direct-usb`) uses the private
identity map, single-target checks, candidate/setup binding and role lock without
querying or controlling hub power. A USB topology node alone does not establish
a qualified controllable rig. Use this mode for a directly attached disposable
phone, with no separate controller managing its power. Records identify the
connection mode.

A `rig` job (the default for existing jobs) retains the qualified rig guard and
persistent maintenance inhibitor. Move the qualified rig and disposable FP6 to this host only after the
test-host battery controller is cleanly disabled. The builder uses a separate
private device map and rig configuration; copying a host-specific USB path or
serial from the tester is not permitted. Stop the builder ADB service and move
the rig back before restoring test-host battery maintenance.

## VTS and modular-suite boundary

VTS is built from the exact candidate source and mapped to its VINTF and build
configuration. Any root/debug companion is labelled diagnostic and cannot
prove locked-user properties. CTS-on-GSI is likewise diagnostic rather than
final-system acceptance. Matching MTS modules are selected only after the
candidate's shipped module inventory is known. Available matching STS,
AutoRepro and public CVE regressions belong to the pre-release gate; partner
artifacts that are not accessible are recorded as unavailable, never claimed
as run.

Sources: the official Android
[CTS downloads](https://source.android.com/docs/compatibility/cts/downloads),
[CTS setup guide](https://source.android.com/docs/compatibility/cts/setup),
[CTS Verifier guide](https://source.android.com/docs/compatibility/cts/verifier)
and [VTS systems guide](https://source.android.com/docs/core/tests/vts/systems).

## Authenticated extraction and result completion

After approving an official archive hash, create a new package directory:

```sh
bin/diamaneos test compatibility --extract-package \
  --package-id PACKAGE_ID --archive /absolute/path/to/approved.zip \
  --package-root /absolute/path/to/new-package-directory
```

The extractor rejects traversal, duplicate members, special files and
size/count overflows. The official bundled JDK uses relative licence-notice
links; only links beneath `jdk/legal` to regular files in that same archive
subtree are materialized as copies of the referenced text. Escaping, dangling,
chained and other symlinks fail. The extracted tree contains no symlinks. Inspection compares every input's contents, size and
executable bit with the approved ZIP. Only top-level `results` and `logs` are
excluded as generated outputs; replacing those roots with symlinks is rejected.
Use one owner-controlled package tree per concurrent trial and keep its inputs
unchanged during execution. Retrying does not change package identity merely
because results/logs now exist.

PASS requires transport success, matching suite version, the selected
module/test, explicit completed modules and a consistent summary. Standalone
`--parse-result` therefore also requires `--profile` and a matching `--package-id`.
Missing or unknown completion remains incomplete. Validate the exact XML format
against the pinned official package before accepting the first stock trial;
synthetic fixtures establish failure handling, not acceptance of an official
suite format or an Android build.

## Observed Android 16 R6 package behavior

The pinned ARM archive reports CTS `16_r6`, build `15835701`, target `arm64`,
and includes its own Linux JDK. Its launcher defaults to ATS but also explicitly
supports the Tradefed console. The adapter fixes `USE_ATS=false` and disables
the dynamic downloader so its invocation and result parser use that inspected
console contract. `adb` and `aapt2` are host prerequisites; system Java is not
required while the bundled JDK is present. Package hashes are in the registry.

The stock timing-test candidate is `CtsOsTestCases` with
`android.os.cts.SystemClockTest#testUptimeMillis`. Package inspection found the
class/method binding in the APK DEX table and the module in the official
Tradefed inventory. Extraction and the host gate pass. The profile is approved
for this labelled trial; a matching private candidate/setup record is still
required. The trial fixes the registry ABI and disables parameterized module
variants, so it does not implicitly create work-profile or secondary-user runs. The module
installs its test APK and cleans it up; the CTS plan also changes package
verification settings temporarily. These settings must be captured and restored
on the disposable harness. This trial does not require media, SIM or manual
hardware fixtures and does not count as a full CTS qualification.

The official report writer creates a `results/latest` symlink and may create
ZIP exports alongside session directories. Collection counts real session
directories only and permits `latest` solely when it resolves to a directory
directly within the same results root. Escaping or dangling aliases fail.

The console launcher also invokes legacy `aapt` for APK inspection; expose both
`aapt` and `aapt2` from the pinned Android build-tools. The host gate checks both.
The builder adapter confines Tradefed's HOME and Java `user.home` to its owned
`compatibility/home` directory within the service's writable workspace. A timeout
may prevent upstream teardown: retain evidence and restore captured device
settings before retrying or returning the device to ordinary use.

The dedicated ADB service stops only its own foreground process; it must not
issue a global `adb kill-server` that could stop another listener. Restarts are
limited to three starts per minute. Before starting a trial, verify that the
service owns the listening server. An ADB client can otherwise auto-start an
unmanaged server after the service exits. Recover that conflict only after all
trials stop and the conflicting process's executable and owner are verified.
