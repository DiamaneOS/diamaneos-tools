# DiamaneOS Testing

## Stock hardware observations

The [stock hardware report](../reports-public/stock-capabilities.json) records
observed component results for one Fairphone 6 on the stated stock build.
It combines operator-observed stock diagnostics and ordinary app use with
selected ADB identity, charging and disposable-file transfer checks. Each
component has its own result. A separately dated Android 16 stock follow-up
formatted a disposable 128 GB microSD as portable storage and completed a
4 MiB create/read/hash/delete round trip. The report-level software and boot
state still describe the original Android 15 arrival inspection; the microSD
row identifies its later build explicitly.

During the original arrival inspection, the stock build remained unchanged
and the bootloader remained locked. The removable-storage follow-up occurred
after the separately verified official Android 16 OTA; it does not imply that
the arrival build remained installed. Performance/battery measurements,
custom-OS qualification, restoration and unlock/relock acceptance require
their own evidence. This report also does not establish acceptance of the
baseline collector CLI described below.

## Stock recovery inputs

The [stock-input inventory](../config/stock-inputs.json) binds the observed FP6
product/build to official factory-package URLs, exact byte sizes and published
SHA-256 values. The final Android 15 package and the EU Android 16 package
explicitly offered by the phone are verified recovery inputs: for each, two
complete reads reproduced Fairphone's outer hash, the full ZIP CRC passed,
required recovery members were present and all 76 files declared by the
embedded checksum list matched. The phone remains on its received Android 15
build. This is archive verification, not a successful update, restore or
relock.

Recovery copies must be read from two independent storage locations and match
the recorded byte count and SHA-256 before destructive work. Two directories on
one physical volume do not meet that requirement. The inventory records a
verified private primary copy plus an owner-confirmed independent cloud copy;
both cloud-copy SHA-256 values reproduced the manifest. A path or filename
never substitutes for content verification.

The official factory script wipes user data by default and requires both normal
and critical bootloader unlock. It also contains a fallback that continues when
no checksum utility is found; that fallback is prohibited by the project
recovery procedure. Verify the complete archive against the independently read
official hash and verify its embedded declared files before execution. The
regional Android 16 package must match the build explicitly offered by the
phone; do not infer EU/US selection from a maintainer's location. For this
EU device the observed offer is `FP6.QREL.16.100.0`, so the US
`FP6.QREL.16.104.0` package is excluded as a restore or flash input for this
device. It remains a planned comparison input for the separately controlled US
FP6; “excluded here” does not mean excluded from regional qualification.

Raw partition bodies, per-partition device hashes and rollback-index values
were not available under the accepted locked, non-root capture. They remain
unknown. Package contents are separately derived inputs and must not be
misreported as device dumps.

## Regional FP6 qualification

EU is the initial supported hardware target. The second maintainer's planned
US-region FP6 will supply the independent US stock and candidate evidence once
available; no EU result, version-label similarity or reference-ROM support
substitutes for that device run.

Before one image is claimed for both regions, compare the exact EU and US stock
inputs for partition/super layout, AVB chain and rollback locations, boot and
vendor images, firmware, VINTF, init/SELinux policy, feature/permission files,
SKU properties, modem profiles and carrier/regulatory configuration. Record
byte-identical common inputs separately from the regional delta. If a delta is
selected at runtime, bind it to an observed trustworthy hardware/boot SKU
property rather than locale, language, timezone or location. A boot-critical
delta requires separately bound variants.

The US candidate must then pass the applicable hardware matrix on the US phone
and the declared T-Mobile-oriented voice, SMS, data, 5G, VoLTE and VoWiFi
tests. Until both stock comparison and candidate runs are accepted, report US
as `UNVERIFIED`, not supported or assumed compatible.

## Baseline collector

The reusable test-host setup and isolation boundary are documented in the
[test-host deployment recipe](../deploy/test-host/README.md).

Fixture and dry-run checks do not touch hardware:

```sh
python3 -m unittest discover -s tests/baseline -t .
python3 src/diamaneos_tools/baseline.py --fixture tests/baseline/fixtures/valid.json
bin/diamaneos baseline capture --dry-run
```

Live command:

