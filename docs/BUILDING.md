# Build DiamaneOS for the Fairphone 6

This guide builds a DiamaneOS test image for the Fairphone 6 from source and
flashes it. A test image is signed with public test keys. It is not a
release. Install it only on a phone whose bootloader you keep unlocked.

What you build is the current development line: the latest published commit
of every DiamaneOS repository. It is not a release and has not been tested on
a phone as a whole.

The whole path is four steps: check the tools, get the source, build, flash.

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

## 1. Get the tools and check them

```sh
git clone https://github.com/DiamaneOS/diamaneos-tools.git
cd diamaneos-tools
```

Check that the commit you build is signed by a DiamaneOS maintainer. The
maintainer keys are not published; with an allowed-signers file that lists
them:

```sh
git -c gpg.ssh.allowedSignersFile=/path/to/diamaneos-allowed-signers verify-commit HEAD
```

This checkout pins everything else. The build checks every download against
those pins: the GrapheneOS release signature, every source commit, the
Fairphone factory package and each file taken from it.

## 2. Get the source

```sh
bin/diamaneos build sync
```

This downloads the Android source into `~/diamaneos-build/src`. Use
`--workspace DIR` on every command to put the build somewhere else.

To download less, run `bin/diamaneos build sync --shallow` instead. It fetches
only the pinned commits, not their history. That saves roughly half of the
download and of the disk space, but the checkout has no history to look at,
and moving to a newer release later downloads more. The workspace remembers
the choice.

## 3. Build

```sh
bin/diamaneos build all
```

This builds a `user` image, like a release. For testing, with adb and root
debugging available, build a `userdebug` image instead:

```sh
bin/diamaneos build all --variant userdebug
```

`build all` builds the kernel, downloads Fairphone's factory package and takes
the files DiamaneOS needs from it, builds Android, packages the images and
checks them. The first build takes many hours. Each long command prints a
`live output` file that you can follow with `tail -f`. If the build stops, run
the same command again: finished steps are skipped. To see what is left
without changing anything, add `--dry-run`.

Give `build all` the same `--variant` each time; a different one rebuilds
Android. A single step, such as `build verify`, uses the variant Android was
built with.

At the end it prints where the images are, for example
`~/diamaneos-build/images/20261003-user-3f9a1c2b7d`. The directory also holds
`build.json`, which records exactly what the build was made from, and
`SHA256SUMS`.

DiamaneOS builds with its own copy of the Android build system, changed in
one place: it keeps the zero version fields the Fairphone 6's boot images
need in their headers when it rebuilds them for packaging. The checks at the
end confirm it.

## 4. Flash (test builds only)

Read these rules first:

- Never lock the bootloader with a test build installed. Never run
  `fastboot flashing lock` or `fastboot flashing lock_critical`. Anyone can
  sign images with the public test keys.
- Never run `fastboot -w` or `fastboot erase`. When a wipe is needed, the
  printed steps flash empty images instead, as Fairphone's own factory
  package does.
- Everything goes to slot a.
- The phone's firmware must come from the stock release the build's vendor
  files come from (`FP6.QREL.16.111.0` today). Never install firmware older
  than the phone already runs.

If you flash from another computer, copy the whole image directory there.

**First install** (from stock Android or another system):

1. Unlock the bootloader as
   [Fairphone describes](https://support.fairphone.com/hc/en-us/articles/10492476238865-How-to-unlock-or-lock-your-Fairphone-s-bootloader),
   including `fastboot flashing unlock_critical`, because the next step writes
   firmware.
2. Install Fairphone's stock factory package for that release with
   [Fairphone's instructions](https://support.fairphone.com/hc/en-us/articles/18896094650513-How-to-manually-install-Android-on-your-Fairphone).
   It writes the firmware.
3. Print the flash steps and run them:

   ```sh
   bin/diamaneos flash-steps --wipe
   ```

The wipe erases everything on the phone. It flashes empty userdata, metadata
and misc images and Fairphone's factory FRP image, so factory reset protection
is cleared and OEM unlocking stays allowed. The wipe is not yet tested on a
phone, and the printed steps say so.

**Updating** a phone that already runs a DiamaneOS test build:

```sh
bin/diamaneos flash-steps --since ~/diamaneos-build/images/<previous build>
```

This prints only the images that changed. Without `--since` it prints them
all.

**Back to stock:** install Fairphone's factory package as in step 2 of the
first install. Relock only after stock is back, and only with the checks in
the installer's
[recovery preflight](https://github.com/DiamaneOS/installer/blob/main/docs/recovery-preflight.md).

## When something goes wrong

| What you see | What to do |
| --- | --- |
| `this host cannot run the build` | It lists everything that is missing. Fix those and run the command again. |
| `missing Python modules ... install python3-jsonschema` | Install the package named, for the Python the error names. |
| `the build cannot compile with network access off` | It says why: user namespaces turned off, or util-linux older than 2.38. Fix that if you can; otherwise add `--allow-network`, and `build.json` records that the build had network access. |
| A signature or hash does not match | Do not work around it. Check your network, then report it: the download is not what this checkout pins. |
| `not our ref` or `resolved project commit is unavailable` during sync | The pinned commit is not published yet. Use an older tools commit or wait for the push. |
| `bytes of body are still expected` during sync | A long download broke off. Run the same command again; sync already uses HTTP/1.1 and retries, and finished repositories are not downloaded again. |
| `... is out of date; run "diamaneos build ..."` | A step you ran on its own needs an earlier step to run again first. Run the step it names, or `build all`. |
| The disk fills up | Free the space the host check asked for and run the command again. |
| A build step fails | The error names the log in `~/diamaneos-build/logs/`. Run the command again after fixing the cause; finished steps are skipped. |
| `checks failed` at the end | The report next to the image directory (`<build>.verify.json`) names each failed check and why it exists. Do not flash that build. |
| `the verification report belongs to another image set` | Run `bin/diamaneos build verify` again for this set. |
| Flashing `super` stops after its first part | Use the fastbootd steps printed under the main steps. |

## More detail

- [Build reference](BUILD.md): how the pins, the checks, the generated inputs
  and packaging work, and every step on its own.
- [FP6 kernel](FP6-KERNEL.md), [firmware](FIRMWARE.md) and
  [signing](SIGNING.md).
- [Threat model](THREAT_MODEL.md).
