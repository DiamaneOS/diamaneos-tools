# Build DiamaneOS for the Fairphone 6

This guide builds a DiamaneOS test image for the Fairphone 6 from source and
flashes it. A test image is signed with public test keys. It is not a
release. Install it only on a phone whose bootloader you keep unlocked.

What you build is the current development line: the `android17` branch of the
DiamaneOS manifest, which selects every project the build uses. It is not a
release and has not been tested on a phone as a whole.

Building DiamaneOS works like building GrapheneOS: get the source with `repo`,
then build. The build tools come with the source, in `tools/diamaneos`. The
whole path is four steps: install the host packages, get the source, build,
flash.

## What you need

- A computer with Linux on x86-64. Debian 13 is the tested system.
- 16 CPU cores or more (8 works, slowly) and 64 GB of RAM (32 GB is the
  minimum).
- About 500 GB free on an SSD, on a case-sensitive filesystem such as ext4,
  xfs or btrfs. The build checks the exact figure before it starts.
- A fast internet connection. The first build downloads roughly 100 to 150 GB.
- For flashing: a Fairphone 6, a USB cable and `fastboot` from the Android
  platform tools, on any computer.

Debian ships `repo` in its `contrib` section. Add `contrib` to the
`Components:` line in `/etc/apt/sources.list.d/debian.sources` (or to each
`deb` line in `/etc/apt/sources.list`), then install the packages once:

```sh
sudo apt update
sudo apt install git git-lfs repo python3 python3-jsonschema gnupg openssh-client \
  rsync zip unzip diffutils fontconfig fonts-dejavu-core gperf libc6-dev-i386 \
  lib32gcc-s1 hostname ca-certificates util-linux kmod binutils openssl make perl
```