```sh
bin/diamaneos baseline capture --target <serial> \
  --device-role <mapped-role> \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --rig-config <PRIVATE_ROOT>/rig.json \
  --conditions "<env>" \
  --raw-dir <PRIVATE_ROOT>/runs/<run-id>/ --output report.json
```

Raw storage: per-run subdirectory, reuse refused, per-file sha256 in
evidence refs; without --raw-dir the run is ephemeral (not accepted
evidence). On a controlled rig, the three role/map/rig arguments hold the
selected role lock for the complete live capture; supply all three or none.
Public reports carry a device alias only; serials stay private.
Graphics capture is fixed to `dumpsys gfxinfo com.android.systemui`. An
unscoped `gfxinfo` query can enumerate enough installed-package state to
exceed the bounded collector output, while SystemUI provides a stable,
non-personal host-readiness target. The 256 KiB per-command cap still applies.
The full-suite command below includes baseline, CLI and endpoint validation
tests; device runs remain separate hardware evidence.

5 fixtures + fake-adb live-path tests prove the contract: valid→ok,
truncated/timeout/overflow→error (never averaged as zero),
missing-service→unsupported while bare-`unknown` operator values stay ok,
sensitive→IMEI/IMSI/ICCID/EID/phone/account/MAC/serial redacted with context
preserved (full-report checked, not just case fields); ambiguous target
refuses before any adb command. Over-producers are killed at the byte
cap (termination proven, not just detected); failed captures keep partial
stdout+stderr evidence with hashes; device-gone stays error/partial, never
unsupported-complete. At collector revision
`fddadbb9d1ac6c7853b4add32785703bb90ef11e`, one stock Android 15 FP6 host
acceptance run produced five `ok` cases, one explicit `unsupported` service and
zero errors; its raw bundle and device identity remain private. That proves the
bounded read-only capture path, not custom-OS compatibility or comparative
performance. Standalone ADB checks do not establish collector acceptance. Host
bounds: 20s per adb call plus 256KB streaming byte cap
(byte-exact, invalid UTF-8 kept visible). Large traces/samples stay outside
git with hashes. Use the current full-suite command below; test counts are recorded in the acceptance evidence for the exact tree.

## Controlled USB rig

`bin/diamaneos rig` provides identity-bound status, explicit port power and
battery-maintenance operations for an independently qualified switchable hub.
The private configuration binds each role to an exact ADB map entry, logical
port and USB topology path. The controller refuses `cycle`, checks the USB2 and
USB3 companion port states agree, verifies the selected role after power-on,
and checks that every other present mapped role remains on its original path.

Configuration validation and planning contact no device:

```sh
bin/diamaneos rig validate --config <PRIVATE_ROOT>/rig.json
bin/diamaneos rig dry-run --config <PRIVATE_ROOT>/rig.json
```

An active or unreadable `.partial` run in any configured private output root
inhibits maintenance and ordinary power changes. The scheduled maintenance
unit remains disabled until all deployed test starters have a race-free
role-lock-to-partial-state handoff and the owner has accepted each battery
policy. Hub qualification, least-privilege device-node access, service-owned
ADB and reboot recovery are deployment requirements, not results of unit
tests. See the [test-host deployment recipe](../deploy/test-host/README.md).
The start guard also rejects persistent operation leases and automatically
restores a maintenance-held role to its verified powered path before partial
test state is created.

## Stock performance protocol and pilot

`config/baseline.json` is the consumed FP6 stock-performance and camera
protocol. Validate it without contacting a device or creating output:

```sh
bin/diamaneos baseline protocol validate
bin/diamaneos baseline pilot --dry-run
```

The connected pilot is shorter than the declared series and is always labelled
`PILOT_ONLY_NOT_BASELINE_EVIDENCE`. It checks an exact private device-role
mapping before ADB, requires the operator to confirm an unlocked phone and a
visible 50% brightness setting, and independently verifies the remaining
display, radio, SIM, battery and build controls. The live power-service reading
must also report an awake display; an earlier operator confirmation is not
treated as current state. A mismatch stops before the launch, frame or thermal
workload.

