# Build DiamaneOS

The short path from source to a phone running your own build. For anyone building DiamaneOS. The
[build reference](BUILD.md) explains each step in depth.

These commands sign every build with Android's public test keys. Anyone can sign images with those
keys, so keep the bootloader unlocked while such a build is installed. To lock a build, sign it with
[your own keys](SIGNING.md#your-own-keys).

## Requirements

- Linux on x86-64. Debian 13 is tested.
- 16 CPU cores or more (8 work, slowly) and 64 GB of RAM (32 GB is the minimum).
- About 500 GB free on an SSD with a case-sensitive filesystem, such as ext4, xfs or btrfs.
- A fast connection. The first sync downloads 100 to 150 GB.

Install the packages. `repo` is in Debian's `contrib` section, which [host setup](BUILD.md#host-setup)
shows how to enable.

```sh
sudo apt install git git-lfs repo python3 python3-jsonschema gnupg openssh-client \
  rsync zip unzip diffutils fontconfig fonts-dejavu-core gperf libc6-dev-i386 \
  lib32gcc-s1 hostname ca-certificates util-linux kmod binutils openssl make perl curl
```

## 1. Get the source

```sh
mkdir -p ~/diamaneos && cd ~/diamaneos
repo init -u https://github.com/DiamaneOS/platform_manifest.git -b android17
repo sync -j8
```

This downloads every project of the DiamaneOS manifest, including the build tools in
`tools/diamaneos`. Any directory works. Run the commands below from it.

The tools keep images, logs and build state next to the checkout, here in `~/diamaneos-build`.
`--workspace DIR` picks another directory.

## 2. Add the vendor files

```sh
tools/diamaneos/bin/diamaneos build vendor <codename>
```

`<codename>` is the device's codename from the [supported devices](https://diamaneos.de/devices/)
page. The command checks the source, applies the [patch series](SOURCES.md#the-patch-series), downloads
the device maker's factory package, checks its SHA-256 and takes the vendor files out of it.

It warns about what only an official build refuses, such as local changes or another `repo` version,
and `build.json` records them. It ends by printing the commands of step 3.

## 3. Build

```sh
source build/envsetup.sh
lunch <codename>-cur-user
m droid target-files-package otatools-package
```

Keep `droid`: without it the target-files archive lacks the vendor SELinux policy, and step 4 fails.
For a build to keep, remove `out` first (`rm -r out`), as GrapheneOS's build guide says.

`<codename>-cur-userdebug` builds a debuggable image with root over adb, for development.

## 4. Package and check

```sh
tools/diamaneos/bin/diamaneos build package
```

This checks that `out/` holds this source's build with these vendor files, then writes the image set
and runs every image check on it.

**Or in one command.** After step 1, `tools/diamaneos/bin/diamaneos build all` syncs and runs steps
2 to 4 itself, compiling without network access. After a stop, run it again. `--variant userdebug`
builds a debuggable image.

## 5. Flash

Unlock the bootloader as the [install guide](https://diamaneos.de/install/cli/#unlocking-the-bootloader)
describes, including `fastboot flashing unlock_critical`. Then, with the phone in the bootloader:

```sh
cd ~/diamaneos-build/images/latest
bash flash-all.sh
```

On Windows, run `flash-all.bat`. The script checks the phone and the images, erases the phone and
installs the image set. The phone restarts into fastbootd and back into the bootloader on its own.

- The script writes the device maker's firmware of the release the vendor files come from. Do not run
  it on a phone that already runs a newer stock release.
- Never run `fastboot -w` or `fastboot erase`.
- [Flashing](BUILD.md#flashing) covers updates that keep data, locking and going back to stock.

## Updating

```sh
tools/diamaneos/bin/diamaneos patches reset --source .
repo sync --force-sync -j8
tools/diamaneos/bin/diamaneos build vendor <codename>
m droid target-files-package otatools-package
tools/diamaneos/bin/diamaneos build package
```

- `patches reset` takes the patch series off, so `repo` can move every project. `build vendor` applies
  the new series.
- `--force-sync` lets `repo` replace a project that now comes from another repository at the same path.
- Run `m` in a shell set up as in step 3.
- DiamaneOS rebases its branches onto new GrapheneOS releases. Rebase your own commits onto the new
  branch tips (`git rebase --onto`).

## Rebuilding a release

```sh
mkdir -p ~/diamaneos-<release> && cd ~/diamaneos-<release>
repo init -u https://github.com/DiamaneOS/platform_manifest.git -b refs/tags/<release>
repo sync -j8
tools/diamaneos/bin/diamaneos build all --release <release> --official
```

`<release>` is a release number, such as `2026111400`. The tag pins every project, the build tools
and the patch series to the commits the release was built from. Check the tag's signature first, as
the [build guide](https://diamaneos.de/build/#verifying-the-source) shows.

- Give `--release <release>` to every command you run in this checkout.
- `--official` builds the release's own images, numbered with the release number. Leave it out for a
  build of your own from the same source.
- The images are signed with test keys, not the release keys. Their `resolved-manifest.xml` names
  the same project commits as the release's factory package.

## More

- [Build reference](BUILD.md): each step, [building by hand](BUILD.md#building-by-hand),
  [flashing](BUILD.md#flashing) and [troubleshooting](BUILD.md#troubleshooting).
- [Signing with your own keys](SIGNING.md#your-own-keys).
- [All documents](README.md).
