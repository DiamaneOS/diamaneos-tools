# Build DiamaneOS for the Fairphone 6

These steps build a DiamaneOS test image from source and flash it. Test images
are signed with public test keys: keep the phone's bootloader unlocked while
one is installed.

## Requirements

- Linux on x86-64 (Debian 13 is tested), 16 CPU cores or more (8 work, slowly)
  and 64 GB of RAM (32 GB minimum).
- About 500 GB free on an SSD with a case-sensitive filesystem (ext4, xfs or
  btrfs), and a fast connection: the first sync downloads 100 to 150 GB.
- These packages (`repo` is in Debian's `contrib` section; see
  [host setup](BUILD.md#host-setup)):

```sh
sudo apt install git git-lfs repo python3 python3-jsonschema gnupg openssh-client \
  rsync zip unzip diffutils fontconfig fonts-dejavu-core gperf libc6-dev-i386 \
  lib32gcc-s1 hostname ca-certificates util-linux kmod binutils openssl make perl
```

## 1. Get the source

```sh
mkdir -p ~/diamaneos-build/src && cd ~/diamaneos-build/src
repo init -u https://github.com/DiamaneOS/platform_manifest.git -b android17
repo sync -j8
```

This downloads every project the DiamaneOS manifest selects, including the
build tools in `tools/diamaneos`. Run the next commands from this directory.

## 2. Check the source

```sh
tools/diamaneos/bin/diamaneos build sync
```

Syncs again with the pinned `repo` tool and checks the tree: every project at
the commit the manifest selects, nothing else in it. Local changes are named.

## 3. Add the vendor files

```sh
tools/diamaneos/bin/diamaneos build vendor
```

Builds the image tools, downloads Fairphone's factory package and checks its
SHA-256, then extracts the files DiamaneOS takes from it into
`vendor/fairphone/FP6`.

## 4. Build

```sh
source build/envsetup.sh
lunch FP6-cur-userdebug
m
```

The usual Android build. `userdebug` has adb and root debugging for testing;
`FP6-cur-user` builds a `user` image.

## 5. Package and check

```sh
tools/diamaneos/bin/diamaneos build all --variant userdebug
```

Packaging and the image checks need `build all`, with the variant of step 4.
It runs steps 2 to 4 itself, skips what is current, then writes and checks the
image set. Right after step 1 it is the one command for the whole build; after
a stop, run it again.

## 6. Flash

[Unlock the bootloader](https://support.fairphone.com/hc/en-us/articles/10492476238865-How-to-unlock-or-lock-your-Fairphone-s-bootloader)
as Fairphone describes, with `fastboot flashing unlock_critical`. Then, in the
bootloader:

```sh
{ fastboot getvar all; fastboot oem device-info; } > phone.txt 2>&1
tools/diamaneos/bin/diamaneos flash-steps --wipe --phone phone.txt --phone-firmware FP6.QREL.16.100.0
```

This prints the fastboot commands for the newest checked image set and runs
none. `--phone-firmware` is the build number in Settings > About phone; `--wipe`
erases the phone. Never lock the bootloader, and never run `fastboot -w` or
`fastboot erase`. [Flashing](BUILD.md#flashing) covers firmware, updates and
going back to stock.

## More detail

- [Build reference](BUILD.md): host setup, sync options, each step's checks and
  records, `--official`, `build.json`, [troubleshooting](BUILD.md#troubleshooting).
- [FP6 kernel](FP6-KERNEL.md), [firmware](FIRMWARE.md), [signing](SIGNING.md),
  [threat model](THREAT_MODEL.md).