The live pilot records one cold and one warm launch for each bound stock app.
Cold requires a force-stopped package and Android's `COLD` launch state; warm
finishes the cold activity with BACK, verifies that its process remains
resident, and requires Android's `WARM` state. Both require a positive reported
time. The 10-second package-scoped Settings frame sample must record at least
one rendered frame per completed swipe. The remaining connected sequence is a
bounded 30-second four-worker CPU load plus 30-second cooldown, before/after
memory signals, and an ending battery/charging snapshot. It force-stops only
the named packages, never clears app data, and uses Android's existing thermal
policy. Current HAL battery/skin values enforce conservative stop thresholds;
read-only sysfs thermal-zone type/temp pairs are retained as additional raw
observations.

On the locked stock user build, `/proc/pressure/memory` is not readable by the
shell user. The pilot retains that permission failure as `UNSUPPORTED` and uses
bounded `dumpsys meminfo`, selected `/proc/vmstat` deltas, and run-bounded LMKD
and ActivityManager event logs. Activity kill events are not called LMKD kills
without corroborating evidence. Unsupported and failed measurements are never
converted to numeric zero.

Live connected-pilot shape (private values substituted by the operator):

```sh
bin/diamaneos baseline pilot \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --rig-config <PRIVATE_ROOT>/rig.json \
  --run-id <run-id> \
  --output <PRIVATE_ROOT>/baseline-runs \
  --ambient-start-c <room-thermometer-reading> \
  --operator-confirmed-display-50 \
  --operator-confirmed-unlocked \
  --conditions "<stock build, USB path and controlled setup>"
```

The command produces immutable private raw files with SHA-256 references and a
serial-redacted result. It covers only the connected pilot. The fixed-scene
camera pilot and physical-disconnect idle pilot remain explicit `NOT_RUN`
phases until their supervised operator steps are completed; a connected-pilot
pass does not complete FP6-022 or authorize an Android-version comparison.

After that harness pilot succeeds, inspect the declared connected plan with
`bin/diamaneos baseline connected dry-run`. The declared `run` action consumes
the protocol's three cold and three warm launches per app, three independent
60-second Settings frame runs, and the 15-minute load plus 10-minute cooldown.
It requires a shared series ID and repeat index 1 or 2 so two whole runs cannot
be mistaken for unrelated samples. A successful workload remains
`AWAITING_AMBIENT_END` in its `.partial` directory; immediately read the room
thermometer and supply that directory to `finalize`. Finalization verifies all
evidence hashes and the unchanged protocol, records the ending temperature,
and makes an out-of-range or over-tolerance run `NON_COMPARABLE` rather than a
pass. Both finalized whole-run repetitions are required for the declared
connected baseline.

Boot timing is a separate declared workflow because rebooting in the middle of
the connected sequence would change app residency, thermal state and run
order. Inspect it without touching a device using `bin/diamaneos baseline boot
dry-run`. One `restart` repetition issues exactly three explicitly authorized
ordinary `adb reboot` operations. For each, the host monotonic clock reports
ADB unavailability, authorized-ADB return, `sys.boot_completed=1`, and boot
animation completion separately. The latter accepts either
`service.bootanim.exit=1` or `init.svc.bootanim=stopped` and records which
property supplied the signal; the ready value is the later of boot completion
and boot-animation completion. The raw observer record and reboot stdout/stderr
are hash-bound, while the private ADB serial is excluded from the structured
result. This follows the AOSP boot-completion boundary while preserving the
limits of host-side polling. A passed run remains `AWAITING_AMBIENT_END` until
`finalize` verifies its evidence and ending room temperature. Two whole
repetitions share one series ID.

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
  --ambient-start-c <room-thermometer-reading> \
  --operator-authorized-reboots \
  --operator-confirmed-permanent-state \
  --conditions "<stock build, permanent SIM/Wi-Fi state and controlled rig>"

bin/diamaneos baseline boot finalize \
  --run-dir <PRIVATE_ROOT>/baseline-runs/<unique-run-id>.partial \
  --ambient-end-c <room-thermometer-reading>
