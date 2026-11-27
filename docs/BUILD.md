# Build reference

How the build tools work: each step, what it checks and records, official and release builds,
flashing and troubleshooting. For anyone who builds DiamaneOS and wants more than the
[quickstart](BUILDING.md).

The tools are `tools/diamaneos/bin/diamaneos` in a source checkout, and `--help` on each command
lists its options. The examples use the Fairphone 6 (FP6) profile: `config/devices/FP6.json`, the
`config/fp6-*.json` files, `config/build-environment-fp6.json` and the device tree at
`device/fairphone/FP6`. Terms are explained in [TERMS.md](TERMS.md).

Related documents: [SOURCES.md](SOURCES.md) for the manifest and the patch series,
[FP6-VENDOR.md](FP6-VENDOR.md) for the vendor files, [FIRMWARE.md](FIRMWARE.md),
[FP6-KERNEL.md](FP6-KERNEL.md) and [SIGNING.md](SIGNING.md).

## Host setup

[BUILDING.md](BUILDING.md#requirements) lists the hardware and the packages.

`repo` is in Debian's `contrib` section. Add `contrib` to the `Components:` line in
`/etc/apt/sources.list.d/debian.sources` (or to each `deb` line in `/etc/apt/sources.list`), then run
`sudo apt update` before the install. Without `contrib`, leave `repo` out and install the launcher as
its [install instructions](https://gerrit.googlesource.com/git-repo#install) describe.

The package install is the only step that needs `sudo`. Everything else runs as your own user.

Compilation runs without network access, in an unprivileged user namespace. Debian allows these by
default. Some systems restrict them (Ubuntu 24.04 does, through AppArmor), and the build then stops
and says why ([network](#network)).

Flashing needs only the phone, a USB cable and `fastboot` from the Android platform tools, on any
computer.

## Workspace and source

The tools keep images, logs, downloads and build state in one workspace directory. Each command
prints the one it uses:

| Given | Workspace |
| --- | --- |
| `--workspace DIR` | `DIR` |
| `DIAMANEOS_WORKSPACE` | that directory |
| Tools run from a checkout, at `<checkout>/tools/diamaneos` | `<checkout>-build` next to the checkout, or for a checkout named `src`, the directory that holds it |
| None of these | `~/diamaneos-build` |

The default next to the checkout must be on the same filesystem as the checkout. The workspace may
not be the checkout or a directory inside it. The source is the workspace's `src` directory if it has
one, else the checkout the tools run from.

Run the tools from the source tree (`tools/diamaneos`), so they match the source
([tools and source](#tools-and-source)).

**Smaller download.** Add `--depth=1` to `repo init`, sync with `repo sync -c --no-tags -j8` and
pass `--shallow` to the first `build sync` or `build all`.

The checkout then has no history, which saves roughly half of the download and the disk space.
Moving to a newer release later downloads more.

The workspace remembers the shallow choice (`state/shallow`). Before `repo sync`, a shallow sync
removes project directories that an interrupted sync left without refs, which `repo` would fetch
with their whole history.

It also fetches the largest prebuilt projects itself (`shallow_prefetch` in
`config/fp6-build.json`), one revision each at depth 1, with a speed limit and bounded retries.

**Moved projects.** A newer release can take a project from another repository at the same path.
`diamaneos build` removes the clean old checkout before it syncs. A plain `repo sync` needs
`--force-sync` for it.

**Signatures.** The manifest commits and DiamaneOS's own commits are signed with an SSH key. The
[build guide](https://diamaneos.de/build/#verifying-the-source) gives the line the allowed signers
file must hold. Then check the manifest:

```sh
curl --fail --create-dirs -o ~/.ssh/diamaneos_allowed_signers \
  https://diamaneos.de/allowed_signers.txt
git -C .repo/manifests -c gpg.ssh.allowedSignersFile=~/.ssh/diamaneos_allowed_signers \
  verify-commit HEAD
```

## The build steps

`build all` runs five steps in one workspace. Each step can also run on its own, as `build sync`,
`build vendor` and so on.

| Step | What it does | What it checks and records |
| --- | --- | --- |
| `sync` | `repo init` on the manifest branch with the pinned `repo` tool, then `repo sync`. Takes the [patch series](SOURCES.md#the-patch-series) off before and applies it again after. | The `repo` tool's signed tag, that `.repo/manifests` is the declared manifest at the head of its branch, then the full source check. Records the resolved manifest, the project map, the manifest commit and the kernel prebuilts commit. |
| `vendor` | Builds the image tools from the synced source, downloads the factory package, extracts the vendor files and installs them in the source ([FP6-VENDOR.md](FP6-VENDOR.md)). Prints the `lunch` and `m` commands. | The package's size and SHA-256, and each image and file against the recipes. |
| `android` | `lunch <codename>-cur-<variant>`, then `m` with network access off. | The source check before and after. Records the target-files archive and the build identity. |
| `package` | Exports the images from the target-files archive, adds the stock firmware and the wipe images, builds `super.img`, writes `build.json`, `SHA256SUMS` and the install scripts. | The archive hashes, the factory package and each firmware image, and the wipe images against the stock partition table. |
| `verify` | Runs the [image checks](#image-checks). | Writes the report `<build_id>.verify.json` next to the image directory. |

`build vendor` needs the device codename, as in `build vendor FP6`. The other steps take it too, but
need it only when more than one device is supported.

Each device has an entry in `config/devices/<codename>.json` that names its recipes, its vendor tree
and its lunch product.

A step refuses to start while an earlier step it uses has not run or is out of date. `build vendor`
and `build package` check or record those steps themselves ([building by hand](#building-by-hand)).

### Running build all

```sh
tools/diamaneos/bin/diamaneos build all
```

- Each long command prints a `live output` file to follow with `tail -f`.
- After a stop, run the same command again. Finished steps are skipped, and later steps run again
  only when the synced source changed.
- `--dry-run` shows what is left without changing anything. `--from vendor` reruns from that step
  without syncing again.
- `user` is the default variant. `--variant userdebug` builds with root debugging over adb, for
  development. Give `build all` the same `--variant` each time, because another one rebuilds Android.
- The manifest branch moves, so `build all` starts with `sync` every time. It keeps the source of a
  pinned manifest commit, a release tag or a reproduced build.

At the end it prints the image directory, such as `~/diamaneos-build/images/20261003-user-3f9a1c2b7d`,
and `images/latest` points to it. It holds the images, `build.json`, `resolved-manifest.xml`,
`SHA256SUMS`, `flash-all.sh` and `flash-all.bat`.

### State and resume

Each step writes `state/<step>.json` with a digest of its inputs (the configuration it reads, its code
and the outputs of earlier steps), its outputs and its log. `build all` skips a step whose input digest
is unchanged and whose outputs still verify.

Each step prints its log path when it starts, and a short title for each action. The log and
`--dry-run` show the full command.

A second command on the same workspace fails at once (`.workspace.lock`). A failure is recorded in
the step's state with a plain message.

## What the build checks

### Tools and source

The vendor selection and the image checks must match the device tree, so the tools must match the
source. After `sync`, the command compares its own commit with the one the sync checked out at
`tools/diamaneos`:

| Tools that run | Result |
| --- | --- |
| The same commit, or a source without that project | Nothing to say. |
| Newer tools that contain that commit | A note, and `build.json` records the tools commit. |
| Older or diverged tools | A warning that names the synced tools to run instead. An official build stops. |
| The checkout the sync just moved | The command starts again with the new tools, keeping the sync. |

### Local changes and deviations

A build that is not official accepts these, with a warning that names each one:

- Projects with local changes: edited, added or deleted files.
- Another `repo` tool than the pinned one, or another origin of the manifest checkout.
- A remote without HTTPS, or local manifests in `.repo/local_manifests` (left to `repo`).
- A project at another commit than the one the manifest pins.
- A manifest that is not the pinned GrapheneOS release with one included file
  ([the manifest](SOURCES.md#the-diamaneos-manifest)).
- Files outside the projects. A `keys` directory in the source root that holds only key files is not
  one, because no build reads it.

The sync state and `build.json` list them (`modified` and `source.deviations`), and `build.json` marks
the set `reproducible: false`. A digest of the changed files joins the source identity, so a new edit
rebuilds Android and gets its own image directory.

`--official` refuses all of them. Every build stops on a changed `.repo/manifests`. With an environment
that pins one manifest commit, run `build all --from sync` after an edit.

`build.json` also says when the tools were not the source's (`tools_skew`) and whether the vendor files
were made from this source (`generated_inputs.vendor_made_from_this_source`).

### Device policy record

Before it compiles, the `android` step hashes each file that
`device/fairphone/FP6/sepolicy/provenance.json` lists and compares it with the recorded SHA-256. A
difference stops every build the tools run, so a test build finds what an official build would
refuse.

A [build by hand](#building-by-hand) only warns. A record that cannot be read stops an official
build and gives a warning otherwise.

### Host check

Before it runs, the command checks the host for the steps it will run:

- Linux on x86-64, and Python 3.11 or newer with `jsonschema`.
- The commands each step uses (`modinfo` and `modprobe` are also looked for in `/usr/sbin` and `/sbin`).
- At least 32 GB of RAM, and the free disk space of the steps whose output does not exist yet.
- A case-sensitive filesystem.
- For compile steps, unprivileged user namespaces and util-linux 2.38 or newer.

It records the host in `build.json`. Package versions that differ from the build environment's are
recorded, not fatal.

### Network

Only `repo`, `git fetch`, the kernel preparation and the factory package download use the network.
Downloads use HTTPS only, stop at the expected size and refuse redirects to another host or to plain
HTTP.

Compilation of the kernel, the image tools and Android runs inside `unshare --user
--map-current-user --net`. The build keeps its user id and has no network.

It also loses the variables that lead to local agents and services, such as `SSH_AUTH_SOCK`,
`DBUS_*` and `DOCKER_HOST`. Unix sockets in the filesystem stay reachable.

- Without unprivileged user namespaces the command stops. `--allow-network` builds anyway and records
  `network_isolation: off`.
- If `DIAMANEOS_THERMAL_CHECK` names a program, it runs before every compile and a non-zero exit stops
  the build.
- Temporary files go to the workspace. Only `repo` gets a directory in `/tmp`, because Linux limits
  the path length of its sockets.

## Build records and identity

The **source identity** is a digest of the build environment file, the source project map, the
resolved manifest, the manifest commit, the kernel prebuilts commit, the vendor inventory, the
variant and the build parts of `config/fp6-build.json`.

| Value | How it is set |
| --- | --- |
| `BUILD_NUMBER` | `test.` and the first 12 digits of the source identity, unless `DIAMANEOS_BUILD_NUMBER` sets it |
| `BUILD_DATETIME` | The newest committer time among the synced projects and the manifest commit |
| `BUILD_USERNAME`, `BUILD_HOSTNAME` | Fixed values from `config/fp6-build.json` |
| Image directory | `<date>-<variant>-<build identity>` |

The **build identity** adds the build number, the network isolation, the tools commit and the
target-files hash. An existing image directory is reused only when its record names the same
target-files and identity.

`build.json` in each image set records what the set was made from: the manifest and kernel prebuilts
commits, the vendor inputs, the tools commits that built and packaged it, the host, the firmware and
the flash steps. `resolved-manifest.xml` names every project's commit.

**Reproducibility.** The same resolved manifest gives the same source identity and generated inputs.
The kernel, its modules and device trees come prebuilt from the manifest, so they are the same on
every host.

The same inputs are meant to give identical partitions on any host, but no command here compares two
hosts' builds.

A modified tools checkout, packaging by another tools commit than the build, tools that are not the
source's, source deviations, and vendor files kept from an earlier source each make the set
`reproducible: false`.

**Generated-input descriptor.** `build inputs`, which the `vendor` and `android` steps call, writes
`.repo/diamaneos-generated-inputs.json`. It binds the vendor tree to the build environment, the
recipe digests and the tree's complete inventory.

The source check accepts the generated directory only while every file matches. A tree that no
longer matches moves aside to `.repo/diamaneos-previous-inputs/`.

## Official builds and releases

**Official builds.** `--official` builds images that include the Updater, which checks
`releases.diamaneos.de` for updates. It is for DiamaneOS's own builder: leave it off for your own
builds. An official build signed with the public test keys checks for updates but installs none.

- `--official` gives the Android build `DIAMANEOS_OFFICIAL_BUILD=true`, which
  `vendor/diamaneos/product.mk` reads to add the Updater. Every other build runs without the variable.
- The choice is part of the build identity and recorded as `official` in `build.json`.
- The workspace remembers it (`state/official`) until `--no-official`.
- An official build needs `DIAMANEOS_BUILD_NUMBER` as a release number: the release date and a
  two-digit serial, such as `2026102700`.

**A release from its tag.** `--release RELEASE` builds a published release from its signed tag, after
`repo init -b refs/tags/RELEASE` and `repo sync`:

- Give it to every step, and to `build preflight`. `build all` then keeps the source at the tag
  instead of syncing every time.
- The checks are those of a branch build, with the tag's commit in place of the branch head. The tag
  must be an annotated tag, and `build.json` records it (`manifest_tag_object`, `manifest_branch`).
- A checkout set up from a tag follows that release. A command without `--release` stops instead of
  moving it to the branch.
- The tools do not check the tag's signature. Check it by hand first, as the
  [build guide](https://diamaneos.de/build/#verifying-the-source) shows.
- With `--official` it builds the release's own images, numbered with the release number.
  `DIAMANEOS_BUILD_NUMBER` must be unset or name that release. Without `--official` it is a build of
  your own.

**Other environments.** `--environment FILE` selects another build environment file, such as a copy
of the FP6 environment with another manifest branch, or with `manifest.revision` set to build one
manifest commit.

## Reproducing a build

```sh
tools/diamaneos/bin/diamaneos build all --resolved-manifest DIR/resolved-manifest.xml \
  --build-json DIR/build.json
```

This syncs the source of the image set in `DIR`. After the usual `repo init`, it checks out the
manifest commit `build.json` records, then `repo sync -m` checks out every project at the commit the
resolved manifest names. Without `build.json`, `--manifest-commit` gives the manifest commit.

The resolved manifest must match the SHA-256 in `build.json`, and the manifest commit must be on the
manifest branch.

The resolved manifest may differ from that commit's manifest only in the project commits. After
`repo sync`, `repo manifest -r` must give it back byte for byte.

The sync records the reproduction (`pinned_manifest`), and later `build all` runs keep that source.
A plain `build sync` moves back to the branch head. Reproduce an official build with `--official`.
Another tools commit gives the same source identity but another image directory.

## OTA test builds

The update test installs a build N+1 on a phone that runs N, by incremental update. Two builds of one
tree share their build date, and an update needs a later one. `--ota-successor-of` builds N+1 from N's
image set:

```sh
tools/diamaneos/bin/diamaneos ota-test plan --base "$N"
tools/diamaneos/bin/diamaneos build all --workspace "$TEST_WORKSPACE" \
  --resolved-manifest "$N/resolved-manifest.xml" --build-json "$N/build.json" --ota-successor-of "$N"
tools/diamaneos/bin/diamaneos ota-test incremental --base "$N" --successor "$N1" \
  --output "$OUT/n-to-n1-incr.zip" --source-root "$SOURCE_ROOT"
```

- N+1 is N's tree with no local changes, N's variant and N's official choice.
- Its build date is N's plus 60 seconds and its build number N's with `.1`, such as `2026102700.1`.
  `DIAMANEOS_BUILD_NUMBER` must be unset.
- `build.json` marks it `ota_test`, and release signing refuses it.
- N's own workspace can build it once N's image set exists. A later build with other changes then
  needs a plain `build sync` first.

`ota-test incremental` makes the incremental with N+1's `otatools.zip` and checks it: an A/B package
from N's build number to N+1's, with N+1's date and no wipe or downgrade flag. Signed copies need
`--source-target-files`, `--target-target-files` and `--key`. `--source-root` puts the source's JDK on
`PATH`.

## Building by hand

The plain Android flow of [BUILDING.md](BUILDING.md): `repo sync`, `build vendor <codename>`, `lunch`
and `m`, then `build package`.

**`build vendor`** checks the checkout as `sync` does after its `repo sync`, runs no `repo` command
and records the result as the sync.

It applies the patch series, so `m` builds the patched sources. Take the series off with `patches
reset` before the next `repo sync`, which stops at a patched file that changed upstream.

It makes the vendor files again only when its recipes, code or settings changed, and for an official
build also when the source changed. An official build needs the pinned `repo` tool, the `repo_tool`
release tag of the build environment (`repo init --repo-rev=v2.74.1`).

**`m droid target-files-package otatools-package`** builds the images, the target-files archive and
`otatools.zip`, which holds the host tools that packaging and the checks use.

Keep `droid`: without it the archive lacks the vendor and odm SELinux policy (`VENDOR/etc/selinux`)
and the checks fail. A later `m` does not remake the archive, so `build package` stops on a stale
one.

**`build package`** records the build in `out/` as the Android step, with no `m` of its own, then
packages and checks the set. It stops with one message when:

- `out/` lacks the target-files archive or `otatools.zip`, or the archive is stale.
- `out/` holds another variant than `--variant` asks for. Without `--variant`, it takes the one in
  `out/`.
- The source changed since `build vendor` checked it, or holds another vendor tree.
- The images do not carry the vendor files byte for byte (the `vendor-binding` check).
- The workspace builds official images, which come only from `build all`.

File times cannot prove what `m` built, so an `out/` older than the last `build vendor` is only a
note.

`build.json` marks the set `android_build: manual` with `network_isolation: off`, no tools commit
and `reproducible: false`. `build all` never reuses such a record and builds Android itself.

## Building the kernel

The Android build does not compile the kernel. It uses the kernel prebuilts the manifest checks out at
`device/fairphone/FP6-kernel`. To build that kernel set from source:

```sh
tools/diamaneos/bin/diamaneos build kernel
```

`build kernel` runs `kernel prepare` (with network) and `kernel build --config-profile production`
(without network) in the workspace's `kernel` directory. It is not part of `build all`.
`kernel publish` copies a passed build into a checkout of the kernel prebuilts repository.

[FP6-KERNEL.md](FP6-KERNEL.md) describes the sources, the build, its checks and publishing.

## Packaging

The target-files archive is the authority for the images. Its `IMAGES/` were made together by the
build, so the AVB descriptors match them, and `super.img` is built from the same archive.

Nothing is repacked, and the images in the product output directory are never mixed in.

`build package` runs its host tools, and those of `verify`, from the build's own `otatools.zip`,
unpacked under `work/tmp`. The target-files archive is also the unsigned input for signing with your
own keys ([SIGNING.md](SIGNING.md)).

DiamaneOS's fork of `build/make` differs from its upstream in one change: when the release tools
rebuild boot, init_boot and recovery from target-files, they keep the zero OS version and patch level
in the image headers. The versions live in the AVB properties.

`avb_pkmd.bin` is the public test key that `vbmeta.img` embeds, and `build.json` names it in
`avb_custom_key`. The install scripts write it only for a lock you ask for.

**Wipe images.** A wipe flashes empty images, as the stock factory package does, and nothing is
cleared with `fastboot erase`:

| Image | Content |
| --- | --- |
| `userdata.img` | 4 MiB of zeros. First boot formats `/data` with the phone's own size and settings. |
| `metadata.img` | An empty f2fs filesystem of the partition's size, made with a fixed UUID, time and seed. |
| `frp.img` | The stock `frp_for_factory.img`: factory reset protection cleared, OEM unlocking still allowed. |
| `misc.img` | Zeros of the stock misc partition's size. |

`config/fp6-build.json` declares each one with its reason. While `wipe.validated` there is false, the
printed flash steps say that the wipe has not been tested on a phone.

**Firmware.** The image set carries the device maker's firmware of the release its vendor files come
from, copied from the authenticated factory package under the stock file names.
[FIRMWARE.md](FIRMWARE.md) describes the images, their checks and how they are flashed.

## Image checks

`build verify` checks an image set before anything can flash it. A failed check stops `build verify`,
and with it `build all` and `build package`. The set then moves to `<build_id>.failed`, and `latest`
no longer points to it.

The report `<build_id>.verify.json` names each check, its result and the reason it exists. It also
carries the digest of the set's `SHA256SUMS`. Every set gets these checks:

| Check | What it requires |
| --- | --- |
| `sums` | Every file matches `SHA256SUMS`. |
| `record` | The set matches its `build.json`, and stays unlocked unless you ask for a lock. |
| `test-keys` | The set is signed with the public test keys and says so in every fingerprint. |
| `avb-chain` | The AVB chain verifies with the test key and the FP6 layout: vbmeta at rollback location 0, recovery at 28, vbmeta_system at 29, boot at 30 and init_boot at 31. An official build has a rollback index above 0 everywhere. |
| `boot-header` | Boot-family headers carry zero OS fields. |
| `super` | `super.img` holds exactly the logical images, and `super_empty.img` lists the same partitions. |
| `validators` | `validate_target_files` and `check_target_files_vintf` pass. |
| `kernel-binding` | boot, vendor_boot and dtbo carry the kernel prebuilts that `build.json` names. |
| `build-identity` | Every partition carries the build date and number that `build.json` names. |
| `modules` | Module placement and load lists match the kernel prebuilts, and no module is denied or unsigned. |
| `vendor-binding` | Every selected stock file arrives with its generated bytes. |
| `vendor-patch-level` | The vendor patch level is that of the stock vendor image. |
| `firmware-release-table` | The vendor image carries the firmware release table of the firmware inventory. |
| `selinux-enforcing` | No permissive domain beyond what the variant allows. |
| `bootconfig` | The required bootconfig is there, and nothing overrides SELinux. |
| `kernel-arguments` | No device tree, overlay, boot header or bootconfig sets an argument that turns a protection off (`kernel_arguments` in `config/fp6-build.json`). |
| `adb-keys` | No pre-trusted adb key. |
| `wipe-images` | The wipe images are the declared ones. |
| `firmware` | Every firmware image of the release, byte for byte, in the stock order, with no lower anti-rollback version. |
| `firmware-ota` | An update from this target-files carries the stock A/B firmware and no other firmware partition. |

Then come the device rules of `config/fp6-image-checks.json`, each with its reason and, where it
matters, the variants and the kind of build it applies to. For example, `no-diagnostic-sanitizer`
refuses the full UBSan runtime in any native binary of a `user` build.

Some rules read the built code. `decode-gate-framework-hooks` and `vold-cards-in-vm-hooks` name each
upstream method of `framework.jar` and each function of `vold` that no longer calls its hook, for
example after a rebase.

`build.json`'s `signing` value selects the profile. A set signed with the public test keys gets the
checks above. A set signed with your own keys gets signed versions of `record`, `test-keys`,
`avb-chain` and `validators`, through [`images check`](SIGNING.md#check-a-signed-image-set).

## Flashing

The commands here make builds signed with the public test keys. Anyone can sign images with those
keys, so a phone locked with such a build accepts images and updates from anyone. Keep the bootloader
unlocked while one is installed.

- Never run `fastboot -w` or `fastboot erase`. A wipe flashes empty images instead.
- The OS goes to slot a only. The firmware steps write both slots of each A/B firmware partition, as
  the stock flash does.
- Never install firmware older than the phone already runs.
- To flash from another computer, copy the whole image directory there.

### First install

1. Check the build number in Settings > About phone on stock Android. It must not be a newer stock
   release than the one the image set carries (`firmware.release` in `build.json`).
2. Unlock the bootloader as the [install guide](https://diamaneos.de/install/cli/#unlocking-the-bootloader)
   describes, including `fastboot flashing unlock_critical`, because the install writes firmware.
3. In the bootloader, run `bash flash-all.sh` in the image directory (`flash-all.bat` on Windows).

The scripts check that the phone is the device the set is for, unlocked and in the bootloader, and
that the files match `SHA256SUMS`.

A US Fairphone 6 reports `FP6_US`, and the scripts accept it as the same phone. They stop at the
first failed command.

They always write the carried firmware, because the FP6 bootloader cannot say which release the
phone runs. They cancel an update the OS left pending, if the bootloader reports one, and write the
boot images.

Then they restart into fastbootd for the logical partitions and return to the bootloader for the
wipe images.

Before fastbootd they write the empty misc image. An unlock leaves an erase request there, which
recovery would run instead of starting fastbootd.

The wipe erases everything on the phone. It flashes empty userdata, metadata and misc images and the
stock FRP image. Together with the firmware, it also writes the stock empty `logfs` and `vm-persist`
images.

### Printed flash steps

`flash-steps` prints the fastboot commands instead of running them. It works only for a set whose
files match `SHA256SUMS` and whose verify report shows that every check passed.

```sh
fastboot getvar all > phone.txt 2>&1
fastboot oem device-info >> phone.txt 2>&1
tools/diamaneos/bin/diamaneos flash-steps --wipe --phone phone.txt --phone-firmware <BUILD_NUMBER>
```

The FP6 bootloader does not report its firmware, so `--phone-firmware` names it: the build number in
Settings > About phone on stock Android. Firmware steps also need the phone's saved bootloader state
(`--phone`).

`flash-steps` writes the firmware first when the phone runs older firmware, prints none when it runs
the same release, and stops when it runs a newer one. `--rewrite-firmware` writes the same release
again, and `--no-firmware` leaves the firmware alone.

**Updating** a phone that runs a DiamaneOS test build, keeping its data:

```sh
tools/diamaneos/bin/diamaneos flash-steps --since ~/diamaneos-build/images/<previous build>
```

This prints only the images that changed, and the firmware only when the new set carries newer
firmware (then add `--phone`). The earlier set tells which firmware the phone runs. If it carried
none, add `--phone-firmware`.

The logical partitions are always written in fastbootd: `fastboot wipe-super super_empty.img`, then
every logical image, also when only one changed.

### Locking

`flash-all.sh lock` (`flash-all.bat lock`) and `flash-steps --lock` lock a test build anyway, after
a warning and a typed `yes`. Lock one only for a test, then unlock again or install a build signed
with your own keys.

For a set signed with your own keys, `flash-steps` ends with the lock without asking, and
`flash-all` installs the key and stops in the bootloader for it.

The steps follow the stock relock order, tested on the FP6:

1. `fastboot flash avb_custom_key avb_pkmd.bin` (`flash-all` does this itself).
2. `fastboot flashing get_unlock_ability` must report 1. Otherwise stop.
3. `fastboot flashing lock_critical`, and confirm on the phone. It wipes the phone and restarts. Hold
   Volume down while it restarts, so that it comes back to the bootloader. Android must not start
   before the second lock.
4. `fastboot flashing get_unlock_ability` must report 1 again. Otherwise stop.
5. `fastboot flashing lock`, and confirm on the phone.

`flash-steps` prints the lock only when the verify report shows that `avb_pkmd.bin` signs the set
(checks `record` and `avb-chain`).

A test build keeps rollback index 0, so if the phone ran a locked build before, the steps first ask
for `fastboot oem reset-rollback`. Keep OEM unlocking on in the setup. A set signed with
qualification keys is never locked.

### Back to stock

Follow [returning to the stock OS](https://diamaneos.de/install/cli/#returning-to-the-stock-os) in
the install guide, and relock only with the stock OS installed. After a locked DiamaneOS boot, run
`fastboot oem reset-rollback` while unlocked before relocking stock
([rollback rules](FIRMWARE.md#rollback-rules)).

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `this host cannot run the build` | It lists everything that is missing. Fix those and run the command again. |
| `missing Python modules ... install python3-jsonschema` | Install the package it names, for the Python it names. |
| `repo implementation commit does not match the pin` | An official build needs the pinned `repo` tool: run `repo init --repo-rev=<tag>` with the `repo_tool` release tag of `config/build-environment-fp6.json`, then `repo sync` and the command again. Other builds only warn. |
| `is a source checkout` or `is inside the source checkout` | `--workspace` names the checkout or a directory in it. Leave the option out, or name a directory outside the checkout. |
| `name the device` or `unknown device` | Add the device codename the error lists, as in `build vendor FP6`. |
| `out/ holds no complete <codename> build` or `out/ lacks ...` | After `lunch`, run `m droid target-files-package otatools-package`, then `build package` again. |
| `the target-files archive is stale` | `m` installed files after it made the archive, for example after a build without `droid`. Remove the directory the error names, run `m droid target-files-package otatools-package`, then `build package` again. |
| `the source changed since "diamaneos build vendor <codename>" checked it` | Run `build vendor` and `m` again, then `build package`. |
| `the build cannot compile with network access off` | It says why: user namespaces turned off, or util-linux older than 2.38. Fix that if you can. Otherwise add `--allow-network`, and `build.json` records that the build had network access. |
| `these tools ... do not contain the source's tools/diamaneos commit` | The tools are older than the source. Run the copy it names (`tools/diamaneos/bin/diamaneos` in the checkout). |
| A signature or hash does not match | Do not work around it. Check your network, then report it: the download is not what the tools pin. |
| `not our ref` during sync | A commit the manifest selects is not on the server. Run the command again once it is there. |
| `holds local manifests the build does not use` | An official build uses the manifest alone: move the files in `.repo/local_manifests` away. |
| `contains dirty or untracked content` | An official build, or the manifest checkout, has local changes. Undo them or move them aside. |
| `undeclared inputs outside its projects` | An official build found files between the projects. Move them away. |
| `policy files differ from their record` | A device policy file changed without its row in `sepolicy/provenance.json`. Update the row with the reviewed change. |
| `bytes of body are still expected` during sync | A long download broke off. Run the same command again. Finished repositories are not downloaded again. |
| `... is out of date. Run "diamaneos build ..."` | A step you ran on its own needs an earlier step first. Run the step it names, or `build all`. |
| The disk fills up | Free the space the host check asked for and run the command again. |
| A build step fails | The error names the log in `~/diamaneos-build/logs/`. Fix the cause and run the command again. Finished steps are skipped. |
| `checks failed` at the end | The report next to the image directory (`<build_id>.verify.json`) names each failed check and why it exists. Fix the cause and run the command again. |
| `the verification report belongs to another image set` | Run `tools/diamaneos/bin/diamaneos build verify` again for this set. |
| `flash-all` waits for fastbootd or the bootloader | Hold the device's bootloader keys (Power and Volume down on the FP6) until the phone restarts into the bootloader, then run the script again. |

## Pinned build environment

The FP6 build environment, [`config/build-environment-fp6.json`](../config/build-environment-fp6.json),
names everything a build starts from:

- `manifest`: the manifest URL, its branch and an optional `revision` that pins one commit.
- `upstream`: the GrapheneOS release the manifest is based on, with its tag, tagged manifest and signer,
  and the `repo` tool's release tag and commit (`repo_tool`).
- `host`: the tested system, the minimum RAM and disk, and the tested package versions.
- `project_inputs`: the tools' own input files, whose hashes `build.json` records.

`environment_id` names an environment, not an OS release. The file's hash is part of every build
identity, so any edit to it changes the build number.

The distribution's `repo` package is only the launcher. The implementation it runs updates itself, so
the environment pins it to a signed tag and commit instead of its moving `stable` branch.

`build preflight` checks an environment without building:

```sh
tools/diamaneos/bin/diamaneos build preflight --inputs-only
```

`--inputs-only` needs no host or source tree. It validates the environment's input records and prints
its declared build identity. `--host-only` and the full check also need `--thermal-check` with an
executable that confirms the host's cooling is safe. `--config` selects another environment file.

The full check also covers the gaps between Git projects: undeclared files or symlinked directories
cannot supply build inputs. Manifest `copyfile` and `linkfile` targets must match the resolved
manifest. No environment permits local manifests.
