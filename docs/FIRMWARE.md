# FP6 firmware

The Fairphone 6's closed firmware (boot chain, TrustZone, modem, signal processors) and how
DiamaneOS delivers it.

## Overview

- DiamaneOS image sets carry Fairphone's firmware of the stock release their vendor files come from
  (`selected_build` in the inventory below), byte for byte, and `diamaneos flash-steps` writes it
  before the OS when the phone runs older firmware ([Delivery](#delivery)).
- Every update carries the A/B firmware of that release too ([Delivery](#delivery)).
- [`config/fp6-firmware-inventory.json`](../config/fp6-firmware-inventory.json) lists every image of
  the selected and the previous factory package (`selected_build`, `previous_build`) with size and
  SHA-256, plus Qualcomm version strings, signing metadata, AVB rollback data, the Wi-Fi firmware
  files and the stock flash order.
- It was read from the archives, not from a phone.

- Settings shows which release the booted slot's firmware is (About phone > Android version >
  Fairphone firmware).
- The phone cannot say itself: releases can share every Qualcomm version string (the inventory
  records them per image).
- So `fwrelease` (device `firmware/`) hashes the slot's A/B firmware partitions once per boot and
  compares them with a table the vendor step writes from the inventory
  (`/vendor/etc/diamaneos/firmware-releases.txt`; the images left out, each with its reason, are
  under `firmware_release` in `config/fp6-build.json`).

- "Mixed releases": the partitions come from different releases.
- "Unknown": a partition matches no release in the inventory, for example firmware of a release it
  does not list or a stock OTA that wrote other bytes.

## Partitions and sources

Qualcomm and Fairphone build and sign all firmware; DiamaneOS must ship the exact stock bytes. A/B
partitions have two slots that updates switch between.

| Group | Partitions (image) | Slots |
| --- | --- | --- |
| Boot chain | `xbl` (xbl_s.melf), `xbl_config`, `xbl_ramdump` (XblRamdump.elf), `abl` (Android boot loader), `uefi`, `uefisecapp`, `imagefv`, `shrm`, `cpucp`, `cpucp_dtb`, `aop`, `aop_config`, `qupfw`, `devcfg`, `multiimgoem` | A/B |
| Boot chain, single copy | `toolsfv` (tools.fv), `storsec` | single |
| Trusted execution | `tz`, `hyp` (Gunyah), `keymaster` (keymint.mbn), `featenabler` | A/B |
| Radio and DSP | `modem` (NON-HLOS.bin), `bluetooth` (BTFM.bin), `dsp` (dspso.bin) | A/B |
| Trusted VM | `vm-bootsys` | A/B |
| Trusted VM data, logs | `vm-persist`, `logfs` | single |
| Fairphone | `study` and `studybk_a/b` (study.img) | single + A/B |

`modem` is a FAT filesystem with the modem (MPSS), audio and compute DSPs (ADSP, CDSP), Wi-Fi
processor firmware (WPSS, `image/qca6750/`), IPA firmware, carrier modem profiles (MCFG) and
TrustZone applications; its stale `verinfo/ver_info.txt` names older builds, so use the images' own
version strings.

- Not firmware: the replaced OS images, the wipe images (`userdata`, `metadata`, `frp`) and the
  GPT/EDL layout files (`gpt_*`, `rawprogram*`, `patch*`), which fastboot never writes.
- `pvmfw` is built from source.
- Peripheral firmware under `/vendor/firmware` (GPU, camera, video, touch, amplifiers) ships in the
  vendor image via `config/fp6-minimal/vendor-files.json`, covered by verified boot.

## The stock flash script

`flash_fp6_factory.command` (`stock_flash_script` in the inventory records its SHA-256 and order):

1. refuses to run unless `fastboot oem device-info` reports both `Device unlocked: true` and `Device
   critical unlocked: true`, and never locks or unlocks;
2. writes the firmware partitions, `_a` and `_b` of each A/B one, in alphabetical order from `abl`
   to `xbl_ramdump` (exact order in the inventory);
3. writes the OS partitions boot, dtbo, init_boot, recovery, super, vbmeta, vbmeta_system and
   vendor_boot (both slots, except the single super);
4. by default writes userdata, metadata and frp (a full wipe);
5. erases `misc`, `modemst1` and `modemst2` (the modem's runtime settings copy, restored from its
   backup);
6. selects slot a, then tries `fastboot oem reset-rollback` and accepts failure.

## Delivery

- **Image set.**
  - `build package` copies every firmware image except the stock `pvmfw.img` out of the factory
    package the vendor step authenticated, under its stock name.
  - The package must be the inventory's package of the release (size and SHA-256), and each image
    must match its own pinned size and SHA-256.
  - The step refuses firmware of another release than the vendor files' and any image with a lower
    Qualcomm anti-rollback version than an older inventoried release.
  - `build.json` records the release, the package, each image's hash and the flash steps.
  - `build verify` checks all of it again against the inventory (check `firmware`).

**Flash steps.** The firmware steps come first, in the stock script's order. What DiamaneOS keeps of
that script (reasons in the `firmware` block of `config/fp6-build.json`):

| Stock step | DiamaneOS |
| --- | --- |
| A/B firmware, `_a` and `_b` | Same, both slots, stock order |
| `storsec`, `toolsfv`, `study`, `studybk_a/b` | Same |
| `logfs`, `vm-persist` (empty images) | Only with `--wipe`: they hold state, which an A/B update never touches either |
| Stock `pvmfw` on both slots | DiamaneOS's own pvmfw, slot a, with the OS |
| OS images on both slots | Slot a only |
| `userdata`, `metadata`, `frp` | With `--wipe`; FRP image identical to stock |
| `erase misc` | Zeros, with `--wipe` only: misc holds the OS's boot messages |
| `erase modemst1`, `modemst2` | Zeros over both whole partitions, right after the firmware, whenever firmware is written |
| `--set-active=a` | Same |
| `oem reset-rollback` | Dropped: it weakens rollback protection, and test builds stay unlocked |
| Unlock checks | From the saved phone state, plus product and bootloader mode |

With both modem file system copies empty, the modem rebuilds its file system from its factory backup
(`fsg`) when it next starts, so new modem firmware and its carrier profiles start from that backup,
as after Fairphone's own flash.

- **Slots.**
  - The OS goes to slot a only, but each A/B firmware partition gets the same image on both slots,
    as Fairphone's flash writes it.
  - Older firmware left on slot b would run after any later switch to b (`fastboot --set-active=b`,
    the bootloader's fallback after failed boots, or an update that lands on b):
    - Firmware older than the phone has already run, next to state the newer firmware wrote in
      partitions both slots share (`storsec`, the modem file system, secure storage).
  - Slot b holds no OS after a DiamaneOS flash (`super` gets slot a only), so its old firmware is no
    fallback either way.
  - As with Fairphone's flash, there is then no firmware fallback slot: a failed firmware write is
    fixed by running the steps again from the bootloader, or by Fairphone's factory package.

- **Never older firmware.**
  - The FP6 bootloader reports empty `version-bootloader` and `version-baseband`, and releases can
    share every Qualcomm version string, so the phone cannot say which release it runs.
  - `flash-steps` takes the release from `--phone-firmware` (the stock build number) or from the
    image set given with `--since`, compares build numbers, and stops if the phone runs a newer
    release than the image set carries.
  - For the same release it prints no firmware steps (`--rewrite-firmware` writes it again, for
    example to bring slot b and the single partitions up to it).
  - `--no-firmware` leaves the firmware alone.
  - If a phone reports version values, they must be the ones the inventory records for the stated
    release (`fastboot_versions`), or `flash-steps` refuses.
  - Firmware steps also need the saved output of `fastboot getvar all` and `fastboot oem
    device-info` (`--phone`): an FP6, unlocked, with unlocked critical partitions, in the bootloader
    rather than fastbootd.

- **Updates.**
  - Every A/B update carries the A/B firmware of the build's stock release, byte for byte, next to
    the OS. A locked phone cannot be flashed, so this is how it gets firmware fixes.
  - The vendor step copies those images from the factory package into the vendor tree (`radio/`).
    The generated makefiles add them to the update partition list and to the build's update inputs.
  - An update writes them to the slot it installs, with the OS. The running slot keeps its firmware
    until the next update.
  - An update never writes the single-copy partitions (`storsec`, `toolsfv`, `study`, `logfs`,
    `vm-persist`) or `studybk`, which holds run-time state (`update_skips` in
    `config/fp6-build.json`). Flashing writes them.
  - Unlike a flash, an update does not reset the modem file system.
  - `build verify` fails a target-files archive whose update partition list lacks an A/B firmware
    partition, names another firmware partition, or carries other bytes than stock's (check
    `firmware-ota`).

## Rollback rules

- The bootloader stores AVB rollback indices at locations 1 (recovery), 2 (vbmeta_system), 3 (boot)
  and 4 (init_boot) and enforces them while locked.
- Once a locked phone boots a stock release, locations 2 to 4 hold that release's index
  (`avb_rollback_index` in the inventory, the security patch date as Unix time), and images with a
  lower index no longer boot locked.
- A DiamaneOS build relocked with its own key must use indices at or above the stored values.
- The stock script's `oem reset-rollback` (unlocked only) resets them; Fairphone warns that locking
  on older software than before may brick the phone.
- Qualcomm's own anti-rollback version (signing metadata, enforceable from fuses or protected
  storage) is recorded in the inventory (`qualcomm_anti_rollback_version`).
- Never downgrade firmware: it is untested even at equal anti-rollback version, and the stock flash
  script is the only supported way back to stock.
- `flash-steps` refuses firmware steps towards an older stock release.

## Verification

- The factory package matches its pinned SHA-256, the value Fairphone publishes and its own
  checksum list (`verification` in `config/stock-inputs.json`).
- Each firmware image in an image set matches the inventory's SHA-256, at packaging and again in
  `build verify`; `SHA256SUMS` covers the set.
- On the phone, Qualcomm secure boot checks each image against the SoC's OEM key; AVB covers only
  the OS partitions and pvmfw, so byte-exact stock images keep firmware authentic.