```

Do not call that result a physical cold-boot time. Cold power-on has a
different declared method: three repetitions from a verified powered-off
state, timed from the visible physical power-button press to the first visibly
usable stock UI using continuous fixed-frame-rate source media or an
equivalently reviewable recording. USB enumeration, `adb reboot`, a stopwatch
started after the button press, and restart timing are not substitutes. Keep
that source media private and retain failed attempts rather than silently
discarding them.

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
  --ambient-start-c <room-thermometer-reading> \
  --operator-confirmed-display-50 \
  --operator-confirmed-unlocked \
  --conditions "<stock build, USB path and controlled setup>"

bin/diamaneos baseline connected finalize \
  --run-dir <PRIVATE_ROOT>/baseline-runs/<unique-run-id>.partial \
  --ambient-end-c <room-thermometer-reading>
```

The idle pilot is a staged five-minute harness check. `baseline idle start`
captures the build, app versions, display, connected Wi-Fi/SIM and battery
state before performing an explicitly authorized `dumpsys batterystats
--reset`; it then sends `KEYCODE_SLEEP` and verifies that Android is Asleep or
Dozing while the authoritative built-in panel state is `OFF`. Stock Android 15
on the FP6 reports `Dozing` with the panel off, so wakefulness alone is not the
screen-off oracle.
The sleep transition is asynchronous: the harness polls for at most five
seconds and requires two consecutive non-awake/`OFF` observations rather than
sampling immediately after the key event.
`baseline idle observe-disconnect` supports two explicitly distinguished
methods. The default `physical-unplug` method records stable ADB loss, but
physical VBUS removal remains an operator attestation. Keep the cable
physically unplugged, the screen off and the phone untouched until `baseline
idle status` reports that the interval is complete. Read the ending
thermometer while the phone is still disconnected, then run `baseline idle
finish --wait-for-reconnect` with that value. Reconnect only after it prints
`READY_TO_RECONNECT`; the command records two consecutive authorized-ADB
observations and begins the ending capture immediately.

For a qualified controlled hub, pass `--disconnect-method
verified-rig-port-off` and `--rig-config <PRIVATE_ROOT>/rig.json` to `start`,
then pass the same rig config to `observe-disconnect` and `finish`. The observer
verifies screen-off state, selects the configured role and port, switches only
that port off, verifies USB2/USB3 power-off state plus ADB absence and records
the redacted controller result. Leave the cable attached. At the threshold,
`finish` switches the same port on, verifies the mapped role returns on the
same path, checks the other mapped phone was not disturbed, records stable ADB
presence and immediately captures the ending state. This method requires no
physical-disconnect attestation and never represents hub power-off as a cable
unplug. If disconnect observation fails, it attempts to restore the port.

The legacy physical finish path accepts a target that is already connected,
but its invocation time is the reconnect time and therefore remains subject
to the 60-second finish tolerance. After the pilot passes, select the declared
eight-hour state machine by supplying both `--declared-repeat-index` (`1` or
`2`) and one shared, valid `--series-id` to `dry-run` and `start`. The command
then binds the immutable report to `DECLARED_STOCK_BASELINE_EVIDENCE`, the
eight-hour protocol duration, the series, and its exact repeat. Omitting either
declared argument fails closed; omitting both remains the five-minute pilot.
The declared baseline requires both eight-hour repetitions. Wi-Fi and
telephony service output is reduced to
connection/registration booleans in memory: SSID, BSSID, subscriber and cell
identifiers are not persisted.

The camera fixture is a closed cardboard enclosure with fixed green-timer and
Johnson's Buds-box subjects, one marked phone-stand position, a secured USB lamp
and a 21.25 cm ±0.25 cm nominal phone-to-focus-target distance. The MacBook
powers the lamp and controls the phone over ADB from outside. Align the stand
and timer from the fixture marks and reference photographs; millimetre-scale
repositioning variation is accepted rather than claimed as exact registration.
Close the room blinds and door, switch off the room light, then close the box.
Use the unambiguous rear-facing orientation for the main/ultrawide captures and
reverse the phone 180 degrees in the same stand position for the front camera.
The lamp controls are recorded by their physical cyclic position (colour mode
1, 2 or 3) and discrete brightness level (1 through 10). The operator labels
the modes neutral white, cool white and warm, respectively; these labels are
not measured colour temperatures. Record the camera/mode/zoom and
tap-focus action with every original; do not edit or transcode source media.
Use `bin/diamaneos baseline camera dry-run` to inspect the exact pilot order
without contacting a device or creating output. Add `--declared` to `dry-run`
and `start` only after the pilot succeeds; that repeats the nine standard
matrix captures twice while retaining the six advertised-mode survey captures
once, for 24 originals total. Declared camera finalization enforces the ambient
range and within-run tolerance. The staged `start`, `capture` and `finalize`
actions keep one immutable private run open across manual lamp changes. Each
`capture` snapshots the camera media directory, triggers one
tap-focus and shutter action, requires exactly one new original, compares the
device and pulled SHA-256 values, and records the pre-capture UI hierarchy.
The advertised still-mode survey also retains rear-main 1x originals for
Portrait, Pro and Super Night, the exposed 2x and Super Macro controls, and the
front multi-person field of view. The standard front capture explicitly selects
the single-person view, and Face Beauty remains disabled. Pano and motion modes
remain inventoried but not applicable to this fixed-still fixture. If a physical
condition was wrong, preserve the partial run with
`quarantine --status NON_COMPARABLE` and state the exact exclusion reason.

