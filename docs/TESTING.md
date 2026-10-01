# DiamaneOS testing

How to run the tool tests and the Fairphone 6 (FP6) test workflows, and what
they have shown on stock software. Terms: see the
[threat model](THREAT_MODEL.md#terms).

## Run the tool tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python bin/diamaneos endpoints validate
```

The suite covers the baseline, CLI and endpoint tests; only endpoint schema
validation needs the development dependencies (the baseline collector uses the
standard library). Device runs are separate hardware evidence. Test counts are
recorded in the acceptance evidence for the exact tree.

[SIGNING.md](SIGNING.md) describes `bin/diamaneos signing roles`,
`signing inventory` and `signing verify`. Their unit fixtures cover malformed
archives, development-key versus signed-output separation, presigned-package
refusal, source/role drift, incomplete proofs, path escape and artifact
tampering, and sign nothing; real APK, APEX, AVB, full-OTA and delta-OTA
evidence comes from the builder and offline qualification there.

## Stock phone records

The [stock hardware report](../reports-public/stock-capabilities.json) gives a
result per component for one FP6 on the stated stock build, from
operator-observed stock diagnostics and app use plus selected ADB identity,
charging and throwaway-file transfer checks. Its software and boot state are
from the Android 15 arrival inspection (build unchanged, bootloader locked). A
later row, after the separately verified official Android 16 OTA, formatted a
throwaway 128 GB microSD as portable storage and passed a 4 MiB
create/read/hash/delete round trip; it names its own build and does not imply
the arrival build stayed installed. The report does not cover performance,
battery or custom-OS qualification, restore and unlock/relock (later validated
by FP6-025, see the installer recovery runbook) or
[baseline collector](#baseline-collector) acceptance.

The [stock-input inventory](../config/stock-inputs.json), the immutable,
hash-bound pre-restore selection snapshot of build environment v4, binds the
observed product and build to official factory-package URLs, byte sizes and
published SHA-256 values. The final Android 15 package and the EU Android 16
package first offered by the phone are verified recovery inputs: two complete
reads reproduced Fairphone's outer hash, the full ZIP CRC passed, required
members were present and all 76 embedded checksum-list files matched.

| Build | Role |
| --- | --- |
| `FP6.QREL.15.176.0` (Android 15) | Arrival build; the official OTA came later. |
| `FP6.QREL.16.100.0` (Android 16 EU, 2026-08-05 patch) | Accepted locked, green-verified stock checkpoint and EU restore selection; the Android 15 archive stays a historical verified input. |
| `FP6.QREL.16.111.0` (released 2026-09-28, 2026-09-05 patch) | Vendor-file input since 2026-09-30, not a tested restore input. Fairphone had not published its checksum, so the entry has two agreeing local reads, the MD5 from the official host's object metadata, the full ZIP CRC and all 76 embedded hashes; compare with Fairphone's value once published. |
| `FP6.QREL.16.104.0` (US) | Excluded as this EU phone's restore or flash input (it offered 16.100.0); still a planned comparison input for the separately controlled US FP6. |

Archive checks prove no restore, rollback eligibility, AVB/relock safety or
bootloader operation; FP6-025 proved those for the exact Android 16 EU archive
(restore, AVB/rollback review, critical relock, normal relock, locked-green
boot, final cold-boot hardware checks).

- Read recovery copies from two independent private storage locations (not two
  directories on one volume), matching the recorded byte count and SHA-256,
  before destructive work. Custody, provider and account evidence stay private;
  a path or filename never replaces content verification.
- The factory script wipes user data by default and needs normal and critical
  unlock. Its fallback that continues without a checksum tool is forbidden:
  first verify the whole archive against the independently read official hash,
  and its embedded declared files.
- The regional package must match the build the phone offers, never the
  maintainer's location.
- The validated `super.img` has checksummed liblp 10.2 metadata with all seven
  slot-A logical partitions populated and every slot-B counterpart at zero
  bytes/extents. The script selects A; never select, boot or fabricate B as a
  repair.
- After relock, rollback locations 0–4 matched the authenticated target values
  `0,1,1785888000,1785888000,1785888000`; 5–31 were zero. No raw partition
  bodies or per-partition hashes were collected; package-derived topology is
  labelled derived, not a device dump.
- Relock trap: with the script's defaults the first unlocked boot set
  `get_unlock_ability` to `0` and greyed out the OEM control. No lock was tried
  at zero. The accepted path used a same-directory copy with only the
  `REBOOT_TO_BOOTLOADER` toggle enabled, repeated the verified wipe/flash,
  required ability `1` before the critical and the normal lock, and proved
  final ability `0`, both locks closed and green Verified Boot. See the
  [installer recovery runbook](../../installer/docs/recovery-preflight.md).

## Regional FP6 qualification

EU is the first target. US stays `UNVERIFIED` until both steps pass on the
second maintainer's planned US-region FP6 (availability and state not yet
evidenced); no EU result, version-label similarity or reference-ROM support
replaces that.

1. Before claiming one image for both regions, compare the exact EU and US
   stock partition/super layout, AVB chain and rollback locations, boot and
   vendor images, firmware, VINTF, init/SELinux policy, feature/permission
   files, SKU properties, modem profiles and carrier/regulatory configuration.
   Only byte-identical files enter the common set unadapted, recorded apart from
   the regional delta. Select a runtime delta by an observed trustworthy
   hardware/boot SKU property, never locale, language, timezone or location; a
   boot-critical delta needs separately bound variants.
2. Run the applicable hardware matrix and the declared T-Mobile-oriented voice,
   SMS, data, 5G, VoLTE and VoWiFi tests on the US phone.

## Baseline collector

`bin/diamaneos baseline capture` makes a bounded read-only ADB capture; the
[test-host recipe](../deploy/test-host/README.md) covers host setup and
isolation. Fixture and dry-run checks touch no hardware:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/baseline -t .
.venv/bin/python src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
bin/diamaneos baseline capture --dry-run
```

Live (`<PRIVATE_ROOT>` is private, outside Git):

```sh
bin/diamaneos baseline capture --target <serial> \
  --device-role <mapped-role> \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --rig-config <PRIVATE_ROOT>/rig.json \
  --conditions "<env>" \
  --raw-dir <PRIVATE_ROOT>/runs/<run-id>/ --output report.json
```

`--raw-dir` gives a fresh per-run subdirectory with per-file sha256 in the
evidence refs; without it the run is ephemeral, not evidence. On a controlled
rig, `--device-role`, `--device-map` and `--rig-config` (all or none) hold the
role lock for the whole capture. Reports carry a device alias, never serials.
Graphics capture is fixed to `dumpsys gfxinfo com.android.systemui`, since
unscoped `gfxinfo` can overflow the output with package state. Limits: 20 s per
adb call, 256 KiB streamed per command (byte-exact, invalid UTF-8 visible);
large traces stay outside Git, by hash.

Five fixtures and fake-adb live-path tests prove: valid output `ok`;
truncation, timeout or overflow `error` (never averaged as zero); missing
service `unsupported`, bare `unknown` operator values `ok`; IMEI, IMSI, ICCID,
EID, phone, account, MAC and serial redacted with context, across the full
report; ambiguous target refused before any adb command; over-producers killed
at the cap; failed captures keep partial stdout/stderr with hashes; device loss
stays `error`/partial, never `unsupported`-complete. At collector revision
`73452925347dba523cf22b86df58789527b177dc`, one host acceptance run on stock
Android 15 gave five `ok`, one explicit `unsupported` and zero errors (raw
bundle private): the bounded read-only capture path works, not custom-OS
compatibility or comparative performance. Standalone ADB checks do not count.

## Controlled USB rig

`bin/diamaneos rig` gives identity-bound status, explicit port power and
battery maintenance for an independently qualified switchable hub, binding each
role (a non-identifying phone name) to an exact ADB map entry, logical port and
USB topology path. It refuses `cycle`, requires USB2 and USB3 companion ports
to agree, verifies the selected role after power-on and that other present
mapped roles kept their paths. An active or unreadable `.partial` run in any
configured private output root blocks maintenance and power changes; the start
guard rejects persistent operation leases and restores a maintenance-held role
to its verified powered path before partial state is created.

```sh
bin/diamaneos rig validate --config <PRIVATE_ROOT>/rig.json
bin/diamaneos rig dry-run --config <PRIVATE_ROOT>/rig.json
```

The scheduled maintenance unit stays disabled until every deployed test starter
hands over from role lock to partial state without a race and the operator has
accepted each battery policy. Hub qualification, least-privilege device-node
access, service-owned ADB and reboot recovery are deployment requirements, not
unit-test results.

## Stock performance baseline

`config/baseline.json` is the stock performance and camera protocol:

```sh
bin/diamaneos baseline protocol validate
bin/diamaneos baseline pilot --dry-run
```

Unsupported or failed measurements never become numeric zero.

**Connected pilot.** Shorter than the declared series and always labelled
`PILOT_ONLY_NOT_BASELINE_EVIDENCE`. It checks the private role mapping before
ADB, needs operator confirmation of an unlocked phone and visible 50%
brightness, verifies display, radio, SIM, battery and build itself, and needs
the live power service to report an awake display; any mismatch stops it before
the workload. It records one cold launch (force-stopped package, `COLD` state)
and one warm launch (BACK, process resident, `WARM` state) per bound stock app,
each with a positive time; a 10-second package-scoped Settings frame sample
with at least one rendered frame per swipe; a bounded 30-second four-worker CPU
load and 30-second cooldown; memory signals before and after; and an ending
battery/charging snapshot. It force-stops only named packages, never clears app
data and keeps Android's thermal policy; HAL battery/skin values enforce
conservative stop thresholds and read-only sysfs thermal-zone type/temp pairs
are kept raw. The shell cannot read `/proc/pressure/memory` on the locked user
build, so that is recorded `UNSUPPORTED` and bounded `dumpsys meminfo`, selected
`/proc/vmstat` deltas and run-bounded LMKD and ActivityManager logs are used;
activity kills are not called LMKD kills without corroboration.

```sh
bin/diamaneos baseline pilot \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --rig-config <PRIVATE_ROOT>/rig.json \
  --run-id <run-id> \
  --output <PRIVATE_ROOT>/baseline-runs \
  --operator-confirmed-display-50 \
  --operator-confirmed-unlocked \
  --conditions "<stock build, USB path and controlled setup>"
```

It writes immutable private raw files with SHA-256 references and a
serial-redacted result. The fixed-scene camera and physical-disconnect idle
pilots stay `NOT_RUN` until their supervised steps are done; a connected pass
does not complete FP6-022 or authorize an Android-version comparison.

**Declared connected run.** After the pilot, inspect the plan with
`bin/diamaneos baseline connected dry-run`. `run` uses three cold and three
warm launches per app, three 60-second Settings frame runs and a 15-minute load
with 10-minute cooldown, under a shared series ID and repeat index 1 or 2 (both
are required). A successful workload verifies all evidence hashes and the
unchanged protocol before atomically publishing.

```sh
bin/diamaneos baseline connected run \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --rig-config <PRIVATE_ROOT>/rig.json \
  --run-id <unique-run-id> \
  --series-id <shared-series-id> \
  --repeat-index 1 \
  --expected-build FP6.QREL.16.100.0 \
  --output <PRIVATE_ROOT>/baseline-runs \
  --operator-confirmed-display-50 \
  --operator-confirmed-unlocked \
  --conditions "<stock build, USB path and controlled setup>"
```

**Boot timing** is separate, as a mid-run reboot would change residency,
thermal state and order (inspect with `bin/diamaneos baseline boot dry-run`).
One `restart` repetition does exactly three authorized ordinary `adb reboot`
operations, each timed on the host monotonic clock for ADB loss, authorized-ADB
return, `sys.boot_completed=1` and boot-animation end (`service.bootanim.exit=1`
or `init.svc.bootanim=stopped`, noting which); ready is the later of the last
two, following the AOSP boot-completion boundary within host-polling limits.
Raw observer records and reboot output are hash-bound; the serial stays out. A
pass publishes at once; two repetitions share one series ID.

```sh
bin/diamaneos baseline boot restart \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --rig-config <PRIVATE_ROOT>/rig.json \
  --run-id <unique-run-id> \
  --series-id <shared-series-id> \
  --repeat-index 1 \
  --expected-build FP6.QREL.16.100.0 \
  --output <PRIVATE_ROOT>/baseline-runs \
  --operator-authorized-reboots \
  --operator-confirmed-permanent-state \
  --conditions "<stock build, permanent SIM/Wi-Fi state and controlled rig>"
```

**Cold power-on** is not restart timing: three repetitions from a verified
powered-off state, from the visible power-button press to the first usable
stock UI, on continuous fixed-frame-rate source media or an equally reviewable
recording. USB enumeration, `adb reboot` and a stopwatch started after the
press do not substitute. Keep media private and keep failed tries.

**Idle pilot** (staged, five minutes). `baseline idle start` records build, app
versions, display, Wi-Fi/SIM and battery, performs an authorized
`dumpsys batterystats --reset`, sends `KEYCODE_SLEEP` and polls up to five
seconds for two consecutive Asleep/Dozing observations with the built-in panel
`OFF` (stock Android 15 reports `Dozing` with the panel off, so wakefulness
alone proves nothing). `baseline idle observe-disconnect` then uses:

- `physical-unplug` (default): stable ADB loss is recorded; VBUS removal stays
  an operator attestation. Keep the cable out, screen off and phone untouched
  until `baseline idle status` reports the interval complete, run
  `baseline idle finish --wait-for-reconnect`, reconnect only at
  `READY_TO_RECONNECT`; two consecutive authorized-ADB observations start the
  ending capture. The legacy finish path accepts an already connected target,
  but its invocation time counts as reconnect time against the 60-second
  tolerance.
- `--disconnect-method verified-rig-port-off` with
  `--rig-config <PRIVATE_ROOT>/rig.json` on `start`, `observe-disconnect` and
  `finish` (qualified hub, cable attached): after verifying screen-off it
  switches only the role's port off, verifies USB2/USB3 power-off and ADB
  absence and records the redacted controller result; at the threshold it powers
  the port on, verifies the role on the same path and the other phone
  undisturbed, records stable ADB and captures the end state. No attestation is
  needed and hub power-off is never presented as an unplug; a failed observation
  tries to restore the port.

**Declared idle run.** Give both `--declared-repeat-index` (`1` or `2`) and a
shared valid `--series-id` to `dry-run` and `start`, binding the immutable
report to `DECLARED_STOCK_BASELINE_EVIDENCE`, the eight-hour duration, series
and repeat; one alone fails closed, neither gives the pilot. Both eight-hour
repetitions are required. Wi-Fi and telephony output is reduced in memory to
connection/registration booleans (no SSID, BSSID, subscriber or cell IDs).

`baseline idle series launch` can own both repetitions on the tester without
SSH or another computer. It fails closed unless scheduled rig maintenance is
disabled, the role is authorized, the operator separately authorizes both
batterystats resets and confirms unchanged display, unlock, no-interaction and
no-planned-outage conditions. The worker wakes the no-lock phone, runs repeat
1, restores the port at eight hours, waits up to four hours for a verified
100%/full state and runs repeat 2 (over 16 hours in all). It finishes only on
the explicit `ready_to_reconnect` flag from `finish`'s boot-relative check; the
rounded `remaining_seconds` is display-only (`0.0` may still mean closed), and
missing or invalid interval state fails the series; duration and tolerance
are unchanged. `baseline idle series status --series-id <shared-series-id>` is
read-only; each reset authorization
is consumed separately. A tester reboot, rejected preflight, late or
non-comparable finish, recharge timeout, identity/path mismatch or unexpected
rig state fails the series and tries to restore the port. Connectivity is
observed at start and finish only. Re-enable battery maintenance only after the
series ends and its result paths are reviewed.

**Camera.** A closed cardboard box holds fixed green-timer and Johnson's
Buds-box subjects, one marked stand position, a secured USB lamp and a nominal
21.25 cm ±0.25 cm phone-to-target distance; the MacBook powers the lamp and
drives ADB from outside. Align from the fixture marks and reference photographs
(millimetre variation accepted), close blinds and door, switch the room light
off, then close the box. Main and ultrawide use the rear-facing orientation;
for the front camera turn the phone 180 degrees in place. Record the lamp's
colour mode (1, 2 or 3; labels neutral white, cool white and warm are not
measured temperatures), brightness (1 to 10) and every original's camera, mode,
zoom and tap-focus; never edit or transcode source media.
`bin/diamaneos baseline camera dry-run` shows the pilot order; add `--declared`
to `dry-run` and `start` only after the pilot passes (nine standard matrix
captures twice, six advertised-mode survey captures once: 24 originals). Staged
`start`, `capture` and `finalize` keep one private run open across lamp
changes; each `capture` snapshots the media directory, fires one tap-focus and
shutter, requires exactly one new original, compares device and pulled SHA-256
and records the pre-capture UI hierarchy. The survey covers rear-main 1x
Portrait, Pro and Super Night, the 2x and Super Macro controls and the front
multi-person view; the standard front capture uses single-person view with Face
Beauty off. Pano and motion modes do not apply. Keep a run with a wrong physical
condition via `quarantine --status NON_COMPARABLE` and the exact reason.

## Staged device runner

The runner runs reviewed suites on an exact target with a private role map
([BUILD.md](BUILD.md)) and writes a checkpointed, schema-versioned run:

```sh
bin/diamaneos test run --suite smoke --dry-run
```

Live read-only smoke run, as the unprivileged test account, without printing
the authorized serial:

```sh
bin/diamaneos test run \
  --suite smoke \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --evidence-kind real-device \
  --run-id <run-id> \
  --conditions "<build, USB, network and power setup>" \
  --output <PRIVATE_ROOT>/test-runs
```

Each case records stage, preconditions, oracle, build/firmware, duration,
evidence kind, status and redacted/raw references. Repeatable `--stage` runs a
`SELECTED` subset (the rest `NOT_RUN`), never a complete-suite claim. An
unavailable optional capability is `SKIP` with a reason; a missing required
case never passes. The runner is fail-stop (timeout or overflow
`HARNESS_ERROR`, device loss `BLOCKED`, oracle mismatch `FAIL`, later cases
`NOT_RUN`), and results and partial streams survive interruption. Exit codes,
no substitute for per-case completeness: `0` complete or selected success, `2`
invalid arguments or data, `3` prerequisite/target/lock blocked, `4` test
rejection, `5` execution failure, timeout or interruption. Rerun unresolved
cases under a new run ID with matching retry input and raw hashes, suite, role
and build:

```sh
bin/diamaneos test run \
  --suite smoke \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --evidence-kind real-device \
  --run-id <new-run-id> \
  --rerun-from <PRIVATE_ROOT>/test-runs/<prior-run-id>/result.json \
  --conditions "<repeat setup and any deliberate differences>" \
  --output <PRIVATE_ROOT>/test-runs
```

Destructive suites also need `--destructive` and a map entry with
`disposable: true`; the v1 runner cannot flash or wipe, so an
`installer-runbook` case stays `BLOCKED` for the reviewed operator action and
is never marked `PASS` for passing the gate. Synthetic contract tests cover
multiple-device binding, wrong target, unavailable capability, timeout, device
loss, interruption/checkpoint, verified rerun selection, immutable collisions
and the destructive boundary:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover \
  -s tests/runner -t .
```

FP6-034 accepted the runner at signed commit
`089432fd6d82e10cce384747d4b0438120e60086` on the accepted test host: the
read-only `smoke` suite on locked stock Android 15 `FP6.QREL.15.176.0`
(`VS21`, user build), run on 2026-09-11, completed all six
cases in a schema-valid report (five `PASS`, one reasoned `SKIP` for the
optional IMS dumpsys service) with no failure, harness error or unresolved
check; sanitized report SHA-256
`c300c05ca6fcf787a2590aba19844a7d00cf53e397d35ff7a7b40733a56bb4e9`, all 19
private raw artifacts re-hashed. It accepts the runner and this read-only
evidence only, not custom-OS, compatibility, recovery, destructive, release or
signer qualification. Raw diagnostics and device identifiers stay out of
public Git.

### Carrier and telephony

`config/carrier-matrix.json` is the identifier-free plan for the two FP6
carrier profiles and the isolated peer:

```sh
bin/diamaneos carrier matrix validate
bin/diamaneos test run --suite telephony --dry-run
```

`plan_eligibility` (what a carrier page can show) is separate from
`observation_status` (provisioning, registration and FP6 behaviour): `PASS` or
`FAIL` needs build- and arrangement-bound evidence, an unavailable SIM or
unknown tariff stays `BLOCKED` or `NOT_RUN`, and unobserved firmware, APN or IMS
context is `UNRECORDED`. The `telephony` suite makes four allowlisted read-only
captures (`dumpsys carrier_config`, `dumpsys telephony.registry`, the private
raw `dumpsys phone` IMS/MMTEL context, the optional legacy
`dumpsys imsservice`), keeps raw streams private and only bounded, redacted
fields in `result.json`, and nothing from the phone dump. A reviewed case may
raise the 256 KiB stream default to the hard 1 MiB ceiling, as the stock
telephony-registry snapshot does; overflow stays a fail-stop harness error.
Success means capture, not working voice,
SMS, data, VoLTE, WiFi Calling, 5G or emergency behaviour. Run one confirmed
carrier/SIM arrangement at a time:

```sh
bin/diamaneos test run \
  --suite telephony \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --evidence-kind real-device \
  --run-id <run-id> \
  --conditions "<stock build, carrier, SIM type, defaults, USB, Wi-Fi and radio setup; no subscriber identifiers>" \
  --output <PRIVATE_ROOT>/runs/<run-id>/telephony
```

Calls and SMS stay human-led, to private allowlisted destinations with
synthetic text: record inbound/outbound voice and SMS, data transitions, VoLTE
data continuity, provisioned WiFi Calling, observed 5G, audio routes,
reboot/reconnect and selected defaults, restoring connectivity after each case.
**Never dial a live emergency number.** eSIM deletion or reprovisioning needs
separate explicit authorization; the suite never changes a subscription.

Identifier-free results on the locked stock FP6, valid only for the observed
build:

| Check | Shown | Result SHA-256 |
| --- | --- | --- |
| Vodafone eSIM data and 5G display (EU, authorized) | Wi-Fi off, cellular default route, HTTP 200 with a 559-byte HTTPS body, LTE with NR-NSA override, Wi-Fi restored; holds for that profile, place and time. Not that every byte used NR, coverage elsewhere, voice, SMS, VoLTE, WiFi Calling, dual-SIM defaults or eSIM lifecycle. | `1e3572527e15ec1ea6f02b8c3ac86a80ed59f9854ef8ae74409e4f6b9fc41200` |
| Vodafone eSIM disable/re-enable (authorized, in Settings) | Registered, then no voice/data service, then registered; no credentials asked. Never deleted, downloaded, transferred or reprovisioned. | `3be986ec32c64b6d70a20f6cb634a752de5c2fce69fc661bdf9b947ea7edbab0` |
| Dual SIM (plus an active Blau 9 Cent physical SIM) | Three required captures passed, legacy `imsservice` skipped, 15 evidence refs verified; Vodafone default for voice, SMS and data. | `f982b30ea14ab141bc2dc787b01ea05114db742a2bf5f303b45e00e46ba32908` |
| Blau mobile data (authorized) | Blau data, Wi-Fi off, cellular route, HTTP 200 with 559-byte body, Wi-Fi and Vodafone default restored, 15 files verified. Not Blau voice, SMS, VoLTE, WiFi Calling or 5G. | `c9e4c12e61d5ea5f23521175d9e06a2bbc5512e352e57978b4022bf29e7f12d4` |
| Final campaign (peer: Pixel 2 with active Vodafone physical SIM) | Both profiles: inbound/outbound calls and synthetic SMS both ways; active call, LTE voice and IMS registration; earpiece, speaker and concurrent data on outbound LTE calls; WiFi Calling in airplane mode on approved Wi-Fi (indicator, two-way audio, IMS, IWLAN/WLAN agreed); subscriptions, Wi-Fi and Vodafone defaults recovered after reboot. Emergency calling not exercised. Closes the Vodafone and Blau voice, SMS, VoLTE and WiFi Calling rows. | Summary `27c27091a26675d0e5cfec879e2c3fa42dd15a8f868bbf73f58a60cf2ad35f5e`; archive `e0ab9551aab1648ce5e7477fa99b480d9b9e5f498e99f94974753a36ff052726` (nine result hashes, 139 evidence files rechecked) |

The no-package Blau 9 Cent tariff stays `BLOCKED` for 5G because official
material does not consistently establish its 5G eligibility (no FP6 or OS
failure); retest only with an unambiguously 5G-eligible option.

## Other checks

**Compatibility.** [COMPATIBILITY.md](COMPATIBILITY.md) and
`config/test-suites.json` cover the Android 17/API 37 suites, host
requirements, fixtures, interlocks and result parser. A stock Android 16 trial
proves only the harness and collection path; no custom-OS pass or inaccessible
partner-suite completion is claimed, and a selected-test PASS is not full-suite
coverage. Keep trial/parent identity and manual XML exports
([manual report collection](COMPATIBILITY.md#manual-report-collection)). The
original early-risk ledger stays an input to final case selection; complete
CDD coverage and all applicable CTS, CTS Verifier, VTS and modular suite
results are release-gate work on the actual FP6 `user` candidate.

**Overlays.** [OVERLAYS.md](OVERLAYS.md) explains
`bin/diamaneos overlays check`; its tests use fixture trees and local Git
remotes, no network:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/overlays -t .
```

**Fonts.** `bin/diamaneos fonts check` reads a product
`fonts_customization.xml` and its fonts as Android does at boot
(FontCustomizationParser, FontListParser and SystemFonts at the pinned
release); a mistake there can drop every system font or stop boot. `--help`
lists the findings. Tests use synthetic fonts, no network. Run it on the build
module whenever the file or a font changes, with the weights the text styles
use, and on a built image before flashing:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/fonts -t .
```

```sh
bin/diamaneos fonts check --module-dir <FONTS_MODULE_DIR> --weights <WEIGHTS> --strict
```

```sh
bin/diamaneos fonts check --xml <PRODUCT_OUT>/product/etc/fonts_customization.xml \
    --font-dir <PRODUCT_OUT>/product/fonts --weights <WEIGHTS> --strict
```

It reads font tables without rasterising, cannot see the system font list (so
not whether a family replaces or an alias points at a system one), and is
stricter than Android's parser, which accepts a DTD and repeated attributes. A
pass is not a boot: `cmd font dump` and logcat stay part of the phone test.

## Endpoint contracts

See [ENDPOINTS.md](ENDPOINTS.md). `endpoints validate` (setup above) reports
inventory-only scope using `tests/endpoints/fixtures/services.json`, works in a
standalone clone and never looks for an infrastructure checkout. To accept a
real service selection, run the two-repository integration gate with the
actual path:

```sh
.venv/bin/python bin/diamaneos endpoints validate \
  --services /absolute/path/to/infrastructure/config/services.json
```

The tests exercise the CLI's validator: required fields, null or wrong types,
reference/ownership/isolation errors, bounded input, duplicate JSON keys,
Unicode byte limits and non-echoing privacy failures. Wire-shape examples check
204/body/time-unit conventions, not native cryptography or device
compatibility. Valid schema data still has implementation gates and is no
active deployment.

`tests/vendor/test_vendor_files.py` covers selected regular-file generation with
the real component validator and filesystem publication (repeat generation,
retained image metadata, altered input/output, missing notices, wrong stock
identity, absent dependency, unknown owner, traversal, special files,
concurrent publication, interrupted copying, private/public policy
separation), using synthetic bytes: no FP6 product closure or hardware result.
