# Build DiamaneOS

Build DiamaneOS from source and install it on a supported device. These commands sign every build
with Android's public test keys: keep the bootloader unlocked while such a build is installed.

## Requirements

- Linux on x86-64 (Debian 13 is tested), 16 CPU cores or more (8 work, slowly) and 64 GB of RAM (32
  GB minimum).
- About 500 GB free on an SSD with a case-sensitive filesystem (ext4, xfs or btrfs), and a fast
  connection: the first sync downloads 100 to 150 GB.
- These packages (`repo` is in Debian's `contrib` section, see [host setup](BUILD.md#host-setup)):

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

- Downloads every project of the DiamaneOS manifest, including the build tools in `tools/diamaneos`.
- Another directory works as well, as long as the checkout is its `src`: the tools use the
  directory above the checkout they run from as their workspace.
- Stay in this directory.

## 2. Add the vendor files

```sh
tools/diamaneos/bin/diamaneos build vendor
```

Checks the source, downloads the device maker's factory package, checks its SHA-256 and takes the
vendor files out of it into the source tree.

- It warns about what only an official build refuses: local changes, local manifests, files between
  the projects, another `repo` version. `build.json` records them.
- Run again, it makes the vendor files only when their recipe changed.

## 3. Build

```sh
source build/envsetup.sh
lunch <codename>-cur-user
m target-files-package otatools-package
```

- `<codename>` is the device's codename, listed on the [supported
  devices](https://diamaneos.de/devices/) page.
- The usual Android build, plus what step 4 packages and checks. A plain `m` builds the images only.
- `<codename>-cur-userdebug` builds a `userdebug` image with root debugging over adb, for
  development.

## 4. Package and check

```sh
tools/diamaneos/bin/diamaneos build package
```

Checks that `out/` holds this source's build with these vendor files, then writes and checks the
image set step 5 flashes, marked as built by hand.

**Or in one command:** after step 1, `tools/diamaneos/bin/diamaneos build all` syncs and does steps
2 to 4 itself, compiling without network access and checking every step. After a stop, run it
again. `--variant userdebug` builds a `userdebug` image.

## 5. Flash

Unlock the bootloader as the [install guide](https://diamaneos.de/install/#unlocking-the-bootloader)
describes, including `fastboot flashing unlock_critical`. Then, in the bootloader:

```sh
cd ~/diamaneos-build/images/latest
bash flash-all.sh
```

- It checks the phone and the images, then erases the phone and installs the image set. On Windows,
  run `flash-all.bat`.
- The phone restarts into fastbootd for the system partitions and back into the bootloader on its
  own.
- It writes the device maker's firmware of the release the build's vendor files come from, then the
  OS. Do not run it on a phone that already runs a newer stock release.
- Never lock the bootloader or run `fastboot -w` or `fastboot erase`.
- [Flashing](BUILD.md#flashing) covers updates that keep data, firmware and going back to stock.

## Updating

```sh
repo sync --force-sync -j8
tools/diamaneos/bin/diamaneos build vendor
m target-files-package otatools-package
tools/diamaneos/bin/diamaneos build package
```

- `--force-sync` lets `repo` replace a project that now comes from another repository at the same
  path.
- Run `m` in a shell set up as in step 3 (`envsetup.sh` and `lunch`).
- DiamaneOS rebases its branches onto new GrapheneOS releases: rebase your own commits onto the new
  branch tips (`git rebase --onto`).
- `build vendor` is quick unless DiamaneOS moved to another stock release or changed the vendor
  file list.

## More detail

- [Build reference](BUILD.md): host setup, each step, [building by hand](BUILD.md#building-by-hand)
  and [troubleshooting](BUILD.md#troubleshooting).
- [Signing](SIGNING.md) and [terms](TERMS.md).
- The FP6 profile: [firmware](FIRMWARE.md) and [kernel](FP6-KERNEL.md).