## Staged device runner

The hardware runner consumes reviewed suites, requires an exact target plus a
private target-role map, and produces a checkpointed, schema-versioned run.
First validate the committed smoke plan without contacting ADB or writing an
output directory:

```sh
bin/diamaneos test run --suite smoke --dry-run
```

For a live read-only smoke run, create the private device map described in
[BUILD.md](BUILD.md), select the already authorized USB serial without printing
it, and run under the unprivileged test account:

```sh
bin/diamaneos test run \
  --suite smoke \
  --target "$TEST_DEVICE_TARGET" \
  --device-role harness \
  --device-map <PRIVATE_ROOT>/devices/test-host.json \
  --evidence-kind real-device \
  --run-id <run-id> \
  --conditions "<build, USB, network, power and ambient setup>" \
  --output <PRIVATE_ROOT>/test-runs
```

`--stage` is repeatable when an intentional subset is needed. A subset can
exit successfully but is labelled `SELECTED`, with the full expected inventory
still present as `NOT_RUN`; it is not a complete-suite claim. Every case names
its stage, preconditions, oracle, installed build/firmware, duration, evidence
kind, status and redacted/raw references. Optional capabilities are `SKIP` with
a declared reason when genuinely unavailable; a missing required case is never
converted to a pass.

The runner is fail-stop. A timeout or output overflow is `HARNESS_ERROR`, a
device loss is `BLOCKED`, an oracle mismatch is `FAIL`, and later selected
cases remain `NOT_RUN`. On interruption, completed results and partial streams
survive. Repeat only the unresolved cases under a new immutable run ID:

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

Retry input and raw hashes, the exact suite, role, and installed build identity
must still agree. Exit codes are `0` complete/explicit selected success, `2`
invalid arguments or data, `3` prerequisite/target/lock blocked, `4` test
rejection and `5` execution failure, timeout or interruption. The exit code
does not replace per-case completeness.

Destructive suites are a separate stage and additionally require
`--destructive` plus a private map entry with `disposable: true`. The v1 runner
contains no flash/wipe implementation: after enforcing those gates, an
`installer-runbook` case remains explicitly `BLOCKED` for the separate reviewed
operator action. Never change such a case to `PASS` merely because the gate was
accepted.

Runner contract tests are synthetic evidence, not hardware results:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover \
  -s tests/runner -t .
