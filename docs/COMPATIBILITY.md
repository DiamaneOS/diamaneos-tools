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
symlink, special file, unsafe path or oversized inventory. Package discovery
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
  "retry_result": null
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
scope. Move the qualified rig and disposable FP6 to this host only after the
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
