# Build DiamaneOS for the Fairphone 6

Build a DiamaneOS test image from source and flash it. Test images are signed with public test keys:
keep the bootloader unlocked while one is installed.

## Requirements

- Linux on x86-64 (Debian 13 is tested), 16 CPU cores or more (8 work, slowly) and 64 GB of RAM (32
  GB minimum).
- About 500 GB free on an SSD with a case-sensitive filesystem (ext4, xfs or btrfs), and a fast
  connection: the first sync downloads 100 to 150 GB.
- These packages (`repo` is in Debian's `contrib` section; see [host setup](BUILD.md#host-setup)):

```sh
sudo apt install git git-lfs repo python3 python3-jsonschema gnupg openssh-client \
  rsync zip unzip diffutils fontconfig fonts-dejavu-core gperf libc6-dev-i386 \
  lib32gcc-s1 hostname ca-certificates util-linux kmod binutils openssl make perl
```

## 1. Get the source

```sh
mkdir -p ~/diamaneos-build/src && cd ~/diamaneos-build/src
repo init -u https://github.com/DiamaneOS/platform_manifest.git -b android17 --repo-rev=v2.65
repo sync -j8
```

- Downloads every project of the DiamaneOS manifest, including the build tools in `tools/diamaneos`,
  with the `repo` version they check.
- Stay in this directory.
- If a later sync stops because a project now comes from another repository at the same path, run
  `repo sync --force-sync PATH` for it; `diamaneos build` does that itself.

## 2. Add the vendor files

```sh
tools/diamaneos/bin/diamaneos build vendor
```

Checks the source (every project at the commit the manifest selects, nothing else in the tree; local
changes are named), then takes the vendor files from Fairphone's factory package, checked by its
SHA-256, into `vendor/fairphone/FP6`.

## 3. Build

```sh
source build/envsetup.sh
lunch FP6-cur-userdebug
m target-files-package otatools-package
```

The usual Android build, plus what step 4 packages and checks; a plain `m` builds the images only.
`FP6-cur-user` builds a `user` image without root.

## 4. Package and check

```sh
tools/diamaneos/bin/diamaneos build package
```

Checks that `out/` holds this source's build with these vendor files, then writes and checks the
image set step 5 flashes, marked as built by hand.

**Or in one command:** after step 1, `tools/diamaneos/bin/diamaneos build all --variant userdebug`
syncs and does steps 2 to 4 itself, compiling without network access and checking every step. After
a stop, run it again.

## 5. Flash

[Unlock the
bootloader](https://support.fairphone.com/hc/en-us/articles/10492476238865-How-to-unlock-or-lock-your-Fairphone-s-bootloader)
as Fairphone describes, with `fastboot flashing unlock_critical`. Then, in the bootloader:

```sh
{ fastboot getvar all; fastboot oem device-info; } > phone.txt 2>&1
tools/diamaneos/bin/diamaneos flash-steps --wipe --phone phone.txt --phone-firmware FP6.QREL.16.100.0
```

- This prints, and never runs, the fastboot commands for the newest checked image set.
- `--phone-firmware` is the build number in Settings > About phone; `--wipe` erases the phone.
- Never lock the bootloader or run `fastboot -w` or `fastboot erase`.
- [Flashing](BUILD.md#flashing) covers firmware, updates and going back to stock.

## More detail

[Build reference](BUILD.md) (host setup, each step, [building by hand](BUILD.md#building-by-hand),
[troubleshooting](BUILD.md#troubleshooting)), [FP6 kernel](FP6-KERNEL.md), [firmware](FIRMWARE.md),
[signing](SIGNING.md), [threat model](THREAT_MODEL.md).