```

They cover multiple-device binding, wrong target, unavailable capability,
timeout, device loss, interruption/checkpoint, verified rerun selection,
immutable collisions and the destructive boundary.

### Carrier and telephony evidence

`config/carrier-matrix.json` is the public, identifier-free plan for the two
FP6 carrier profiles and the isolated peer. Validate it without contacting a
device or network:

```sh
bin/diamaneos carrier matrix validate
bin/diamaneos test run --suite telephony --dry-run
```

The matrix deliberately separates `plan_eligibility` from
`observation_status`. A carrier page can establish that a tariff is eligible,
but it cannot establish provisioning, registration or behavior on an FP6. A
`PASS` or `FAIL` row therefore requires build- and arrangement-bound evidence;
an unavailable SIM or unknown exact tariff remains `BLOCKED` or `NOT_RUN`.
Each FP6 profile also keeps its firmware, APN and IMS context explicit;
unobserved context is `UNRECORDED`, never inferred from a carrier page.

The `telephony` device suite performs only four allowlisted read-only captures:
`dumpsys carrier_config`, `dumpsys telephony.registry`, the private raw
`dumpsys phone` IMS/MMTEL context and the optional legacy `dumpsys imsservice`
interface. It writes complete raw streams only under the private output root
and keeps only bounded, redacted fields in `result.json`. The phone-service
dump has no public-safe field allowlist, so its contents never enter the
structured report.
A reviewed case may raise the default 256 KiB stream limit up to the runner's
hard 1 MiB ceiling; the stock FP6 telephony-registry snapshot uses that ceiling
because its measured output exceeded the default. Other cases retain the
smaller default, and an overflow remains a fail-stop harness error.
A successful suite means those observations were captured; it does not prove
voice, SMS, mobile data, VoLTE, WiFi Calling, 5G or emergency behavior.

The locked EU stock FP6 Vodafone eSIM check recorded mobile data and a local
5G display-state observation separately from that read-only context capture.
With Wi-Fi initially providing the default route and mobile data already
enabled, the bounded operator-authorized check disabled Wi-Fi, verified a
cellular default route, received HTTP 200 with a 559-byte HTTPS body, observed
the stock telephony display state as LTE with an NR-NSA override, then restored
and verified the original Wi-Fi route. The identifier-free result has SHA-256
`1e3572527e15ec1ea6f02b8c3ac86a80ed59f9854ef8ae74409e4f6b9fc41200`.
This establishes the two corresponding matrix rows only for the observed
stock build, carrier profile, place and time. It does not show that every
transferred byte used NR, establish coverage elsewhere, or prove voice, SMS,
VoLTE, WiFi Calling, dual-SIM defaults or eSIM lifecycle behavior.

The same retained Vodafone eSIM profile was then disabled and re-enabled
through Android Settings with explicit operator authorization. The observer
recorded registered service before the action, no registered voice or data
service while disabled, and registered service again after re-enable. Android
did not require activation credentials. The identifier-free result has
SHA-256
`3be986ec32c64b6d70a20f6cb634a752de5c2fce69fc661bdf9b947ea7edbab0`.
This closes only the retained-profile disable/re-enable lifecycle row on the
observed stock build. The profile was never deleted, downloaded, transferred
or reprovisioned, and those operations are not implied by the result.

The locked stock FP6 was subsequently observed with that Vodafone eSIM and an
active Blau 9 Cent physical SIM enabled together. The bounded read-only suite
again completed with three required captures passing and the unavailable
legacy `imsservice` interface explicitly skipped. All 15 raw and identity
evidence references verified; the identifier-free result has SHA-256
`f982b30ea14ab141bc2dc787b01ea05114db742a2bf5f303b45e00e46ba32908`.
The operator then observed Vodafone selected as the Android default for voice,
SMS and mobile data without changing any selection. This closes only the
dual-SIM context and default-subscription observation.

The operator later authorized a bounded Blau mobile-data check. The runner
temporarily selected Blau for mobile data, disabled Wi-Fi, verified a cellular
default route and received HTTP 200 with a 559-byte HTTPS body. It then
restored and verified both Wi-Fi and the original Vodafone mobile-data
default. All 15 referenced evidence files verified, and the identifier-free
result has SHA-256
`c9e4c12e61d5ea5f23521175d9e06a2bbc5512e352e57978b4022bf29e7f12d4`.
This closes only the Blau data row. It does not establish Blau voice, SMS,
VoLTE, WiFi Calling or 5G behavior by itself.

The final locked-stock campaign used the active Vodafone physical-SIM Pixel 2
as the permanent peer. Both FP6 profiles passed ordinary inbound and outbound
voice calls and synthetic, non-personal SMS in both directions. Connected-call
captures recorded active call state, LTE voice service and IMS registration;
the operator confirmed earpiece and speaker audio plus concurrent mobile-data
use during each profile's outbound LTE call. Both profiles also passed an
ordinary WiFi Calling call with airplane mode enabled and the approved Wi-Fi
connection active: the stock indicator, two-way audio, IMS registration and
IWLAN/WLAN transport evidence agreed. After an FP6 reboot, both subscriptions,
Wi-Fi and the Vodafone voice/SMS/data defaults recovered. Emergency calling
was not exercised.

The identifier-free final behavior summary has SHA-256
`27c27091a26675d0e5cfec879e2c3fa42dd15a8f868bbf73f58a60cf2ad35f5e`.
The private evidence archive has SHA-256
`e0ab9551aab1648ce5e7477fa99b480d9b9e5f498e99f94974753a36ff052726`;
all nine constituent result hashes and all 139 unique referenced evidence
files were reverified before export. These results close the Vodafone and Blau
voice, SMS, VoLTE and WiFi Calling rows for the observed stock build and
arrangement. The active no-package Blau 9 Cent tariff remains `BLOCKED` for
5G because the reviewed official material does not consistently establish its
5G eligibility. That limitation is neither an FP6 nor an OS failure. Retest
that row only after activating an option with unambiguous 5G eligibility.

Run it for one explicitly confirmed carrier/SIM arrangement at a time, using
the same private role map and target-binding rules as the smoke suite:

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

Ordinary call and SMS checks remain human-led and use only private allowlisted
test destinations and synthetic message text. Record inbound and outbound
voice/SMS, mobile-data transitions, VoLTE data continuity, provisioned WiFi
Calling, locally observed 5G, audio routes, reboot/reconnect behavior and the
selected voice/data/SMS defaults. Restore the starting connectivity state after
each case. Never dial a live emergency number. eSIM deletion or reprovisioning
requires a separate explicit operator authorization; the read-only suite never
changes a subscription.

FP6-034 hardware-harness acceptance used signed implementation commit
`33ed9ec01fb9aef6d5e01097dc472bb4f6d3988e` on the accepted test host. The
read-only `smoke` suite ran on the locked stock Android 15 FP6 build
`FP6.QREL.15.176.0` (`VS21`, user build) as
`fp6-034-stock15-20260911T234004Z`. The schema-valid, complete report selected
and completed all six expected cases: five `PASS`, one reasoned `SKIP` for the
optional IMS dumpsys service, and no failure, harness error or unresolved
check. The sanitized report SHA-256 is
`c300c05ca6fcf787a2590aba19844a7d00cf53e397d35ff7a7b40733a56bb4e9`;
all 19 referenced private raw artifacts reproduced their recorded hashes.
This accepts the runner and this stock read-only evidence only. It is not a
custom-OS, compatibility, recovery, destructive-operation, release or signer
qualification, and raw diagnostics/device identifiers remain outside public
Git.

## Compatibility target (provisional, design only; no device evidence)

Target: GOS branch-17 proposal vs FP6 Android-16 vendor (UNPROVEN pairing);
launch API 35 (shipped Android 15, verify on device); custom API unresolved
until sync. High-risk requirements + fixtures in
`tests/requirements/early-risks.json` (9 risks, all UNRESOLVED-assigned);
coverage ledger in `tests/requirements/coverage.json` (full CDD enumeration
completes during release-gate integration and manual compatibility qualification). Suite revisions are bound when the compatibility harness is configured.
No custom-OS compatibility pass is claimed; no Google-private suites are assumed.

## Endpoint contracts

The baseline collector remains stdlib-only. Endpoint schema validation uses the
pinned development dependencies; install them once in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python bin/diamaneos endpoints validate
```

Ordinary tests use `tests/endpoints/fixtures/services.json` and work in a standalone
clone of tools. They never search for a neighbouring infrastructure checkout.
For acceptance of a real service selection, also run this separate integration
gate with its actual explicit path (substitute your checkout location):

```sh
.venv/bin/python bin/diamaneos endpoints validate \
  --services /absolute/path/to/infrastructure/config/services.json
```

The first command reports inventory-only scope; the second validates the actual
two-repository design. The tests exercise the same schema validator as the CLI:
required fields, null/wrong types, reference/ownership/isolation errors, bounded
input, duplicate JSON keys, Unicode byte limits and non-echoing privacy failures.
Wire-shape examples check 204/body/time-unit conventions; they do not simulate
native cryptography or prove device compatibility. Valid schema data still has
owned implementation gates and cannot be called an active deployment. See
[ENDPOINTS.md](ENDPOINTS.md) for their interpretation and maintenance rules.
