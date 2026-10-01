# DiamaneOS testing

How to run the DiamaneOS tool tests and the Fairphone 6 (FP6) test workflows,
and what they have shown so far on stock (Fairphone's own) software. For anyone
running, adding or changing a test.

| Term | Meaning |
| --- | --- |
| ADB | Android Debug Bridge: the USB command channel to a phone. |
| Role | A non-identifying name for one test phone, such as `harness`. The private device map ([BUILD.md](BUILD.md)) binds it to one ADB serial. |
| `<PRIVATE_ROOT>` | A private directory outside public Git for device maps, rig configuration and raw results. |
| Fail closed | Stop with an error when something is missing or unexpected. |
| AVB | Android Verified Boot: checks the OS partitions at boot and blocks rollback to older images. |

## Run the tool tests

Install the pinned development dependencies once, then run the full suite and
the endpoint check:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python bin/diamaneos endpoints validate
```

The suite includes the baseline, CLI and endpoint validation tests. The baseline
collector itself uses only the Python standard library; endpoint schema
validation needs the development dependencies. Device runs are separate hardware
evidence. Test counts are recorded in the acceptance evidence for the exact
tree.

## Signing checks

`bin/diamaneos signing roles`, `signing inventory` and `signing verify` check
the pinned signing contract, a target-files archive and a retained
disposable-key run; [SIGNING.md](SIGNING.md) explains them. Their unit fixtures
cover malformed archives, keeping development keys apart from signed output,
refusing presigned packages, source or role drift, incomplete proofs, path
escape and artifact tampering. They do not claim any Android artifact was
signed: the real APK, APEX, AVB, full-OTA and delta-OTA tool evidence comes
from the builder and offline qualification in SIGNING.md.

## Stock phone records

### Hardware report

The [stock hardware report](../reports-public/stock-capabilities.json) gives a
separate result per component for one FP6 on the stated stock build. It
combines operator-observed stock diagnostics and ordinary app use with selected
ADB checks of identity, charging and throwaway-file transfer.

- Its software and boot state describe the original Android 15 arrival
  inspection, during which the stock build stayed unchanged and the bootloader
  stayed locked.
- One later row, after the separately verified official Android 16 OTA
  (over-the-air update), formatted a throwaway 128 GB microSD card as portable
  storage and passed a 4 MiB create/read/hash/delete round trip. The row names
  its later build; it does not mean the arrival build was still installed.

The report does not cover performance or battery measurements or custom-OS
qualification (these need their own evidence), stock restore and unlock/relock
(validated later by FP6-025 and summarised in the installer recovery runbook),
or acceptance of the [baseline collector](#baseline-collector) CLI. The
stock-input inventory stays the immutable, hash-bound pre-restore selection
snapshot used by build environment v4.

### Stock recovery inputs

The [stock-input inventory](../config/stock-inputs.json) ties the observed FP6
product and build to Fairphone's official factory-package URLs (a factory
package is the full stock image set its flash script writes), exact byte sizes
and published SHA-256 values.

The final Android 15 package and the EU Android 16 package first offered by the
phone are verified recovery inputs. For each, two complete reads reproduced
Fairphone's outer hash, the full ZIP CRC passed, the required recovery files
were present and all 76 files in the embedded checksum list matched.

- The phone arrived with Android 15 build `FP6.QREL.15.176.0` (the arrival
  snapshot); the official OTA was installed later.
- The current accepted stock checkpoint is the locked, green-verified Android 16
  EU build `FP6.QREL.16.100.0` (2026-08-05 security patch). Its archive is the
  current EU restore selection; the Android 15 archive stays a historical
  verified input.
- On 2026-09-30 the input for generating DiamaneOS's vendor files moved to
  `FP6.QREL.16.111.0` (released 2026-09-28, 2026-09-05 security patch).
  Fairphone had not yet published its checksum, so the entry records two
  agreeing local reads, the MD5 from the official host's object metadata, the
  full ZIP CRC and all 76 embedded declared hashes instead; compare it with
  Fairphone's value once published. It is not a tested restore input: the phone
  still runs `FP6.QREL.16.100.0`, which stays the restore selection.

Verifying and selecting an archive does not prove a successful restore,
rollback eligibility, AVB/relock safety or bootloader operation. FP6-025
supplied that evidence separately for the exact Android 16 EU archive: restore,
AVB/rollback review, critical relock, normal relock, locked-green boot and final
cold-boot hardware checks passed. (The FP6 bootloader has a normal lock and a
separate critical lock for the bootloader partitions.)

#### Rules for using a recovery copy

- Before destructive work, read the copy from two independent private storage
  locations; both must match the recorded byte count and SHA-256. Two
  directories on one physical volume do not count.
- The public inventory records only content identity and policy; custody,
  provider and account evidence stay in the operator's private record. A path or
  filename never replaces content verification.
- Fairphone's factory script wipes user data by default and needs both normal
  and critical bootloader unlock. Its fallback that continues when no checksum
  tool is found is forbidden by the project recovery procedure. Before running
  it, verify the complete archive against the independently read official hash
  and verify its embedded declared files.
- The regional Android 16 package must match the build the phone itself offers;
  do not infer EU or US from a maintainer's location. This EU phone offered
  `FP6.QREL.16.100.0`, so the US `FP6.QREL.16.104.0` package is excluded as a
  restore or flash input for it. It stays a planned comparison input for the
  separately controlled US FP6: "excluded here" does not mean excluded from
  regional qualification.

#### What the package and the phone showed

- The validated factory `super.img` (the image of the super partition, which
  holds logical partitions such as system and vendor) has checksummed liblp 10.2
  metadata: all seven slot-A logical partitions are populated and every slot-B
  counterpart has zero bytes and zero extents. (A and B are the two partition
  copies that updates switch between.) The script deliberately selects A; do not
  select, boot or fabricate B as a repair.
- After relocking, bootloader rollback locations 0–4 matched the authenticated
  target values `0,1,1785888000,1785888000,1785888000`; locations 5–31 were
  zero.
- Raw partition bodies and per-partition device hashes were not collected from
  the phone. The layout taken from the package stays labelled a derived input,
  not a device dump.

#### The relock trap

Booting with the factory script's defaults showed a reproducible relock trap:
the first boot while unlocked set `get_unlock_ability` to `0` and left the
Android OEM control greyed out as already unlocked. No lock was attempted at
zero. The accepted path:

1. Use a same-directory copy of the script with only its declared
   `REBOOT_TO_BOOTLOADER` toggle enabled.
2. Repeat the verified wipe and flash.
3. Require ability `1` before the critical lock, and again before the normal
   lock.
4. Prove the final ability is `0`, both lock domains are closed and Verified
   Boot is green.

See the
[installer recovery runbook](../../installer/docs/recovery-preflight.md).

## Regional FP6 qualification

EU is the first supported hardware target. Report US as `UNVERIFIED`, not
supported or assumed compatible, until both steps below are accepted on the
second maintainer's planned US-region FP6 (its availability and state are not
yet evidenced). No EU result, similar version label or reference-ROM support
replaces that device run.

1. **Compare stock inputs.** Before claiming one image for both regions,
   compare the exact EU and US stock inputs: partition and super layout, AVB
   chain and rollback locations, boot and vendor images, firmware, VINTF (the
   vendor's declared hardware interfaces), init and SELinux policy, feature and
   permission files, SKU (hardware variant) properties, modem profiles and
   carrier/regulatory configuration. Only byte-identical files may enter the
   common generated set without further adaptation; record them separately
   from the regional delta. Bind a delta chosen at runtime to an observed,
   trustworthy hardware or boot SKU property, never to locale, language,
   timezone or location. A boot-critical delta needs separately bound variants.
2. **Test the US candidate** on the US phone: the applicable hardware matrix
   and the declared T-Mobile-oriented voice, SMS, data, 5G, VoLTE (voice over
   LTE) and VoWiFi (voice over Wi-Fi) tests.

## Baseline collector

`bin/diamaneos baseline capture` makes a bounded, read-only capture of a phone
over ADB. The [test-host deployment recipe](../deploy/test-host/README.md)
documents the test-host setup and isolation boundary. Fixture and dry-run
checks do not touch hardware:

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

- `--raw-dir` stores raw output in a per-run subdirectory (reuse refused), with
  each file's sha256 in the evidence references. Without it the run is
  ephemeral and not accepted evidence.
- On a controlled rig, `--device-role`, `--device-map` and `--rig-config` hold
  the role lock for the whole capture; give all three or none.
- Public reports carry a device alias only; serials stay private.
- Graphics capture is fixed to `dumpsys gfxinfo com.android.systemui`: an
  unscoped `gfxinfo` can list enough installed-package state to exceed the
  output limit, while SystemUI is a stable, non-personal host-readiness target.
- Host limits: 20 s per adb call and a 256 KiB streaming byte cap per command
  (byte-exact; invalid UTF-8 stays visible). Large traces and samples stay
  outside Git, recorded by hash.

Five fixtures plus fake-adb live-path tests prove the contract: valid output is
`ok`; truncation, timeout or overflow is `error` (never averaged as zero); a
missing service is `unsupported`, while bare `unknown` operator values stay
`ok`; IMEI, IMSI, ICCID, EID, phone, account, MAC and serial values are redacted
with context kept, checked across the full report and not just the case
fields; an ambiguous target is
refused before any adb command; output past the byte cap kills the command
(termination proven, not just detected); failed captures keep partial stdout
and stderr with hashes; a lost device stays `error`/partial, never
`unsupported`-complete.

At collector revision `73452925347dba523cf22b86df58789527b177dc`, one host
acceptance run on a stock Android 15 FP6 gave five `ok` cases, one explicit
`unsupported` service and zero errors; its raw bundle and device identity stay
private. This proves the bounded read-only capture path, not custom-OS
compatibility or comparative performance. Standalone ADB checks do not
establish collector acceptance.

## Controlled USB rig

`bin/diamaneos rig` gives identity-bound status, explicit port power and
battery maintenance for an independently qualified switchable USB hub. The
private configuration binds each role to an exact ADB map entry, logical port
and USB topology path.

- The controller refuses `cycle`, checks that USB2 and USB3 companion port
  states agree, verifies the selected role after power-on and checks that every
  other mapped role present stays on its original path.
- An active or unreadable `.partial` run in any configured private output root
  blocks maintenance and ordinary power changes.
- The start guard rejects persistent operation leases and restores a
  maintenance-held role to its verified powered path before partial test state
  is created.

Validation and planning contact no device:

```sh
bin/diamaneos rig validate --config <PRIVATE_ROOT>/rig.json
bin/diamaneos rig dry-run --config <PRIVATE_ROOT>/rig.json
```

The scheduled maintenance unit stays disabled until every deployed test starter
has a race-free handoff from role lock to partial state and the operator has
accepted each battery policy. Hub qualification, least-privilege device-node
access, service-owned ADB and reboot recovery are deployment requirements (see
the test-host deployment recipe), not unit-test results.

## Stock performance baseline

`config/baseline.json` is the stock FP6 performance and camera protocol the
tools use. Validate it without contacting a device or creating output:

```sh
bin/diamaneos baseline protocol validate
bin/diamaneos baseline pilot --dry-run
```

| Workflow | Commands | Declared run needs |
| --- | --- | --- |
| [Connected](#connected-pilot-and-run) | `baseline pilot`, `baseline connected` | two whole runs |
| [Boot timing](#boot-timing) | `baseline boot` | two whole repetitions |
| [Cold power-on](#cold-power-on) | — | three recorded repetitions |
| [Idle](#idle) | `baseline idle` | two eight-hour repetitions |
| [Camera](#camera) | `baseline camera` | 24 originals |

### Connected pilot and run

The connected pilot is shorter than the declared series and always labelled
`PILOT_ONLY_NOT_BASELINE_EVIDENCE`. It checks the exact private device-role
mapping before ADB, requires the operator to confirm an unlocked phone and a
visible 50% brightness setting, and verifies the remaining display, radio, SIM,
battery and build controls itself. The live power-service reading must also
report an awake display; an earlier operator confirmation does not count as
current state. A mismatch stops before the launch, frame or thermal workload.

The pilot then measures:

- **Launches**: one cold and one warm launch per bound stock app. Cold needs a
  force-stopped package and Android's `COLD` launch state; warm finishes the
  cold activity with BACK, checks its process stays resident and needs `WARM`.
  Both need a positive reported time.
- **Frames**: a 10-second package-scoped Settings sample with at least one
  rendered frame per completed swipe.
- **Load**: a bounded 30-second four-worker CPU load and 30-second cooldown,
  memory signals before and after, and an ending battery/charging snapshot.

It force-stops only the named packages, never clears app data and uses
Android's existing thermal policy. Current HAL (hardware abstraction layer)
battery/skin values enforce conservative stop thresholds; read-only sysfs
thermal-zone type/temp pairs are kept as extra raw observations.

On the locked stock user build the shell user cannot read
`/proc/pressure/memory`. The pilot keeps that permission failure as
`UNSUPPORTED` and uses bounded `dumpsys meminfo`, selected `/proc/vmstat`
deltas and run-bounded LMKD (low-memory killer daemon) and ActivityManager
event logs. Activity kill events are not called LMKD kills without
corroborating evidence. Unsupported and failed measurements never become a
numeric zero.

Live pilot (the operator fills in private values):

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
serial-redacted result, for the connected pilot only. The fixed-scene camera
pilot and physical-disconnect idle pilot stay explicit `NOT_RUN` phases until
their supervised operator steps are done. A connected-pilot pass does not
complete FP6-022 or authorize an Android-version comparison.

After the harness pilot succeeds, inspect the declared plan with
`bin/diamaneos baseline connected dry-run`. The declared `run` uses the
protocol's three cold and three warm launches per app, three independent
60-second Settings frame runs, and a 15-minute load plus 10-minute cooldown. It
needs a shared series ID and repeat index 1 or 2, so two whole runs cannot pass
as unrelated samples. A successful workload verifies all evidence hashes and the
unchanged protocol before atomically publishing its immutable result. The
declared connected baseline needs both repetitions.

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

### Boot timing

Boot timing is a separate workflow, because rebooting mid-sequence would change
app residency, thermal state and run order. Inspect it without a device using
`bin/diamaneos baseline boot dry-run`.

One `restart` repetition issues exactly three explicitly authorized ordinary
`adb reboot` operations. For each, the host monotonic clock records separately
ADB loss, authorized-ADB return, `sys.boot_completed=1` and boot animation
completion (from `service.bootanim.exit=1` or `init.svc.bootanim=stopped`,
recording which property gave the signal). The ready value is the later of
boot completion and boot-animation completion. This follows the AOSP
boot-completion boundary within the limits of host-side polling. The raw
observer record and reboot stdout/stderr are hash-bound; the private ADB serial
is left out of the structured result. A passed run verifies its evidence and
publishes its immutable result at once. Two whole repetitions share one series
ID.

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

### Cold power-on

The restart result is not a physical cold-boot time. Cold power-on has its own
declared method: three repetitions from a verified powered-off state, timed from
the visible press of the power button to the first visibly usable stock UI,
using continuous fixed-frame-rate source media or an equally reviewable
recording. USB enumeration, `adb reboot`, a stopwatch started after the press
and restart timing are not substitutes. Keep the source media private and keep
failed attempts rather than silently discarding them.

### Idle

The idle pilot is a staged five-minute harness check.

1. `baseline idle start` records the build, app versions, display, connected
   Wi-Fi/SIM and battery state, then performs an explicitly authorized
   `dumpsys batterystats --reset`. It sends `KEYCODE_SLEEP` and checks that
   Android is Asleep or Dozing while the authoritative built-in panel state is
   `OFF` (stock Android 15 on the FP6 reports `Dozing` with the panel off, so
   wakefulness alone does not prove screen-off). Sleep is asynchronous: the
   harness polls for at most five seconds and needs two consecutive
   non-awake/`OFF` observations rather than sampling right after the key event.
2. `baseline idle observe-disconnect` uses one of two distinct methods (below).
3. `baseline idle finish` captures the ending state.

**Physical unplug (default, `physical-unplug`).** The tool records stable ADB
loss; removal of USB power (VBUS) stays an operator attestation. Keep the cable
unplugged, the screen off and the phone untouched until `baseline idle status`
reports the interval complete. Then run
`baseline idle finish --wait-for-reconnect` and reconnect only after it prints
`READY_TO_RECONNECT`; it records two consecutive authorized-ADB observations and
starts the ending capture at once. The legacy physical finish path accepts an
already connected target, but its invocation time counts as the reconnect time,
so the 60-second finish tolerance still applies.

**Rig port off (qualified controlled hub).** Pass
`--disconnect-method verified-rig-port-off` and
`--rig-config <PRIVATE_ROOT>/rig.json` to `start`, and the same rig config to
`observe-disconnect` and `finish`. Leave the cable attached. The observer
verifies screen-off, selects the configured role and port, switches only that
port off, verifies USB2/USB3 power-off and ADB absence, and records the redacted
controller result. At the threshold, `finish` switches the same port on,
verifies the mapped role returns on the same path and the other mapped phone was
not disturbed, records stable ADB presence and captures the ending state at
once. No physical-disconnect attestation is needed, and hub power-off is never
presented as a cable unplug. If disconnect observation fails, it tries to
restore the port.

**Declared eight-hour run.** After the pilot passes, give both
`--declared-repeat-index` (`1` or `2`) and one shared, valid `--series-id` to
`dry-run` and `start`. The immutable report is then bound to
`DECLARED_STOCK_BASELINE_EVIDENCE`, the eight-hour protocol duration, the series
and its exact repeat. Giving only one fails closed; giving neither runs the
five-minute pilot. The declared baseline needs both eight-hour repetitions.
Wi-Fi and telephony output is reduced in memory to connection/registration
booleans; SSID, BSSID, subscriber and cell identifiers are not stored.

**Unattended series.** For the final controlled-hub series,
`baseline idle series launch` can run both repetitions on the tester without
keeping SSH or another computer attached. Launch fails closed unless scheduled
rig maintenance is disabled, the mapped role is currently authorized, the
operator separately authorizes the repeat-1 and repeat-2 batterystats resets,
and the operator confirms the unchanged display, unlock, no-interaction and
no-planned-outage conditions. The detached worker wakes the phone
(which has no screen lock), runs repeat 1, restores the exact port at the
eight-hour boundary, waits up to four hours for a verified 100%/full state, then
repeats for repeat 2. The two intervals are back to back in one owned workflow
but separated by a controlled recharge, so the series takes more than 16 hours.

- The worker waits for the explicit `ready_to_reconnect` flag from the same
  boot-relative duration check `finish` uses. The rounded `remaining_seconds`
  countdown is for display and polling only; `0.0` can still mean the finish
  gate is closed. Missing or invalid interval state fails the series rather
  than allowing an early finish. The declared duration and finish tolerance are
  unchanged.
- `baseline idle series status --series-id <shared-series-id>` is read-only and
  reports progress from tester-owned state. Each reset authorization is
  recorded and consumed on its own.
- A tester reboot, rejected preflight, late or non-comparable finish, recharge
  timeout, identity or path mismatch, or unexpected rig state fails the series,
  and the worker tries to restore the mapped port.
- Phone connectivity is recorded as observed at start and finish only; no
  continuous phone-side network observation is claimed.

Re-enable ordinary battery maintenance only after the series has ended and its
result paths have been reviewed.

### Camera

The fixture is a closed cardboard box with fixed green-timer and Johnson's
Buds-box subjects, one marked phone-stand position, a secured USB lamp and a
nominal phone-to-focus-target distance of 21.25 cm ±0.25 cm. The MacBook powers
the lamp and controls the phone over ADB from outside.

- Align the stand and timer from the fixture marks and reference photographs;
  millimetre-scale repositioning variation is accepted, not claimed as exact
  registration.
- Close the room blinds and door, switch off the room light, then close the box.
- Use the unambiguous rear-facing orientation for main and ultrawide captures;
  turn the phone 180 degrees in the same stand position for the front camera.
- Record the lamp by physical cyclic position (colour mode 1, 2 or 3) and
  discrete brightness level (1 to 10). The operator's labels neutral white,
  cool white and warm are not measured colour temperatures.
- Record the camera, mode, zoom and tap-focus action with every original; do
  not edit or transcode source media.

`bin/diamaneos baseline camera dry-run` shows the exact pilot order without
contacting a device or creating output. Add `--declared` to `dry-run` and
`start` only after the pilot succeeds: it repeats the nine standard matrix
captures twice and keeps the six advertised-mode survey captures once, 24
originals in total. The staged `start`, `capture` and `finalize` actions keep
one immutable private run open across manual lamp changes. Each `capture`
snapshots the camera media directory, triggers one tap-focus and shutter
action, requires exactly one new original, compares the device and pulled
SHA-256 values and records the pre-capture UI hierarchy.

The advertised still-mode survey keeps rear-main 1x originals for Portrait, Pro
and Super Night, the exposed 2x and Super Macro controls, and the front
multi-person field of view. The standard front capture explicitly selects the
single-person view, with Face Beauty disabled. Pano and motion modes are
inventoried but do not apply to this fixed-still fixture. If a physical
condition was wrong, keep the partial run with
`quarantine --status NON_COMPARABLE` and state the exact exclusion reason.

## Staged device runner

The hardware runner runs reviewed suites on an exact target with a private
target-role map, and writes a checkpointed, schema-versioned run. First
validate the committed smoke plan without contacting ADB or writing output:

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
  --conditions "<build, USB, network and power setup>" \
  --output <PRIVATE_ROOT>/test-runs
```

### Results

Every case names its stage, preconditions, oracle (the check that decides it),
installed build/firmware, duration, evidence kind, status and redacted/raw
references.

- `--stage` (repeatable) runs an intentional subset. It can exit successfully
  but is labelled `SELECTED`, with the rest of the expected inventory listed as
  `NOT_RUN`; it is not a complete-suite claim.
- A genuinely unavailable optional capability is `SKIP` with a declared reason;
  a missing required case never becomes a pass.
- The runner is fail-stop: timeout or output overflow is `HARNESS_ERROR`,
  device loss is `BLOCKED`, an oracle mismatch is `FAIL`, and later selected
  cases stay `NOT_RUN`. After an interruption, completed results and partial
  streams survive.

Exit codes do not replace per-case completeness:

| Code | Meaning |
| --- | --- |
| `0` | complete, or explicit selected success |
| `2` | invalid arguments or data |
| `3` | blocked by a prerequisite, the target or a lock |
| `4` | test rejection |
| `5` | execution failure, timeout or interruption |

### Rerun unresolved cases

Repeat only unresolved cases under a new immutable run ID:

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

Retry input and raw hashes, the exact suite, role and installed build identity
must still agree.

### Destructive suites

Destructive suites are a separate stage that also needs `--destructive` and a
private map entry with `disposable: true`. The v1 runner has no flash or wipe
code: after those gates, an `installer-runbook` case stays explicitly `BLOCKED`
for the separate reviewed operator action. Never mark it `PASS` just because
the gate was accepted.

### Runner tests

Runner contract tests are synthetic evidence, not hardware results:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover \
  -s tests/runner -t .
```

They cover multiple-device binding, wrong target, unavailable capability,
timeout, device loss, interruption/checkpoint, verified rerun selection,
immutable collisions and the destructive boundary.

### Runner acceptance (FP6-034)

FP6-034 accepted the hardware harness at signed implementation commit
`089432fd6d82e10cce384747d4b0438120e60086` on the accepted test host. The
read-only `smoke` suite ran on the locked stock Android 15 FP6 build
`FP6.QREL.15.176.0` (`VS21`, user build) as
`fp6-034-stock15-20260911T234004Z`. The schema-valid, complete report selected
and completed all six expected cases: five `PASS`, one reasoned `SKIP` for the
optional IMS dumpsys service, and no failure, harness error or unresolved
check. The sanitized report SHA-256 is
`c300c05ca6fcf787a2590aba19844a7d00cf53e397d35ff7a7b40733a56bb4e9`; all 19
referenced private raw artifacts reproduced their hashes. This accepts the
runner and this stock read-only evidence only, not a custom-OS, compatibility,
recovery, destructive-operation, release or signer qualification. Raw
diagnostics and device identifiers stay outside public Git.

### Carrier and telephony evidence

`config/carrier-matrix.json` is the public, identifier-free plan for the two
FP6 carrier profiles and the isolated peer. Validate it without contacting a
device or network:

```sh
bin/diamaneos carrier matrix validate
bin/diamaneos test run --suite telephony --dry-run
```

The matrix keeps `plan_eligibility` apart from `observation_status`: a carrier
page can show a tariff is eligible, not provisioning, registration or
behaviour on an FP6. A `PASS` or `FAIL` row needs build- and
arrangement-bound evidence; an unavailable SIM or unknown exact tariff stays
`BLOCKED` or `NOT_RUN`. Each FP6 profile keeps its firmware, APN (mobile data
access point) and IMS (the carrier's IP voice and messaging service) context
explicit; unobserved context is `UNRECORDED`, never inferred from a carrier
page.

#### The telephony suite

The `telephony` suite makes only four allowlisted read-only captures:
`dumpsys carrier_config`, `dumpsys telephony.registry`, the private raw
`dumpsys phone` IMS/MMTEL context and the optional legacy `dumpsys imsservice`
interface. Complete raw streams go only under the private output root;
`result.json` keeps only bounded, redacted fields. The phone-service dump has
no public-safe field allowlist, so none of it enters the structured report. A
reviewed case may raise the default 256 KiB stream limit up to the runner's
hard 1 MiB ceiling; the stock FP6 telephony-registry snapshot does, because its
measured output exceeded the default. Other cases keep the default, and an
overflow is still a fail-stop harness error. A successful suite means those
observations were captured, not that voice, SMS, mobile data, VoLTE, WiFi
Calling, 5G or emergency behaviour works.

Run it for one explicitly confirmed carrier/SIM arrangement at a time, with the
smoke suite's private role map and target-binding rules:

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

#### Manual call and SMS checks

Ordinary call and SMS checks stay human-led, using only private allowlisted
test destinations and synthetic message text. Record inbound and outbound
voice/SMS, mobile-data transitions, VoLTE data continuity, provisioned WiFi
Calling, locally observed 5G, audio routes, reboot/reconnect behaviour and the
selected voice/data/SMS defaults. Restore the starting connectivity state after
each case. **Never dial a live emergency number.** eSIM deletion or
reprovisioning needs separate explicit operator authorization; the read-only
suite never changes a subscription.

#### Results on the locked stock FP6

Each result below is identifier-free.

**Vodafone eSIM mobile data and 5G display** (EU stock, operator-authorized,
recorded separately from the read-only context capture). With Wi-Fi as the
default route and mobile data already on, the bounded check turned Wi-Fi off,
verified a cellular default route, received HTTP 200 with a 559-byte HTTPS
body, saw the stock telephony display state as LTE with an NR-NSA override (5G
alongside LTE), then restored and verified the original Wi-Fi route. SHA-256
`1e3572527e15ec1ea6f02b8c3ac86a80ed59f9854ef8ae74409e4f6b9fc41200`. This
establishes the two matching rows only for the observed stock build, carrier
profile, place and time. It does not show that every transferred byte used NR
(5G), coverage elsewhere, or voice, SMS, VoLTE, WiFi Calling, dual-SIM defaults
or eSIM lifecycle behaviour.

**Vodafone eSIM disable and re-enable** (explicitly operator-authorized, in
Android Settings). The observer recorded registered service before, no
registered voice or data service while disabled, and registered service again
after re-enabling; Android did not ask for activation credentials. SHA-256
`3be986ec32c64b6d70a20f6cb634a752de5c2fce69fc661bdf9b947ea7edbab0`. This closes
only the retained-profile disable/re-enable lifecycle row on the observed stock
build. The profile was never deleted, downloaded, transferred or reprovisioned,
and the result implies none of those.

**Dual SIM.** With the Vodafone eSIM and an active Blau 9 Cent physical SIM
enabled together, the read-only suite passed its three required captures and
explicitly skipped the unavailable legacy `imsservice` interface. All 15 raw
and identity evidence references verified; SHA-256
`f982b30ea14ab141bc2dc787b01ea05114db742a2bf5f303b45e00e46ba32908`. The
operator then saw Vodafone as Android's default for voice, SMS and mobile data,
without changing any selection. This closes only the dual-SIM context and
default-subscription observation.

**Blau mobile data** (operator-authorized). The runner temporarily selected
Blau for mobile data, turned Wi-Fi off, verified a cellular default route and
received HTTP 200 with a 559-byte HTTPS body, then restored and verified Wi-Fi
and the original Vodafone mobile-data default. All 15 referenced evidence files
verified; SHA-256
`c9e4c12e61d5ea5f23521175d9e06a2bbc5512e352e57978b4022bf29e7f12d4`. This closes
only the Blau data row, not Blau voice, SMS, VoLTE, WiFi Calling or 5G.

**Final campaign.** The permanent peer was a Pixel 2 with an active Vodafone
physical SIM.

- Both FP6 profiles passed ordinary inbound and outbound voice calls and
  synthetic, non-personal SMS both ways.
- Connected-call captures recorded active call state, LTE voice service and IMS
  registration; the operator confirmed earpiece and speaker audio plus
  concurrent mobile-data use during each profile's outbound LTE call.
- Both profiles passed an ordinary WiFi Calling call with airplane mode on and
  the approved Wi-Fi connection active: the stock indicator, two-way audio, IMS
  registration and IWLAN/WLAN transport evidence agreed.
- After an FP6 reboot, both subscriptions, Wi-Fi and the Vodafone
  voice/SMS/data defaults recovered.
- Emergency calling was not exercised.

The final behaviour summary has SHA-256
`27c27091a26675d0e5cfec879e2c3fa42dd15a8f868bbf73f58a60cf2ad35f5e`; the private
evidence archive has SHA-256
`e0ab9551aab1648ce5e7477fa99b480d9b9e5f498e99f94974753a36ff052726`. All nine
constituent result hashes and all 139 unique referenced evidence files were
rechecked before export. These results close the Vodafone and Blau voice, SMS,
VoLTE and WiFi Calling rows for the observed stock build and arrangement.

The active no-package Blau 9 Cent tariff stays `BLOCKED` for 5G, because the
reviewed official material does not consistently establish its 5G eligibility.
That is neither an FP6 nor an OS failure. Retest that row only after
activating an option with unambiguous 5G eligibility.

## Official compatibility harness

[COMPATIBILITY.md](COMPATIBILITY.md) (data in `config/test-suites.json`)
documents the official Android suites (CTS, CTS Verifier, VTS): the selected
Android 17/API 37 suite revisions, official source URLs, minimum host
requirements, fixture ledger, target interlocks and fail-closed Tradefed result
parser.

- A separately versioned stock Android 16 trial proves only the harness and
  collection path; it is not a DiamaneOS compatibility result. No custom-OS
  compatibility pass or inaccessible partner-suite completion is claimed.
- Harness trials are scoped separately from release qualification. Keep the
  automated trial/parent identity and manual XML exports; a selected test PASS
  does not imply full-suite coverage. See
  [manual report collection](COMPATIBILITY.md#manual-report-collection) for
  export and cleanup.
- The original early-risk ledger stays an input to final case selection.
  Complete CDD (Android Compatibility Definition Document) coverage and all
  applicable CTS, CTS Verifier, VTS and modular suite results stay release-gate
  work on the actual FP6 `user` candidate.

## Resource overlay check

`bin/diamaneos overlays check` compares every DiamaneOS resource overlay with
its target's resources at a GrapheneOS release; [OVERLAYS.md](OVERLAYS.md)
explains when and how to run it. Its tests use small fixture trees and local
Git remotes, so they need no network:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/overlays -t .
```

## Font customization check

`bin/diamaneos fonts check` reads a product `fonts_customization.xml` and its
fonts the way Android does while loading the system fonts
(FontCustomizationParser, FontListParser and SystemFonts at the pinned release).
A mistake in that file is not local to the added fonts: it can make Android drop
every system font or stop boot. `--help` lists the errors and warnings. The
tests build small synthetic fonts, so they need no network:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/fonts -t .
```

Run it on the build module whenever the file or a font changes, with the
weights the product's text styles ask for:

```sh
bin/diamaneos fonts check --module-dir <FONTS_MODULE_DIR> --weights <WEIGHTS> --strict
```

and on a built image before it is flashed:

```sh
bin/diamaneos fonts check --xml <PRODUCT_OUT>/product/etc/fonts_customization.xml \
    --font-dir <PRODUCT_OUT>/product/fonts --weights <WEIGHTS> --strict
```

The check reads the fonts' tables but does not rasterise (draw) them. It does
not see the system font list, so it cannot tell whether a named family replaces
a system one or an alias points at a system family. It is stricter than
Android's XML parser, which accepts a DTD and repeated attributes. A passing
check is not a boot: `cmd font dump` and logcat stay part of the phone test.

## Endpoint contracts

[ENDPOINTS.md](ENDPOINTS.md) explains the endpoint contracts (the network
services DiamaneOS inherits). Set up and run `endpoints validate` as in
[Run the tool tests](#run-the-tool-tests); without `--services` it reports
inventory-only scope. Ordinary tests use
`tests/endpoints/fixtures/services.json`, work in a standalone clone of tools
and never search for a neighbouring infrastructure checkout. To accept a real
service selection, also run this separate integration gate with its actual path
(substitute your checkout location); it validates the actual two-repository
design:

```sh
.venv/bin/python bin/diamaneos endpoints validate \
  --services /absolute/path/to/infrastructure/config/services.json
```

The tests exercise the same schema validator as the CLI: required fields, null
or wrong types, reference/ownership/isolation errors, bounded input, duplicate
JSON keys, Unicode byte limits and non-echoing privacy failures. Wire-shape
examples check 204/body/time-unit conventions; they do not simulate native
cryptography or prove device compatibility. Valid schema data still has owned
implementation gates and cannot be called an active deployment.

## Vendor file generation tests

`tests/vendor/test_vendor_files.py` covers selected regular-file generation
with the real component validator and filesystem publication: repeat
generation, retained image metadata, altered input/output, missing notices,
wrong stock identity, absent dependency, unknown owner, traversal, special
files, concurrent publication, interrupted copying and private/public policy
separation. It establishes the generator boundary with synthetic bytes, not an
FP6 product closure or hardware compatibility result.
