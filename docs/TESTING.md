# DiamaneOS testing

How to run the tool tests and checks, and what is recorded about the stock
Fairphone 6 (FP6). Terms: see the [threat model](THREAT_MODEL.md#terms).

## Run the tool tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python bin/diamaneos endpoints validate
```

The suite covers the build, signing, CLI, overlay, font and endpoint checks
with fixtures and needs no phone; schema validation needs the development
dependencies. Test counts are recorded in the acceptance evidence for the exact
tree.

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
battery or custom-OS qualification, or restore and unlock/relock (later
validated by FP6-025, see the installer recovery runbook).

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
| `FP6.QREL.16.104.0` (US) | Excluded as this EU phone's restore or flash input (it offered 16.100.0). |

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

US is `UNVERIFIED` until a US stock comparison and a run on a US-region FP6 are
accepted; no EU result, version-label similarity or reference-ROM support
replaces them. Before claiming one image for both regions, compare the exact EU
and US stock partition/super layout, AVB chain and rollback locations, boot and
vendor images, firmware, VINTF, init/SELinux policy, feature/permission files,
SKU properties, modem profiles and carrier/regulatory configuration. Only
byte-identical files enter the common set unadapted, recorded apart from the
regional delta. Select a runtime delta by an observed trustworthy hardware/boot
SKU property, never locale, language, timezone or location; a boot-critical
delta needs separately bound variants.

## Other checks

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
