# FP6 firmware inventory

The Fairphone 6's closed firmware (boot chain, TrustZone, modem, signal
processors) and its recent changes; nothing here was read from a phone.

## Current state

DiamaneOS flashes only the OS partitions (boot, init_boot, vendor_boot, dtbo,
recovery, vbmeta, vbmeta_system, super, pvmfw); firmware keeps whatever the last
stock factory flash or OTA wrote, so it can lag the stock release the vendor
files come from.
[`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json)
lists every image of the selected `FP6.QREL.16.111.0` and previous
`FP6.QREL.16.100.0` factory packages with size and SHA-256, plus Qualcomm
version strings, signing metadata, AVB rollback data, the Wi-Fi firmware files
and the stock flash order.

## Partitions and sources

Qualcomm and Fairphone build and sign all firmware; DiamaneOS must ship the
exact stock bytes. A/B partitions have two slots that updates switch between.

| Group | Partitions (image) | Slots |
| --- | --- | --- |
| Boot chain | `xbl` (xbl_s.melf), `xbl_config`, `xbl_ramdump` (XblRamdump.elf), `abl` (Android boot loader), `uefi`, `uefisecapp`, `imagefv`, `shrm`, `cpucp`, `cpucp_dtb`, `aop`, `aop_config`, `qupfw`, `devcfg`, `multiimgoem` | A/B |
| Boot chain, single copy | `toolsfv` (tools.fv), `storsec` | single |
| Trusted execution | `tz`, `hyp` (Gunyah), `keymaster` (keymint.mbn), `featenabler` | A/B |
| Radio and DSP | `modem` (NON-HLOS.bin), `bluetooth` (BTFM.bin), `dsp` (dspso.bin) | A/B |
| Trusted VM | `vm-bootsys` | A/B |
| Trusted VM data, logs | `vm-persist`, `logfs` | single |
| Fairphone | `study` and `studybk_a/b` (study.img) | single + A/B |

`modem` is a FAT filesystem with the modem (MPSS), audio and compute DSPs (ADSP,
CDSP), Wi-Fi processor firmware (WPSS, `image/qca6750/`), IPA firmware, carrier
modem profiles (MCFG) and TrustZone applications; its stale
`verinfo/ver_info.txt` names older builds, so use the images' own version
strings.

Not firmware: the replaced OS images, the wipe images (`userdata`,
`metadata`, `frp`) and the GPT/EDL layout files (`gpt_*`, `rawprogram*`,
`patch*`), which fastboot never writes. `pvmfw` is built from source.
Peripheral firmware under `/vendor/firmware` (GPU, camera, video, touch,
amplifiers) ships in the vendor image via
`config/fp6-minimal/vendor-files.json`, covered by verified boot.

## The stock flash script

`flash_fp6_factory.command` is byte-identical in 16.100.0 and 16.111.0. It:

1. refuses to run unless `fastboot oem device-info` reports both
   `Device unlocked: true` and `Device critical unlocked: true`, and never locks
   or unlocks;
2. writes the firmware partitions, `_a` and `_b` of each A/B one, in
   alphabetical order from `abl` to `xbl_ramdump` (exact order in the
   inventory);
3. writes the OS partitions boot, dtbo, init_boot, recovery, super, vbmeta,
   vbmeta_system and vendor_boot (both slots, except the single super);
4. by default writes userdata, metadata and frp (a full wipe);
5. erases `misc`, `modemst1` and `modemst2` (the modem's runtime settings copy,
   restored from its backup);
6. selects slot a, then tries `fastboot oem reset-rollback` and accepts failure.

## Changes from 16.100.0 to 16.111.0

- All readable Qualcomm version strings are unchanged: boot
  `BOOT.MXF.2.1-02027`, TrustZone and hypervisor `TZ.XF.5.28.0-00021`, AOP
  `AOP.HO.5.0-00813`, modem `MPSS.DE.3.1.4.c4-00192`, ADSP
  `LPAIDSP.HT.1.0-01111`, CDSP `CDSP.HT.3.0-00953`, Wi-Fi
  `WLAN.MSL.3.0.1-00591`, Bluetooth `BTFW.MOSELLE.1.1.2-00064` and
  `1.2.0-00380`.
- 26 of 29 firmware images have new hashes. Only re-signed (new signature
  segment, same code): tz, hyp, keymint, aop, aop_devcfg, cpucp, cpucp_dtbs,
  qupv3fw, shrm, featenabler, storsec, uefi_sec, imagefv, xbl_config;
  xbl_s.melf differs only in certificate timestamps. New code or data under the
  same version: abl, uefi, devcfg, multi_image, XblRamdump, tools.fv.
- `modem`: modem, ADSP and CDSP code changed without a new version string; 14
  carrier profiles changed (1&1, Orange, Proximus, AT&T); Wi-Fi firmware, IPA
  and TrustZone apps were only re-signed. `bluetooth`: all 11 files identical,
  only filesystem metadata changed. `dsp`: one sensor library
  (`adsp/sns_tppe.so`) changed.
- Unchanged: `vm-bootsys`, `vm-persist`, `logfs`; the GPT layout (only unique
  GUIDs differ).
- The Qualcomm anti-rollback version in the OEM signing metadata is 1 in every
  signed image of both releases.
- The AVB rollback index of boot, init_boot and vbmeta_system rose from
  1785888000 to 1788566400 (the 2026-08-05 and 2026-09-05 patch dates as Unix
  time).

**Wi-Fi.** The firmware is `image/qca6750/wpss.mdt` and `wpss.b00`-`b12` in
`NON-HLOS.bin`, beside board data (`bdwlan.*`) and `regdb.bin`:
`WLAN.MSL.3.0.1-00591` in both releases with identical code segments (Android
15, 15.176.0 and 15.178.0, shipped `WLAN.MSL.3.0.1-00328.3`). Fairphone's
16.111.0 fix for apps not loading on Wi-Fi is in the WLAN host driver
(`qca_cld3_qca6750.ko` in vendor_dlkm): the dynamic IPv6 neighbour solicitation
offload handling of CodeLinaro qcacld-3.0 commit `ec35705b4e` ("Avoid caching
NS offload when dynamically disabled"). DiamaneOS's source-built driver is
pinned to a revision that has it, not yet tested on a phone.

## Rollback rules

- The bootloader stores AVB rollback indices at locations 1 (recovery), 2
  (vbmeta_system), 3 (boot) and 4 (init_boot) and enforces them while locked.
  Once a locked phone boots stock 16.111.0, locations 2-4 hold 1788566400 and
  16.100.0 images no longer boot locked. A DiamaneOS build relocked with its own
  key must use indices at or above the stored values. The stock script's
  `oem reset-rollback` (unlocked only) resets them; Fairphone warns that locking
  on older software than before may brick the phone.
- Qualcomm's own anti-rollback version (signing metadata, enforceable from
  fuses or protected storage) is 1 in both releases.
- Never downgrade firmware: it is untested even at equal anti-rollback version,
  and the stock flash script is the only supported way back to stock.

## Verification

- The factory package matches its pinned SHA-256 and Fairphone's published value
  (for 16.111.0 not yet published on 2026-09-30), and its own checksum list.
- On the phone, Qualcomm secure boot checks each image against the SoC's OEM
  key; AVB covers only the OS partitions and pvmfw, so byte-exact stock images
  keep firmware authentic.
