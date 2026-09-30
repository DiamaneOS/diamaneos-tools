# FP6 firmware: inventory and update path

This note describes the firmware of the Fairphone 6, where it comes from, what
changed between the last two stock releases, and how DiamaneOS should deliver
it. It is a design, not an implemented feature. Nothing in it was read from a
phone.

## Current state

DiamaneOS builds and flashes the OS partitions only (boot, init_boot,
vendor_boot, dtbo, recovery, vbmeta, vbmeta_system, super, pvmfw). The
firmware partitions keep whatever the last stock factory flash or stock OTA
wrote. So a phone's firmware can lag, or differ from, the stock release that
the DiamaneOS vendor files come from.

The selected stock release is `FP6.QREL.16.111.0`; the previous one is
`FP6.QREL.16.100.0`. Both are inventoried in
[`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json):
every image in each factory package with its size and SHA-256, plus readable
Qualcomm version strings, signing metadata, AVB rollback data, the Wi-Fi
firmware files and the stock flash order.

## Firmware partitions and their sources

All firmware is closed. Qualcomm and Fairphone build and sign it; DiamaneOS
cannot rebuild or re-sign it and must ship the exact stock bytes.

| Group | Partitions (image) | Slots |
| --- | --- | --- |
| Boot chain | `xbl` (xbl_s.melf), `xbl_config`, `xbl_ramdump` (XblRamdump.elf), `abl` (Android boot loader), `uefi`, `uefisecapp`, `imagefv`, `shrm`, `cpucp`, `cpucp_dtb`, `aop`, `aop_config`, `qupfw`, `devcfg`, `multiimgoem` | A/B |
| Boot chain, single copy | `toolsfv` (tools.fv), `storsec` | single |
| Trusted execution | `tz`, `hyp` (Gunyah), `keymaster` (keymint.mbn), `featenabler` | A/B |
| Radio and DSP | `modem` (NON-HLOS.bin), `bluetooth` (BTFM.bin), `dsp` (dspso.bin) | A/B |
| Trusted VM | `vm-bootsys` | A/B |
| Trusted VM data, logs | `vm-persist`, `logfs` | single |
| Fairphone | `study` and `studybk_a/b` (study.img) | single + A/B |

`modem` holds more than the modem: a FAT filesystem with the modem (MPSS),
audio DSP (ADSP), compute DSP (CDSP), the Wi-Fi processor firmware (WPSS,
`image/qca6750/`), IPA firmware, carrier modem profiles (MCFG) and several
TrustZone applications. Its `verinfo/ver_info.txt` is stale (it names older
modem and Wi-Fi builds than the images carry); use the version strings inside
the images.

Not firmware, although in the package: the OS images DiamaneOS replaces, the
wipe images (`userdata`, `metadata`, `frp`), and the GPT and EDL layout files
(`gpt_*`, `rawprogram*`, `patch*`), which the fastboot script never writes.
`pvmfw` is built from source by DiamaneOS. Peripheral firmware under
`/vendor/firmware` (GPU, camera, video, touch, amplifiers) ships in the vendor
image through `config/fp6-minimal/vendor-files.json` and is covered by verified
boot.

## The stock flash script

`flash_fp6_factory.command` is byte-identical in 16.100.0 and 16.111.0. It:

1. refuses to run unless `fastboot oem device-info` reports both
   `Device unlocked: true` and `Device critical unlocked: true`; it never locks
   or unlocks;
2. writes the firmware partitions, both `_a` and `_b` of each A/B partition,
   in alphabetical order of partition name, from `abl` to `xbl_ramdump`
   (the exact order is in the inventory);
3. writes the OS partitions boot, dtbo, init_boot, recovery, super, vbmeta,
   vbmeta_system and vendor_boot (both slots, except the single super);
4. with its default settings writes userdata, metadata and frp (a full wipe);
5. erases `misc`, `modemst1` and `modemst2` (the modem's runtime settings copy;
   the modem restores it from its backup),
6. selects slot a, then tries `fastboot oem reset-rollback` and accepts failure.

## What changed from 16.100.0 to 16.111.0

- Every readable Qualcomm version string is unchanged: boot
  `BOOT.MXF.2.1-02027`, TrustZone and hypervisor `TZ.XF.5.28.0-00021`, AOP
  `AOP.HO.5.0-00813`, modem `MPSS.DE.3.1.4.c4-00192`, ADSP
  `LPAIDSP.HT.1.0-01111`, CDSP `CDSP.HT.3.0-00953`, Wi-Fi
  `WLAN.MSL.3.0.1-00591`, Bluetooth `BTFW.MOSELLE.1.1.2-00064` and
  `1.2.0-00380`.
- 26 of 29 firmware images have new hashes. Most were only re-signed (new
  signature segment, identical code): tz, hyp, keymint, aop, aop_devcfg,
  cpucp, cpucp_dtbs, qupv3fw, shrm, featenabler, storsec, uefi_sec, imagefv,
  xbl_config. xbl_s.melf differs only in signing certificate timestamps.
  Rebuilt with new code or data but the same version string: abl, uefi,
  devcfg, multi_image, XblRamdump and tools.fv.
- `modem`: modem, ADSP and CDSP code changed without a new version string;
  14 carrier profiles changed (1&1, Orange, Proximus, AT&T). The Wi-Fi
  firmware, IPA and TrustZone apps were only re-signed.
- `bluetooth`: all 11 files are identical; only filesystem metadata changed.
  `dsp`: one sensor library (`adsp/sns_tppe.so`) changed.
- Unchanged: `vm-bootsys`, `vm-persist`, `logfs`. The GPT layout is identical
  (only unique GUIDs differ).
- The Qualcomm anti-rollback version in the OEM signing metadata is 1 in every
  signed image of both releases.
- The AVB rollback index of boot, init_boot and vbmeta_system rose from
  1785888000 to 1788566400 (the security patch dates 2026-08-05 and
  2026-09-05 as Unix time).

### Wi-Fi

The Wi-Fi processor firmware is `image/qca6750/wpss.mdt` and `wpss.b00`-`b12`
inside `NON-HLOS.bin` (the `modem` partition), with the board data
(`bdwlan.*`) and regulatory database (`regdb.bin`) beside it. Its version is
`WLAN.MSL.3.0.1-00591` in both releases, and all its code segments are
identical; only the signature segment changed. Android 15 (15.176.0 and
15.178.0) shipped `WLAN.MSL.3.0.1-00328.3`.

Fairphone's 16.111.0 fix for apps not loading on Wi-Fi is not in the firmware.
It is in the WLAN host driver (`qca_cld3_qca6750.ko` in vendor_dlkm), which
gained the dynamic IPv6 neighbour solicitation (NS) offload handling of
CodeLinaro qcacld-3.0 commit `ec35705b4e` ("Avoid caching NS offload when
dynamically disabled"). DiamaneOS builds this driver from source, and its
pinned qcacld-3.0 revision already contains that commit, so DiamaneOS gets the
fix from its own kernel build. That still needs a phone test.

## How DiamaneOS should deliver firmware

### One release for vendor files and firmware

Take the firmware from the same stock release as the vendor files: the
inventory's `selected_build` must equal the `stock_build` of
`vendor-files.json` and `fp6-stock-image-recipe.json`. The vendor blobs, the
DSP and modem images and the kernel-module interfaces they use are released
and tested together by Fairphone. Mixing releases is allowed only as a
recorded single-component pin (below).

### Installer and flash script

Stage the firmware images from the verified factory package by hash, as
`vendor stage` does for the OS images, and reject any image whose SHA-256 is
not in the inventory. The installer then:

1. writes the firmware to the slot it is installing, in the stock script's
   order, before the OS images;
2. writes the single-copy partitions (`toolsfv`, `storsec`, `study`) only when
   their pinned image differs from the last release DiamaneOS shipped, and
   `vm-persist` and `logfs` only on a full wipe, as the stock script does;
3. erases `modemst1`/`modemst2` only on a full wipe;
4. selects that slot.

The inactive slot has no OS after an install (a super image written by
fastboot fills slot a only), so it gets its firmware with the next A/B update. Writing both
slots, as stock does, is harmless on a full reinstall but not needed.

### OTA updates

Put the A/B firmware images into the OTA so every update carries the firmware
of its stock release: add them to the target files as prebuilt radio images
and to `AB_OTA_PARTITIONS` (abl, aop, aop_config, bluetooth, cpucp,
cpucp_dtb, devcfg, dsp, featenabler, hyp, imagefv, keymaster, modem,
multiimgoem, qupfw, shrm, tz, uefi, uefisecapp, vm-bootsys, xbl, xbl_config,
xbl_ramdump). update_engine writes them to the inactive slot with the rest of
the update, so a failed boot falls back to the old slot with its old firmware.
Single-copy partitions cannot be updated atomically and stay out of the OTA.
Compare this list with the partitions in one of Fairphone's own OTA payloads
before relying on it.

### Pinning a single component

If a stock release regresses one component, keep the rest of the release and
pin that component to the last good release. LineageOS did this for the FP6
Wi-Fi firmware in July 2026 (commit `c167aaa` in ArianK16a's FP6 device tree,
discussed in the Fairphone forum): it copied the Android 15 (15.176.0)
`wpss.*`, `bdwlan.elf` and `regdb.bin` into `/vendor/firmware/qca6750/` and
put `/vendor/firmware` ahead of the modem partition in the kernel's firmware
search path, so the older, still validly signed files load instead of the ones
in `modem`. The partition itself was not changed or re-signed.

DiamaneOS already loads remote-processor firmware through ueventd, whose
default search list puts `/vendor/firmware/` ahead of the
`/vendor/firmware_mnt/image/` directory the device tree adds. A pin would
therefore be exact stock files from the named release in `vendor-files.json`,
each with its hash and a recorded reason, probably with no search-path change;
that needs a build and phone test. This works only for firmware loaded by name
from a filesystem (WPSS, ADSP, CDSP, modem, GPU). Boot-chain images (xbl,
abl, tz, hyp, aop, ...) are loaded from their raw partitions, so pinning one
means shipping that whole partition image from the older release. A pin must
never have a lower anti-rollback version than what the phone has run, and is
dropped once stock fixes the regression.

## Rollback rules

- AVB rollback indices are stored by the bootloader at locations 1
  (recovery), 2 (vbmeta_system), 3 (boot) and 4 (init_boot) and enforced while
  locked. Once a locked phone boots stock 16.111.0, locations 2-4 hold
  1788566400 and 16.100.0 images no longer boot locked. A DiamaneOS build
  relocked with its own key must use rollback indices at or above the stored
  values. The stock script's `oem reset-rollback` (unlocked only) resets them;
  Fairphone warns that locking on older software than before may brick the
  phone.
- Qualcomm firmware carries its own anti-rollback version in the signing
  metadata, which the boot chain can enforce from fuses or protected storage.
  It is 1 in both releases. Never ship or flash a firmware image with a lower
  version than the highest DiamaneOS has shipped; the tools should check this
  from the inventory.
- Do not downgrade firmware in general. A downgrade is untested even at an
  equal anti-rollback version, and the stock flash script is the only
  supported way back to stock.

## Verification

- The factory package matches its pinned SHA-256 (and Fairphone's published
  value; for 16.111.0 that was not published yet on 2026-09-30), and the
  package's own checksum list matches.
- Each staged image matches the inventory hash; the flash script matches its
  recorded hash, so a changed order is noticed.
- Each signed image parses as a Qualcomm MBN v7 image, is OEM-signed, and its
  anti-rollback version is not lower than the last shipped one.
- On the phone, the Qualcomm secure boot chain checks each firmware image's
  signature against the OEM key the SoC trusts before running it. AVB covers
  only the OS partitions and pvmfw, none of the firmware partitions, so
  byte-exact stock images are what keeps them authentic; an OTA adds the
  DiamaneOS payload signature for transport.

## Needs a build or phone test

- 16.111.0 vendor files on a phone whose firmware is still 16.100.0 (the state
  right after moving the stock input, until firmware is updated).
- Writing each firmware partition with fastboot from the installer, including
  which partitions need critical unlock, and then relocking with the DiamaneOS
  key.
- An OTA carrying firmware: the payload lists the partitions, the update
  installs to the inactive slot, the phone boots it, and a forced failure falls
  back cleanly.
- The Wi-Fi fix in the source-built WLAN driver (IPv6 on a network where the
  stall was seen, before and after suspend).
- A single-component pin through `/vendor/firmware`, if one is ever needed.
- The anti-rollback check in the tools, against a real MBN image.