If you would rather not enable `contrib`, leave `repo` out and install the
`repo` launcher as its [install instructions](https://gerrit.googlesource.com/git-repo#install)
describe.

That is the only step that needs `sudo`. Everything else runs as your own
user.

The build compiles with network access turned off. It does that with an
unprivileged user namespace, which Debian allows by default. Some systems
restrict them (Ubuntu 24.04 does through AppArmor); the build then stops and
says why.

## 1. Get the source

The build keeps everything in one workspace directory, `~/diamaneos-build`,
with the source in its `src` directory:

```sh
mkdir -p ~/diamaneos-build/src
cd ~/diamaneos-build/src
repo init -u https://github.com/DiamaneOS/platform_manifest.git -b android17
repo sync -j8
```

To put the workspace somewhere else, use `DIR/src` here and pass
`--workspace DIR` to every `diamaneos` command.

To download less, add `--depth=1` to `repo init` and use
`repo sync -c --no-tags -j8`, then pass `--shallow` to the first `diamaneos`
command. The checkout then has no history, which saves roughly half of the
download and of the disk space; moving to a newer GrapheneOS release later
downloads more. The workspace remembers the choice.

A newer release can take a project from another repository at the same path
(GrapheneOS forking an AOSP project). `diamaneos build` removes the clean old
checkout before syncing; a plain `repo sync` needs `--force-sync <path>` for it.

The manifest commits are signed by a DiamaneOS maintainer. The maintainer keys
are not published; with an allowed-signers file that lists them:

```sh
git -C .repo/manifests -c gpg.ssh.allowedSignersFile=/path/to/diamaneos-allowed-signers \
  verify-commit HEAD
```

## 2. Build

From `~/diamaneos-build/src`:

```sh
tools/diamaneos/bin/diamaneos build all
```

This builds a `user` image, like a release. For testing, with adb and root
debugging available, build a `userdebug` image instead:

```sh
tools/diamaneos/bin/diamaneos build all --variant userdebug
```

`build all` first syncs the source to the head of the manifest branch, as
`repo sync` does, with the pinned `repo` tool, and checks the whole tree: every
project clean at the commit the manifest selects and nothing else in it. It
records the exact commit of every project (`repo manifest -r`). Then it
downloads Fairphone's factory package and takes the files DiamaneOS needs from
it (the `vendor` step), builds Android, packages the images and checks them.
The kernel is not built: it comes prebuilt from the manifest
(`device/fairphone/FP6-kernel`). The first build takes many hours. Each long
command prints a `live output` file that you can follow with `tail -f`. If the
build stops, run the same command again: finished steps are skipped, and later
steps run again only if the synced source changed. To see what is left without
changing anything, add `--dry-run`; to rebuild without syncing again, add
`--from vendor`.

Give `build all` the same `--variant` each time; a different one rebuilds
Android. A single step, such as `build verify`, uses the variant Android was
built with.

DiamaneOS's own builder adds `--official`: the image then includes the
Updater, which checks DiamaneOS's update server (`releases.diamaneos.de`) for
updates. Leave it off for your own builds. The workspace remembers it, so
later commands there build official images too, until `--no-official`. An
official build that is signed with the public test keys, as every build of
these commands is, checks for updates but downloads and installs none.

At the end it prints where the images are, for example
`~/diamaneos-build/images/20261003-user-3f9a1c2b7d`. The directory also holds
`build.json`, which records exactly what the build was made from (the manifest
commit, the kernel prebuilts commit and the vendor inputs),
`resolved-manifest.xml` with every project's commit, and `SHA256SUMS`.

DiamaneOS builds with its own copy of the Android build system, changed in
one place: it keeps the zero version fields the Fairphone 6's boot images
need in their headers when it rebuilds them for packaging. The checks at the
end confirm it.

**Building by hand.** After `tools/diamaneos/bin/diamaneos build sync` and
`build vendor`, `build inputs --source . --vendor ~/diamaneos-build/vendor`
installs the vendor tree, and the usual
`source build/envsetup.sh && lunch FP6-cur-userdebug && m` builds Android. The
tools do not package or check images built this way; flash only image sets
from `build all`.

## 3. Flash (test builds only)

Read these rules first:

- Never lock the bootloader with a test build installed. Never run
  `fastboot flashing lock` or `fastboot flashing lock_critical`. Anyone can
  sign images with the public test keys.
- Never run `fastboot -w` or `fastboot erase`. When a wipe is needed, the
  printed steps flash empty images instead, as Fairphone's own factory
  package does.
- The OS goes to slot a only. The firmware steps write both slots of each A/B
  firmware partition, as Fairphone's own flash does.
- Never install firmware older than the phone already runs. `flash-steps`
  refuses to.

If you flash from another computer, copy the whole image directory there.

**Firmware.** Each image set carries Fairphone's firmware of the release its
vendor files come from (`FP6.QREL.16.111.0` today). `flash-steps` writes it
first when the phone runs older firmware, prints no firmware steps when the
phone runs that release, and stops when it runs a newer one. The FP6 bootloader
does not report its firmware, so name it with `--phone-firmware`: the build
number in Settings > About phone on stock Android, or the firmware the last
DiamaneOS flash steps installed (`--since` reads it from that image set). For
firmware steps, also save the phone's state in the bootloader and pass it with
`--phone`:

```sh
{ fastboot getvar all; fastboot oem device-info; } > phone.txt 2>&1
```

`--no-firmware` leaves the firmware as it is. The firmware steps are not yet
tested on a phone, and the printed steps say so.

**First install** (from stock Android or another system):

1. Note the build number in Settings > About phone, for example
   `FP6.QREL.16.100.0`.
2. Unlock the bootloader as
   [Fairphone describes](https://support.fairphone.com/hc/en-us/articles/10492476238865-How-to-unlock-or-lock-your-Fairphone-s-bootloader),
   including `fastboot flashing unlock_critical`, because the steps write
   firmware.
3. In the bootloader, save the phone's state as above, then print the flash
   steps and run them:

   ```sh
   tools/diamaneos/bin/diamaneos flash-steps --wipe --phone phone.txt --phone-firmware FP6.QREL.16.100.0
   ```

The wipe erases everything on the phone. It flashes empty userdata, metadata
and misc images and Fairphone's factory FRP image, so factory reset protection
is cleared and OEM unlocking stays allowed; unless you pass `--no-firmware`, it
also writes Fairphone's empty logfs and vm-persist images. The wipe is not yet
tested on a phone, and the printed steps say so.

**Updating** a phone that already runs a DiamaneOS test build:

```sh
tools/diamaneos/bin/diamaneos flash-steps --since ~/diamaneos-build/images/<previous build>
```

This prints only the images that changed, and the firmware only when the new
set carries newer firmware than the earlier one (then add `--phone`). An earlier
image set without firmware does not say what the phone runs; add
`--phone-firmware`. Without `--since` it prints every image.

**Back to stock:** install Fairphone's factory package with
[Fairphone's instructions](https://support.fairphone.com/hc/en-us/articles/18896094650513-How-to-manually-install-Android-on-your-Fairphone).
Relock only after stock is back, and only with the checks in the installer's
[recovery preflight](https://github.com/DiamaneOS/installer/blob/main/docs/recovery-preflight.md).

## When something goes wrong

| What you see | What to do |
| --- | --- |
| `this host cannot run the build` | It lists everything that is missing. Fix those and run the command again. |
| `missing Python modules ... install python3-jsonschema` | Install the package named, for the Python the error names. |
| `the build cannot compile with network access off` | It says why: user namespaces turned off, or util-linux older than 2.38. Fix that if you can; otherwise add `--allow-network`, and `build.json` records that the build had network access. |
| A signature or hash does not match | Do not work around it. Check your network, then report it: the download is not what the tools pin. |
| `not our ref` during sync | A commit the manifest selects is not published yet. Run the command again after the push. |
| `holds local manifests the build does not use` | Move the files in `.repo/local_manifests` away; the build uses the manifest alone. |
| `contains dirty or untracked content` or `undeclared input` | A project or the space between projects was changed. Undo the change (or move it aside) and run the command again. |
| `bytes of body are still expected` during sync | A long download broke off. Run the same command again; sync already uses HTTP/1.1 and retries, and finished repositories are not downloaded again. |
| `... is out of date; run "diamaneos build ..."` | A step you ran on its own needs an earlier step to run again first. Run the step it names, or `build all`. |
| The disk fills up | Free the space the host check asked for and run the command again. |
| A build step fails | The error names the log in `~/diamaneos-build/logs/`. Run the command again after fixing the cause; finished steps are skipped. |
| `checks failed` at the end | The report next to the image directory (`<build>.verify.json`) names each failed check and why it exists. Do not flash that build. |
| `the verification report belongs to another image set` | Run `tools/diamaneos/bin/diamaneos build verify` again for this set. |
| Flashing `super` stops after its first part | Use the fastbootd steps printed under the main steps. |

## More detail

- [Build reference](BUILD.md): how the manifest, the checks, the generated
  inputs and packaging work, and every step on its own.
- [FP6 kernel](FP6-KERNEL.md), [firmware](FIRMWARE.md) and
  [signing](SIGNING.md).
- [Threat model](THREAT_MODEL.md).
