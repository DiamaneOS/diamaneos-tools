# Build reference

To build DiamaneOS, follow [BUILDING.md](BUILDING.md). This page covers host setup, how the build
works and why (the build commands and their records, the manifest and the build environment, the
generated inputs and packaging), flashing and troubleshooting. To run the tool tests, see
[TESTING.md](TESTING.md).

The host tools are Python 3 programs and do not require an Android source checkout or compilation.
Schema conformance tests use the pinned development dependencies in `requirements-dev.txt`.

## Host setup

[BUILDING.md](BUILDING.md#requirements) lists the hardware and the packages.

- `repo` is in Debian's `contrib` section: add `contrib` to the `Components:` line in
  `/etc/apt/sources.list.d/debian.sources` (or to each `deb` line in `/etc/apt/sources.list`) and
  run `sudo apt update` before the install.
- Without `contrib`, leave `repo` out and install the `repo` launcher as its [install
  instructions](https://gerrit.googlesource.com/git-repo#install) describe.
- The package install is the only step that needs `sudo`; everything else runs as your own user.
- The build checks the exact free disk space it needs before it starts.
- Compilation runs with network access off, in an unprivileged user namespace.
- Debian allows them by default.
- Some systems restrict them (Ubuntu 24.04 does, through AppArmor); the build then stops and says
  why.
- Flashing needs the phone, a USB cable and `fastboot` from the Android platform tools, on any
  computer.

## Source checkout

- **Workspace.**
  - The tools keep everything in one workspace directory, `~/diamaneos-build` by default, with the
    source in its `src` directory.
  - For another place, run `repo init` in `DIR/src` and pass `--workspace DIR` to every `diamaneos`
    command (or set `DIAMANEOS_WORKSPACE`).
- **Tools.**
  - Run the tools from the source tree (`tools/diamaneos`).
  - Another tools checkout must contain the commit the sync checked out there ([tools and
    source](#the-build-commands)).
- **Smaller download.**
  - Add `--depth=1` to `repo init`, use `repo sync -c --no-tags -j8`, and pass `--shallow` to the
    first `build sync` or `build all`.
  - The checkout has no history, which saves roughly half of the download and of the disk space.
  - Moving to a newer GrapheneOS release later downloads more.
  - The workspace remembers the choice ([shallow sync](#the-build-commands)).
- **Moved projects.**
  - A newer release can take a project from another repository at the same path (GrapheneOS forking
    an AOSP project).
  - `diamaneos build` removes the clean old checkout before syncing.
  - A plain `repo sync` needs `--force-sync <path>` for it.
- **Manifest signatures.**
  - The manifest commits are signed with SSH keys.
  - No allowed-signers file is published; with one that lists the keys:

  ```sh
  git -C .repo/manifests -c gpg.ssh.allowedSignersFile=/path/to/diamaneos-allowed-signers \
    verify-commit HEAD
  ```

## The build commands

- `diamaneos build all` runs five steps in one workspace directory (`~/diamaneos-build`, or
  `--workspace`, or `DIAMANEOS_WORKSPACE`); the source is its `src` directory.
- Each step can also run on its own.
- It then always runs again, and it refuses to start while an earlier step it consumes has not run
  or is out of date (`build vendor` and `build package` check or record those steps themselves:
  [building by hand](#building-by-hand)).

- The build environment
  ([`config/build-environment-fp6.json`](../config/build-environment-fp6.json)) names the source:
  the DiamaneOS manifest (`https://github.com/DiamaneOS/platform_manifest.git`) on its `android17`
  branch.
- That branch selects every project: GrapheneOS and AOSP projects at the exact commits of the
  GrapheneOS release it is based on, Fairphone and CodeLinaro projects at exact commits, the
  DiamaneOS forks and projects at their `android17` branches, the kernel prebuilts and the tools.
- Because the branch moves, `build all` starts with `sync` every time; an environment that pins one
  manifest commit (`manifest.revision`) does not.

| Step | What it does | What it checks and records |
| --- | --- | --- |
| `sync` | Moves a generated tree where the manifest has a project, and an old local manifest the tools installed, out of the way; runs `repo init` on the manifest branch with the pinned `repo` tool, then `repo sync`. | The `repo` tool's tag signature and commit, that `.repo/manifests` is the declared manifest at the head of the branch (and at `manifest.revision` when pinned; at the recorded manifest commit when reproducing a build), then the full source preflight: every project at its resolved commit and clean (local changes only outside official builds, see below), no local manifests, no undeclared files. Records the resolved manifest (`state/resolved-manifest.xml`), its SHA-256, the project map, the manifest commit, the kernel prebuilts commit and, when reproducing a build, what it reproduced (`pinned_manifest`). |
| `vendor` | Builds `aapt2`, `simg2img`, `lpunpack` and `debugfs_static` from the synced source (generic lunch target, network off), downloads the Fairphone factory package from its official host, then `vendor stage`, `vendor extract` and `vendor product`, and installs the tree at `vendor/fairphone/FP6` (`build inputs`), so a plain `m` can follow. On its own, `build vendor` first checks the existing checkout as `sync` does after its `repo sync`, runs no `repo` command and records it as the sync; after a sync that reproduced a resolved manifest it keeps that record. | The package's size and SHA-256, each staged image and each extracted file against the recipes. The image tools are accepted because they come from the synced source; their hashes are recorded in the extraction identity. |
| `android` | Installs (or re-checks) the generated vendor tree, then `lunch FP6-cur-<variant>` and `m` with network off. The kernel comes from the kernel prebuilts project (`device/fairphone/FP6-kernel`). | The full preflight before and after the build, including the generated-input descriptor; the tree must be the one `sync` recorded. Records the target-files archive and the build identity. |
| `package` | Exports the partition images from the target-files archive, copies the stock firmware out of the factory package, builds `super.img` from the same archive, makes the wipe and modem file system reset images, copies the resolved manifest and writes `build.json` and `SHA256SUMS`. | The target-files hash; the factory package and each firmware image against the firmware inventory; the wipe images against the device fstab, the stock FRP image and the stock partition table. |
| `verify` | Checks the exported set. | See below. Writes `<build>.verify.json` next to the image directory. |

**Using `build all`.**

- The first build takes many hours. Each long command prints a `live output` file to follow with
  `tail -f`.
- After a stop, run the same command again: finished steps are skipped, and later steps run again
  only if the synced source changed.
- `--dry-run` shows what is left without changing anything; `--from vendor` rebuilds without syncing
  again.
- `user` is the default variant; `--variant userdebug` builds a test image with adb and root
  debugging.
- Give `build all` the same `--variant` each time: another one rebuilds Android.
- A single step, such as `build verify`, uses the variant Android was built with.
- At the end it prints the image directory, for example
  `~/diamaneos-build/images/20261003-user-3f9a1c2b7d`.
- It holds the images, `build.json` (exactly what the build was made from: the manifest commit, the
  kernel prebuilts commit, the vendor inputs and more), `resolved-manifest.xml` with every project's
  commit, and `SHA256SUMS`.
- To build by hand instead, see [building by hand](#building-by-hand).

- **State and resume.**
  - Each step writes `state/<step>.json` with the digest of its inputs (the hashes of the configs,
    and only the parts of `config/fp6-build.json`, it reads, its code, and the outputs of earlier
    steps), its outputs and its log.
  - `build all` skips a step whose input digest is unchanged and whose outputs still verify.
  - A later step runs again only when an earlier step's outputs changed.
  - Each step prints its log path when it starts, and each action a short title.
  - The log and `--dry-run` have the full description and command.
  - A second command on the same workspace fails at once (`.workspace.lock`).
  - `--dry-run` prints every command and changes nothing.
  - Any failure is recorded in the step's state with a plain message.

**Tools and source.** The vendor selection and the image checks must match the device tree, so the
tools must match the source. Before the first step after `sync`, the command compares its own commit
with the commit the sync checked out at `tools/diamaneos`:

- The same commit, or a source without that project: nothing to say.
- Tools that contain that commit (a newer checkout): a note; `build.json` records the tools commit.
- Tools that do not contain it (an older or diverged checkout): the command stops and names the
  synced tools to run instead.
- The sync moved the very checkout the command runs from: the command starts again with the new
  tools, keeping the sync that just passed, as `repo` does after updating itself.

**Local changes.** A build that is not official accepts projects with local changes (edited, added
or deleted files):

- `sync` and the `android` step print a warning that names them.
- The sync state and `build.json` list them (`modified`).
- A digest of the changed files joins the source identity, so a new edit rebuilds Android and gets
  its own image directory.
- `build.json` marks the set not reproducible (`reproducible: false`).

- `--official` refuses them.
- Every build still fails on a project the manifest pins to a commit at another commit, a changed
  `.repo/manifests`, local manifests and files outside the projects.
- With an environment that pins one manifest commit, `build all` keeps its sync; after an edit, run
  `build all --from sync`.

- **Official builds.**
  - DiamaneOS's own builder adds `--official`: the image then includes the Updater, which checks
    DiamaneOS's update server (`releases.diamaneos.de`) for updates.
  - Leave it off for your own builds.
  - An official build signed with the public test keys, as every build of these commands is, checks
    for updates but downloads and installs none.
  - `build all --official` (or `build android --official`) gives the Android build
    `DIAMANEOS_OFFICIAL_BUILD=true`, which `vendor/diamaneos/product.mk` reads to add the Updater;
    any other value stops the build.
  - Every other build runs with the variable removed from the environment, as GrapheneOS's
    `OFFICIAL_BUILD` always is.
  - The choice is an Android input, part of the build identity, and recorded as `official` in
    `build.json`.
  - The workspace remembers it (`state/official`) until `--no-official`.
  - The image checks marked `official` require the Updater, its permissions and its server URL in
    official builds, and its absence in the others.

- **Shallow sync.**
  - `build sync --shallow` passes `--depth=1` to `repo init` and `-c --no-tags` to `repo sync`, so
    only the resolved commits are fetched.
  - The workspace remembers the choice (`state/shallow`), so a later `build all` stays shallow.
  - The tree and its preflight are the same, so the choice is not part of the digest.
  - Before `repo sync`, a shallow sync does two more things:

- It removes project git directories with no refs and no shallow file, and their empty object
  directories and work trees.
- An interrupted sync leaves them, and `repo` would fetch such a project with its whole history.
- It fetches the largest prebuilt projects itself, listed in `shallow_prefetch` in
  `config/fp6-build.json`: one revision each (the commit, or the tag the manifest names) at depth 1
  over HTTP/1.1, a few at a time.
- A transfer that stays below the speed limit stops, and a failed fetch is retried a bounded number
  of times.
- The fetch goes into the project's git directory, or into its object directory when `repo` has not
  created the project yet; `repo` then finds the revision and does not fetch the project.
- If a project still fails, the step stops, and running it again starts that fetch over.

- **Building the kernel.**
  - `build kernel` runs `kernel prepare` (network) and `kernel build` (network off) in the
    workspace's `kernel` directory.
  - It is not part of `build all`: the Android build uses the kernel prebuilts the manifest selects.
  - It makes a new kernel set, which `kernel publish` copies into the kernel prebuilts repository
    ([FP6-KERNEL.md](FP6-KERNEL.md)).

- **Host check.**
  - Before running, the command checks the host for the steps it will run:
    - Linux on x86_64.
    - Python 3.11 or newer with `jsonschema` (vendor generation validates its recipes with it).
    - The commands each step uses (`modinfo` and `modprobe` are also looked up in `/usr/sbin` and
      `/sbin`).
    - RAM (the 32 GiB floor allows 2 GiB for what firmware and the kernel reserve).
    - Disk (only for steps whose output does not exist yet).
    - A case-sensitive filesystem.
    - For compile steps, unprivileged user namespaces with util-linux 2.38 or newer.
  - Differences from the environment's pinned package versions are recorded, not fatal.

- **Network.**
  - Only `repo`, `git fetch`, the kernel preparation and the factory package download use the
    network.
  - Downloads use HTTPS only, stop at the expected size, and refuse redirects to another host or to
    plain HTTP.
  - Compilation (the kernel, the image tools and Android) runs inside `unshare --user
    --map-current-user --net`, so the build keeps its own user id and has no network, and compile
    commands lose variables that lead to local agents and buses (`SSH_AUTH_SOCK`, `DBUS_*`,
    `XDG_RUNTIME_DIR`, `DOCKER_HOST` and similar).
  - Unix sockets in the filesystem stay reachable.
  - If the host has no unprivileged user namespaces the command stops.
  - `--allow-network` builds anyway and records `network_isolation: off` for the kernel, vendor and
    Android steps.
  - If `DIAMANEOS_THERMAL_CHECK` names a program, it runs before every compile.
  - Temporary files go to the workspace (`TMPDIR`), never to `/tmp`.

- **Build identity.**
  - The source identity is a digest of the environment file, the source project map, the resolved
    manifest's SHA-256, the manifest commit, the kernel prebuilts commit, the vendor inventory, the
    variant and the build parts of `config/fp6-build.json`.
  - `BUILD_NUMBER` is `test.` and its first 12 digits.
  - `BUILD_DATETIME` is the newest committer time among the synced projects and the manifest commit
    (the build stops if it cannot read them).
  - `BUILD_USERNAME` and `BUILD_HOSTNAME` are fixed.
  - The image set's build identity adds the build number, the network isolation, the tools commit
    and the target-files hash.
  - The image directory is `<date>-<variant>-<build identity>`, and an existing directory is reused
    only when its record names the same target-files and identity.

- **Generated-input descriptor.**
  - `build inputs` (called by the `vendor` and `android` steps) writes
    `.repo/diamaneos-generated-inputs.json`.
  - It binds `vendor/fairphone/FP6` to the environment file's hash, the recipe digests the vendor
    generation recorded in its provenance and its complete inventory.
  - Installation and the full preflight require those digests to equal this checkout's recipes, and
    accept the directory only while every file matches its inventory.
  - Any other file outside the projects still fails the preflight.
  - A tree that no longer matches is replaced explicitly (the old one moves to
    `.repo/diamaneos-previous-inputs/`).
  - `sync` moves stale trees aside, and also a generated tree at a path where the manifest has a
    project, such as the kernel prebuilts at `device/fairphone/FP6-kernel`.

- **Packaging.**
  - The target-files archive is the image authority: its `IMAGES/` were made together by the build,
    so the AVB descriptors match them, and `super.img` is built from the same archive.
  - Nothing is repacked.
  - The standalone images in the product output directory can differ, because the release tools
    repack images and derive AVB salts separately; the two sets are never mixed.
  - DiamaneOS builds with its own fork of GrapheneOS's `build/make`, which differs in one change:
    - When the Android release tools rebuild boot, init_boot and recovery from target-files, they
      keep the device's zero OS version and patch level in the image headers, as the Make rules do
      (the versions live in the AVB properties).
  - `verify` checks the headers.
  - The target-files archive is also the unsigned input for the offline signer
    ([SIGNING.md](SIGNING.md)).

- **Wipe images.**
  - `userdata.img` is 4 MiB of zeros, as in Fairphone's factory package: it destroys the old
    filesystem and first boot formats `/data` with the phone's own size and settings.
  - `metadata.img` is an empty f2fs filesystem of the partition's size, made with the synced
    source's `make_f2fs` with a fixed UUID, time and seed.
  - `frp.img` equals Fairphone's `frp_for_factory.img`.
  - `misc.img` is zeros of the stock partition table's misc size, which clears misc as Fairphone's
    factory flash does.
  - `build package` checks the size against the factory package's partition table.
  - Nothing is cleared with `fastboot erase`.
  - While `wipe.validated` in `config/fp6-build.json` is false, the printed flash steps say that
    the wipe is not yet tested on a phone.

- **Firmware.**
  - The image set carries Fairphone's firmware of the release its vendor files come from, under the
    stock file names, copied from the factory package the vendor step authenticated: the package and
    every image must match `config/fp6-firmware-inventory.json`, and the release must be the vendor
    files'.
  - The stock `pvmfw.img` is left out (DiamaneOS builds its own).
  - `modemst1.img` and `modemst2.img` are zeros of the stock partition sizes, which empty the modem
    file system as Fairphone's flash does.
  - `build.json` records the release, the images and the flash steps in the stock order
    ([FIRMWARE.md](FIRMWARE.md)).

- **Verify.**
  - Generic checks: `SHA256SUMS`.
  - The record (a test build that must never be locked, matching hashes).
  - test-keys in every fingerprint.
  - The AVB chain with the test key and the published layout (recovery 1, vbmeta_system 2, boot 3,
    init_boot 4, flags 0, pvmfw in vbmeta_system).
  - Zero OS fields in the boot-family headers with the versions in AVB properties.
  - `super.img` holding exactly the logical images.
  - `validate_target_files` and `check_target_files_vintf`.
  - The kernel prebuilts in boot, vendor_boot and dtbo (the `device/fairphone/FP6-kernel` checkout,
    which must be at the commit `build.json` names and unchanged).
  - Module placement and load lists against those prebuilts, no denied or unsigned module, and the
    device's parallel load streams (`modules.load.*` in vendor_dlkm) naming every vendor_dlkm
    `modules.load` module exactly once, each stream in that list's order.
  - Every selected stock file arriving with its generated bytes.
  - The vendor patch level in target-files and `vendor.img` equal to the one the vendor generation
    read from the stock vendor image.
  - No permissive domain beyond the variant's.
  - The bootconfig.
  - No kernel argument that turns a protection off, in any device tree, overlay, boot image header or
    bootconfig entry (`kernel_arguments` in `config/fp6-build.json`).
  - No pre-trusted adb key.
  - The wipe images.
  - The firmware (every image of the release byte for byte as the inventory pins it, the stock
    order, the reset images, no lower anti-rollback version).
  - And an update partition list in target-files that holds every A/B firmware partition with
    stock bytes and no other firmware partition.
  - Then every rule in `config/fp6-image-checks.json`: the per-build device checks, each with the
    reason it exists and, where it matters, the variants it applies to and whether it checks
    official builds or the others.
  - The report carries the image set's `SHA256SUMS` digest.

- **Flash steps.**
  - `diamaneos flash-steps` prints commands only for a test build whose set matches its `SHA256SUMS`
    and whose verify report belongs to that set and passed.
  - The flash order, slot and wipe images come from `config/fp6-build.json`, the firmware steps from
    `build.json`: first, in the stock order, both slots of each A/B firmware partition, then the
    modem file system reset, and only when the phone runs older firmware than the set carries.
  - The phone's firmware release comes from `--phone-firmware` or the `--since` image set, because
    the FP6 bootloader does not report it.
  - Firmware steps also need the phone's saved bootloader state (`--phone`), and a newer release on
    the phone stops the command.
  - The fastbootd fallback writes every logical partition, because a stalled `super` flash may
    already have written the new layout.

- **Reproducibility.**
  - The same resolved manifest gives the same source map, generated inputs and source identity, and
    every image set carries its resolved manifest.
  - The kernel, its modules and device trees come prebuilt from the manifest, so they are the same
    on every host.
  - A kernel rebuilt from source matches the prebuilts except for the module signatures and the
    certificate the kernel embeds, because each kernel build makes a new module-signing key.
  - The same inputs are meant to give identical partitions on any host; no command here compares
    two hosts' builds.
  - `build.json` names the tools commit that built the Android images and the one that packaged
    them.
  - A modified tools checkout, or packaging by another commit than the build, is recorded as not
    reproducible.

- **Reproducing a build.**
  - `build sync --resolved-manifest DIR/resolved-manifest.xml --build-json DIR/build.json` syncs the
    source of the image set in `DIR` (`build all` takes the same options).
  - After the normal `repo init` and its checks, the sync checks out the manifest commit
    `build.json` records, then `repo sync -m` checks out every project at the commit the resolved
    manifest names.
  - Its checks:

- The resolved manifest must match the SHA-256 `build.json` records, and `build.json` must name the
  environment's manifest URL and branch.
- Without `build.json`, `--manifest-commit` gives the manifest commit.
- The manifest commit must be in the history of the manifest branch.
- It is never fetched by its id; a shallow manifest checkout first gets the branch's history.
- Before `repo sync`, the resolved manifest must equal that commit's manifest apart from the project
  commits: the same remotes, defaults, projects, groups and copy and link files.
- It can only choose commits.
- The check that the manifest checkout is at the head of the branch does not apply.
- The sync and the `android` step say so and check the recorded commit and the branch history
  instead.
- After `repo sync`, `repo manifest -r` must give the resolved manifest byte for byte, and the
  project map and kernel prebuilts commit must match `build.json`.

- The sync state records the reproduction (`pinned_manifest`, which the new set's `build.json`
  carries in `source`).
- A later `build all` keeps that source; `build sync` moves to the branch head again.
- An official build's `build.json` says so (`official`), and the sync notes it: reproduce it with
  `--official`.
- A different tools commit gives the same source identity, but another build identity and image
  directory name, because the build identity includes the tools commit.

**Other environments.** `--environment FILE` selects another environment file: a copy of the FP6
environment with another manifest branch, or with `manifest.revision` set to build one manifest
commit. `DIAMANEOS_BUILD_NUMBER` overrides the build number.

### Building by hand

The plain Android flow of [BUILDING.md](BUILDING.md): `repo sync`, `build vendor`, `lunch` and `m`,
then `build package`.

- `build vendor` on its own checks the checkout as `sync` does after its `repo sync`, runs no `repo`
  command and records it as the sync.
- The checkout must use the pinned `repo` tool: `repo init --repo-rev=<tag>` with the environment's
  `repo_tool` release tag, as in [BUILDING.md](BUILDING.md#1-get-the-source).
- `m target-files-package otatools-package` builds the images, the target-files archive and the host
  tools that packaging and the checks use; a plain `m` builds the images only.
- `build package` on its own, when the Android step has no current record of its own, checks the
  checkout again and records the build in `out/` as the Android step, with no `m` and no
  `installclean`.
- Then it packages and checks the set (`package`, then `verify`).
- It stops, with one message, when:
  - `out/` lacks the target-files archive or a host tool;
  - `out/` holds another variant than `--variant` asks for (without `--variant`, the variant comes
    from `out/`);
  - the source changed since `build vendor` checked it, or holds another vendor tree;
  - the images do not carry the vendor generation's files byte for byte (the `vendor-binding` check
    of `verify`);
  - the workspace builds official images, which come only from `build all`.
- Times cannot prove what `m` built: `out/` older than the last `build vendor` or a local change is
  a note, not a stop.
- `build.json` marks the set `android_build: manual` (`tools` for builds the tools ran), with the
  build number and date from its `build.prop`, `network_isolation: off`, no tools commit and
  `reproducible: false`.
- `build all` never reuses a manual record; it builds Android itself.

## Flashing

For test builds only. The rules:

- Never lock the bootloader with a test build installed: never run `fastboot flashing lock` or
  `fastboot flashing lock_critical`.
- Anyone can sign images with the public test keys.
- Never run `fastboot -w` or `fastboot erase`.
- When a wipe is needed, the printed steps flash empty images instead, as Fairphone's own factory
  package does.
- The OS goes to slot a only. The firmware steps write both slots of each A/B firmware partition, as
  Fairphone's own flash does.
- Never install firmware older than the phone already runs; `flash-steps` refuses to.
- To flash from another computer, copy the whole image directory there.

**Firmware.** Each image set carries Fairphone's firmware of the release its vendor files come from,
named in the set's `build.json`.

- `flash-steps` writes it first when the phone runs older firmware, prints no firmware steps when
  the phone runs that release, and stops when it runs a newer one.
- The FP6 bootloader does not report its firmware, so name it with `--phone-firmware`: the build
  number in Settings > About phone on stock Android, or the firmware the last DiamaneOS flash steps
  installed (`--since` reads it from that image set).
- Firmware steps also need the phone's state, saved in the bootloader and passed with `--phone`: `{
  fastboot getvar all; fastboot oem device-info; } > phone.txt 2>&1`.
- `--no-firmware` leaves the firmware as it is.

**First install** (from stock Android or another system):

1. Note the build number in Settings > About phone, for example `FP6.QREL.16.100.0`.
2. Unlock the bootloader as [Fairphone
   describes](https://support.fairphone.com/hc/en-us/articles/10492476238865-How-to-unlock-or-lock-your-Fairphone-s-bootloader),
   including `fastboot flashing unlock_critical`, because the steps write firmware.
3. In the bootloader, save the phone's state as above, then print the flash steps and run them:
   `tools/diamaneos/bin/diamaneos flash-steps --wipe --phone phone.txt --phone-firmware
   <BUILD_NUMBER>`.

- The wipe erases everything on the phone.
- It flashes empty userdata, metadata and misc images and Fairphone's factory FRP image, so factory
  reset protection is cleared and OEM unlocking stays allowed; unless you pass `--no-firmware`, it
  also writes Fairphone's empty logfs and vm-persist images.
- The printed steps say whether the wipe is tested on a phone ([wipe images](#the-build-commands)).

- **Updating** a phone that already runs a DiamaneOS test build: `tools/diamaneos/bin/diamaneos
  flash-steps --since ~/diamaneos-build/images/<previous build>`.
- It prints only the images that changed, and the firmware only when the new set carries newer
  firmware than the earlier one (then add `--phone`).
- An earlier image set without firmware does not say what the phone runs; add `--phone-firmware`.
- Without `--since` it prints every image.

**Back to stock:** install Fairphone's factory package with [Fairphone's
instructions](https://support.fairphone.com/hc/en-us/articles/18896094650513-How-to-manually-install-Android-on-your-Fairphone).
Relock only after stock is back, and only with the checks in the installer's [recovery
preflight](https://github.com/DiamaneOS/installer/blob/main/docs/recovery-preflight.md).

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `this host cannot run the build` | It lists everything that is missing. Fix those and run the command again. |
| `missing Python modules ... install python3-jsonschema` | Install the package named, for the Python the error names. |
| `repo implementation commit does not match the pin` | The checkout uses another `repo` tool: run `repo init --repo-rev=<tag>` with the `repo_tool` release tag of `config/build-environment-fp6.json`, then `repo sync` and the command again. |
| `out/ holds no complete FP6 build` or `out/ lacks ...` | After `lunch`, run `m target-files-package otatools-package`, then `build package` again. |
| `the source changed since "diamaneos build vendor" checked it` | Run `build vendor` and `m` again, then `build package`. |
| `the build cannot compile with network access off` | It says why: user namespaces turned off, or util-linux older than 2.38. Fix that if you can; otherwise add `--allow-network`, and `build.json` records that the build had network access. |
| `these tools ... do not contain the source's tools/diamaneos commit` | The tools are older than the source. Run the copy it names (`src/tools/diamaneos/bin/diamaneos`), or update your tools checkout. |
| A signature or hash does not match | Do not work around it. Check your network, then report it: the download is not what the tools pin. |
| `not our ref` during sync | A commit the manifest selects is not published yet. Run the command again after the push. |
| `holds local manifests the build does not use` | Move the files in `.repo/local_manifests` away; the build uses the manifest alone. |
| `contains dirty or untracked content` | An official build, or the manifest checkout (`.repo/manifests`), has local changes. Undo them (or move them aside) and run the command again; other builds only warn about changed projects. |
| `undeclared input` | Files were added between projects. Move them away and run the command again. |
| `bytes of body are still expected` during sync | A long download broke off. Run the same command again; sync already uses HTTP/1.1 and retries, and finished repositories are not downloaded again. |
| `... is out of date; run "diamaneos build ..."` | A step you ran on its own needs an earlier step to run again first. Run the step it names, or `build all`. |
| The disk fills up | Free the space the host check asked for and run the command again. |
| A build step fails | The error names the log in `~/diamaneos-build/logs/`. Run the command again after fixing the cause; finished steps are skipped. |
| `checks failed` at the end | The report next to the image directory (`<build>.verify.json`) names each failed check and why it exists. Do not flash that build. |
| `the verification report belongs to another image set` | Run `tools/diamaneos/bin/diamaneos build verify` again for this set. |
| Flashing `super` stops after its first part | Use the fastbootd steps printed under the main steps. |

## Pinned Android environment

- The FP6 environment is
  [`config/build-environment-fp6.json`](../config/build-environment-fp6.json): its `manifest` (URL,
  branch and an optional `revision`), its device and generated-input recipe pins, and an `upstream`
  record that names the GrapheneOS release the manifest is based on and pins the `repo` tool.
- `build preflight` uses the FP6 environment by default; `--config` selects an explicit
  environment file.
- `environment_id` names an environment, not an OS release.
- An environment file's hash is part of every build identity, so any edit to it changes the build
  number.
- The schema version changes only when a file's structure or meaning changes.

- An `upstream` record binds the GrapheneOS tag, tag object, peeled manifest commit, official
  signer-list hash, signer identity, tagged `default.xml`, canonical project commit map and the
  GPG-verified `repo` tag object and commit.
- The file also binds host packages, external tools and project and device input hashes.
- The distribution's `repo` package is only the launcher; the self-updating implementation is a
  separate input, pinned to a tag and commit (`upstream.repo_tool`) instead of its moving `stable`
  branch.
- `release_scope` records the targets GrapheneOS published the release for.
- A branch name, a GitHub verification badge or an existing download cache is not a substitute for
  the local signature checks.

The project-selected Debian 13 host is newer than the operating systems listed by the upstream build
guide. This is a declared compatibility deviation.

- Full preflight also verifies the gaps between Git projects: undeclared files or symlinked source
  directories cannot supply optional Make includes.
- Manifest `copyfile` contents and `linkfile` targets must match the manifest (the signed release
  for the generic environment, the resolved manifest for FP6), and the manifest checkout itself must
  be at that release commit or at the head of the declared branch.
- The declared output container and `.repo` metadata are outside this source-layout traversal;
  individual project content is still checked separately with Git.
- No environment permits local manifests.

The inputs-only check needs no source tree:

```sh
bin/diamaneos build preflight --inputs-only
```

- It validates the committed input records of the environment (`--config`, by default the generic
  one) and prints its declared build identity.
- Changing a tag, project-map pin, tool record or stock input changes that identity.
- It lists what a full preflight still needs, with the environment's
  `generated_input_manifest_status`.

The FP6 preflight also requires the generated-input descriptor described under [the build
commands](#the-build-commands).

## The DiamaneOS manifest

- [`platform_manifest`](https://github.com/DiamaneOS/platform_manifest) is a full `repo` manifest:
  - GrapheneOS's `default.xml` at the release it is based on, with the DiamaneOS forks in place of
    the projects they fork, the Fairphone and CodeLinaro projects the FP6 needs, the kernels of
    other devices left out, and two DiamaneOS projects:
  - The kernel prebuilts
    ([`device_fairphone_FP6-kernels`](https://github.com/DiamaneOS/device_fairphone_FP6-kernels) at
    `device/fairphone/FP6-kernel`, fetched with `clone-depth="1"`) and these tools
    (`tools/diamaneos`, branch `main`).

- GrapheneOS and AOSP projects keep the exact revisions of GrapheneOS's signed manifest.
- Builders trust the DiamaneOS manifest branch; they do not check GrapheneOS's tag themselves.
- GrapheneOS's signed release tag is verified when a GrapheneOS release is merged into the
  manifest, and the merge commit records it.
- Every build records the resolved manifest, so the exact commit of each project is known
  afterwards.

## Upstream tracking

- [`config/forks.json`](../config/forks.json) lists the upstreams used by builds and server
  deployments.
- `forks` are the repositories DiamaneOS forks and patches: the forks the DiamaneOS manifest
  selects, the common kernel (`kernel_common-6.1`), and the server forks.
- A fork's branch is its pin; `upstream` is the reference it follows.
- To add a fork, add its entry here and select the fork in the DiamaneOS manifest.
- Server entries have `scope: "server"`, branch `main`, and an `upstream_revision` recording the
  adopted upstream commit.
- Checks compare it with the upstream branch head without needing a clone.
- Update that field only when adopting reviewed source; deployment is separate.
- Server forks are not selected by the Android manifest or release-tag rebase.
- GrapheneOS forks follow `@release`: the release tag in `config/build-environment-fp6.json`, which
  the registry's `release` entry names. A new release is one edit there.
- A fork exists only where DiamaneOS changes the code.
- A fork that stops carrying a needed change leaves the registry, and the manifest takes the
  upstream project unmodified.
- Once such a fork is deleted, builds whose manifests pinned it can no longer be synced from GitHub.
- A `follow_note` says what a fork changes and, where the reference alone does not show it, how
  it follows upstream.
- `sources` are pinned inputs that are not forked:
  - The GrapheneOS release, the repo launcher, Fairphone's source manifest, the Qualcomm SELinux
    policy and the stock factory image.
  - The platform repositories the manifest takes straight from CodeLinaro, Fairphone or linux-msm.
  - The LineageOS power HAL projects it takes unmodified through DiamaneOS mirrors.
  - AOSP's nos host libraries.
  - The upstream projects imported into the kernel repository.
- Each names the file and field that hold its pin, so the registry never repeats a revision.
- `newer` patterns name the branches or tags that would supersede a followed reference, such as
  Fairphone's next `odm/rc/target/<android>/fp6` branch or the next CodeLinaro release tag.
- Matches are ordered by the pattern's capture group.
- Fairphone forks and imports follow the public-build branch where one exists, otherwise `rc`.
- CodeLinaro forks and imports follow a release tag, or the newest release branch that contains
  the pinned commit, with its release tags as `newer`.

- The kernel's upstream projects are folders of
  [`kernel_qcom-6.1`](https://github.com/DiamaneOS/kernel_qcom-6.1), except the common kernel
  submodule.
- Each was imported as one commit on its `upstream` branch, which is merged into `android17`, so
  upstream commits are not in the history of `android17`.
- [`config/kernel-upstream-fp6.json`](../config/kernel-upstream-fp6.json) records the imports
  followed one by one (the vendor kernel, the techpack drivers, two device trees and edk2): the
  upstream commit, the newest release tag that commit contains (matching the source's `newer_tags`,
  or `null`) and whether the folder also carries Fairphone's FP6 changes.
- The other imports are followed through Fairphone's source manifest.
- An import is `current` while its followed branch still points at the recorded commit; release tags
  after the recorded release are `newer`.
- To take an update, import the new upstream state on `upstream` and merge it into `android17`.
- Where `fairphone_changes` is true, the import must carry Fairphone's FP6 changes too (Fairphone's
  rebased branch, or the difference between the recorded commit and the folder on `upstream`,
  applied to the new state), or the merge drops them.
- The change that moves `config/kernel-sources-fp6.json` to the merged commit also sets the
  project's `commit` and `release`; `git tag --merged <commit>` in a clone of the upstream project
  lists the releases a commit contains.

```sh
bin/diamaneos forks check           # remote refs only; exit 1 when something moved
bin/diamaneos forks status --fetch  # commits behind and patches carried, per fork
bin/diamaneos forks update <id> --ref <tag> [--rerere DIR]   # our patches on the new tag, as update/<date>-<commit>
```

`update` works like GrapheneOS's own rebases: the new upstream release is the base and our commits
are replayed on it.
- **Our patches** are the commits after the tag or commit the fork is pinned to (for a fork that
  follows a branch: after the newest upstream release tag its branch contains), not after the
  merge-base.
- Upstreams that rewrite their history between releases (GrapheneOS's kernel, rebased onto each LTS
  update) would otherwise make their own older commits look like ours.
- **Merges are dropped** and their commits replayed one by one.
- A merge that resolved a conflict between two topics loses that resolution, which then comes back
  as a conflict in our own files.
- Bring a topic in by rebasing it onto the primary branch and fast-forwarding, so no merge carries a
  resolution.
- **Recorded resolutions:**
  - `--rerere DIR` loads git's rerere records from `DIR/<fork slug>` before the rebase and saves new
    ones back, so a conflict resolved once resolves itself in later rebases, also in a fresh clone.
  - A stop that recorded resolutions fully settle continues.
  - The result lists those files as `rerere_resolved`.
  - Review them like any other resolution.
- **`fixup!` commits fold** into the commit whose title they repeat (`fixup! <title>`, git's
  autosquash), so a feature stays one commit across releases.
- Name follow-up fixes that way.

- `check` downloads no history.
- It reads each remote's branch and tag names and answers "is this commit already ours?" locally,
  without fetching missing objects into partial clones.
- States: `current`, `update-available` (the followed branch moved), `newer-release` (a newer branch
  or tag exists), `pinned-commit` (the entry follows no branch or tag yet), `manual-check` (not a
  Git source) and `error`.
- `status` and `update` work on forks only.
- Adopting any update is a reviewed, signed change: rebase or import, run the tests (and for the
  kernel, the kernel build with its layout checks), build, then push the fork branch or update the
  pinned revision.

## Signing handoff

- The online build host stops at unsigned target-files and otatools.
- Signing roles, target-files inventory validation and the disposable qualification path are defined
  in [`SIGNING.md`](SIGNING.md).
- Never copy a production key or signer token to the build host to make a release command
  convenient.
- Full and incremental OTA generation are signing operations because they sign both the payload and
  package.

## Generated inputs step by step

The `vendor` and `android` steps run the vendor and input commands for you, and the `kernel` step
the kernel commands. They stay available for inspection and for work on a single input.

- These commands reconstruct the selected factory-derived userspace files and source-built kernel,
  modules and device trees.
- The source repositories contain recipes and code; generated payloads live in caller-selected work
  directories.
- No command accesses a phone.

- The host is the one [BUILDING.md](BUILDING.md#requirements) describes.
- The kernel workspace provides its pinned compiler, Bazel, DTC and DT image tools.
- Keep its source and outputs on a filesystem supporting case-sensitive names and symbolic links.
- Budget at least 100 GiB of free space for fresh kernel preparation and image extraction, in
  addition to the full Android checkout/build requirements.

Run commands from the authenticated tools checkout. Set absolute paths:

```sh
TOOLS_ROOT="$PWD"
WORK_ROOT="/absolute/path/to/build-work"
FACTORY_ZIP="/absolute/path/to/factory.zip"
IMAGE_TOOLS="/absolute/path/to/extracted-otatools/bin"
SOURCE_ROOT="/absolute/path/to/android-source"
```

- `FACTORY_ZIP` is the EU factory archive `config/fp6-stock-image-recipe.json` identifies by size
  and SHA-256.
- Obtain that exact archive from the download address recorded in `config/stock-inputs.json`.
- Other regions/builds are not interchangeable.
- `IMAGE_TOOLS` is a `bin` directory holding `simg2img`, `lpunpack` and `debugfs_static` with their
  sibling `lib64` directory: either an otatools package whose programs and libraries
  `config/fp6-image-tools.json` pins, or the synced source's own host output
  (`out/host/linux-x86/bin`, as the `vendor` step uses).
- Pass `--record-tools` to `vendor extract` for the latter: it records their hashes in the
  extraction identity instead of requiring the pins.
- Every extracted file is checked against the selected-file recipe either way.

### Extract and generate the vendor product

Set `AAPT2` to the absolute path of the selected Android SDK build-tools `aapt2`. It decodes the
stock carrier configuration resources; its hash is recorded in the generated provenance. See
[carrier integration](CARRIER-INTEGRATION.md).

```sh
"$TOOLS_ROOT/bin/diamaneos" vendor stage --archive "$FACTORY_ZIP" \
  --output "$WORK_ROOT/stock-images"
"$TOOLS_ROOT/bin/diamaneos" vendor extract \
  --super "$WORK_ROOT/stock-images/current/super.img" \
  --image-tools "$IMAGE_TOOLS" --output "$WORK_ROOT/stock-files" [--record-tools]
"$TOOLS_ROOT/bin/diamaneos" vendor product \
  --inputs "$(realpath "$WORK_ROOT/stock-files/current")" \
  --output "$WORK_ROOT/vendor-product" --notice-kind "$NOTICE_KIND" --aapt2 "$AAPT2" \
  --factory-zip "$FACTORY_ZIP"
```

`NOTICE_KIND` is the Android build-system notice kind for these inputs; the build commands take it
from `notice_kind` in `config/fp6-build.json`.

**Staging** (`vendor stage`):

- It authenticates the factory ZIP and the selected image hashes from
  `config/fp6-stock-image-recipe.json`.
- It takes only system-container, boot/ramdisk, DTBO and AVB images; userdata, persist,
  device-unique provisioning and modem state images are excluded.
- It executes no factory script or phone command.
- The output holds `generations/<recipe-digest>` directories and an atomically selected `current`
  symlink; identical inputs reuse and reverify the generation without changing that pointer.
- Missing or wrong images, unsafe archive members and edited generated contents fail; a failed
  extraction does not replace the previous generation.
- Concurrent publishers use one workspace lock.
- Store the output outside source repositories.

**Extraction** (`vendor extract`):

- It independently authenticates `super.img`, expands the sparse image, unpacks only the logical
  partitions the recipe reads (`vendor_a`, and `system_ext_a` or `product_a` when stock Java
  components are selected), and uses the pinned ext4 reader to dump only declared regular files,
  each from its own partition image.
- It checks each file's size/hash, the alias inode/target (an absolute alias target must stay in its
  own partition) and the notice archives.
- It does not mount a filesystem, run factory scripts or copy device-unique state.
- The extraction contract is specific to these ext4 stock images; it rejects an unexpected
  filesystem or another partition (system, odm) rather than guessing another decoder.
- `stock-files/current` contains the regular files, notice files, symlinks and build property file
  declared in [`config/fp6-minimal/vendor-files.json`](../config/fp6-minimal/vendor-files.json).
- Scratch raw images are removed after extraction.

**Product generation** (`vendor product`):

- `config/fp6-minimal/vendor-files.json` declares the selected stock files and `vendor-elf.json`
  binds their reviewed dependencies.
- The command checks component policy, hashes, dependency edges and activation files, applies the
  reviewed source replacements and configuration derivation, and publishes a content-addressed tree
  through `current`.
- Its complete output inventory is stored in `vendor-product/inventories/<generation>.json`.
- Generated provenance distinguishes original bytes from derived files and retained inputs from
  installed libraries.
- Identical inputs reproduce the same generation; changed or missing inputs fail before replacing
  `current`.

- A selected file may declare `runtime_dependencies`: selected files it loads with `dlopen` rather
  than through `NEEDED`, each with the exact soname and a reviewed reason.
- `vendor-elf.json` mirrors each one as a `selected-stock-runtime` edge.
- Such providers join the component closure and the reachable set and are rendered as `required`
  modules: installed with their consumer, never linked.
- A declaration without its edge, an edge without its declaration, or a soname that differs from the
  provider is rejected.
- The ELF closure alone cannot see these loads: qseecomd opens its secure-world listener libraries
  (`librpmb.so` and others) this way, and without them it exits, the QSEE KeyMint cannot serve vold
  and `/data` never mounts.

- The renderer retains native source-built interface libraries where declared, enables native ELF
  checks and generates init/VINTF packaging with the provider.
- Declared are frozen AIDL and HIDL interfaces, the libraries of the source-built display stack and
  a few AOSP libraries that stock blobs link (such as `libdrm` and `libavservices_minijail`).
- The reverse also exists: stock libraries that source modules link (`SOURCE_LINKED_STOCK`: the
  AudioReach graph services, tuning server and voice UI interface that the source-built PAL and AGM
  link) are rendered under their library name instead of the `fp6_stock_` name, and are selection
  roots.
- A C++ implementation library that only a closed HAL uses stays stock: the stock KeyMint HAL keeps
  its `libkeymaster_messages`, because a class-layout change would pass the ELF checks.
- The same caveat applies where closed stock code links a source-built display library, such as
  `libsdmextension` against the source `libsdm*` libraries and CamX against `libgralloc.qti`; the
  ELF checks cannot catch a layout change there either.
- Derived configuration files keep their original and derived hashes distinct.
- Two stock blobs get one dependency renamed in place, to a device library of the same name length,
  with both hashes pinned:
  - The display colour manager links an old-ABI tinyxml2 copy, and the camera provider links the
    seccomp loader (device `camera/seccomp`), which links libhardware in turn and installs the
    provider's system call filter before its `main()`.
- Source-interface replacements and uninstalled optional libraries are listed in generated
  provenance; retained authenticated inputs are not an installed-artifact inventory.

- **Vendor patch level.**
  - `VENDOR_SECURITY_PATCH` in the generated `BoardConfigVendor.mk`, which the build reports as
    `ro.vendor.build.security_patch`, is the value the stock `vendor/build.prop` sets.
  - The recipe pins that file by size and SHA-256 under `build_properties`.
  - It is read, never installed.
  - Generation requires the property exactly once, as a real `YYYY-MM-DD` date no later than the
    factory package's release date in `config/stock-inputs.json` or today, and records it in the
    generation's `provenance.json` and the vendor step's state.
  - The bound is the release date, not the stock build date: stock builds are made before the patch
    date they carry.

**Selected files alone** (`vendor generate`). It materializes a recipe conforming to
`schemas/vendor-files.schema.json` from extracted partitions, without generating the product:

```sh
bin/diamaneos vendor generate --recipe /absolute/path/to/selected-files.json \
  --inputs /absolute/path/to/extracted-partitions \
  --output /absolute/path/to/generated-vendor
```

- The input directory contains partition directories named `vendor`, `odm`, `system`, `system_ext`
  or `product`.
- Each selected regular file has an exact hash, length, stock origin, component label, inventory
  reference, dependency list, purpose and hash-bound notices.
- The recipe names its stock build, region and factory archive hash, which must match
  `config/fp6-stock-image-recipe.json` (`--stock` selects another).
- The command checks selected bytes, not the origin of an extraction directory or whether a
  declared dependency list is complete.
- Dependencies that are not other selected files, wrong bytes and unknown notices fail without
  replacing the previous `current` generation.
- The generation contains `files/`, content-addressed `notices/` and `manifest.json`.
- Original UID/GID, mode, SELinux label and file capabilities remain image metadata in the manifest;
  host files are ordinary non-executable files, and no privileged ownership is applied.
- Symlink traversal, symlink inputs and special files are rejected.

- Selected-file recipes may also declare `symlinks`.
- The generator authenticates link text through directory descriptors without following any link.
- Each alias must name a selected regular file in the same partition, and its source and output
  targets must correspond.
- Cycles, chains, traversal, cross-partition links and links to writable device state are rejected.
- Alias records and their target text hashes enter the component closure and `symlinks.json`; no
  input symlink is created or followed in the host output tree.

### Prepare sources and build the kernel set

```sh
"$TOOLS_ROOT/bin/diamaneos" kernel prepare \
  --workspace "$WORK_ROOT/fp6-kernel"
"$TOOLS_ROOT/bin/diamaneos" kernel build \
  --workspace "$WORK_ROOT/fp6-kernel" --jobs 16 --timeout 7200
```

- Preparation checks out the kernel repository at the commit pinned in
  [`config/kernel-sources-fp6.json`](../config/kernel-sources-fp6.json), its common-kernel submodule
  at the commit the tree records and the toolchains its `prebuilts.json` lists at their revisions.
- The build runs the non-consolidate GKI and vendor targets, the KMI and ABI checks, the module
  packaging from [`config/fp6-kernel-packaging.json`](../config/fp6-kernel-packaging.json) and the
  configuration policy.
- [FP6-KERNEL.md](FP6-KERNEL.md) describes each of these steps and checks.

- Logs and terminal results are in `fp6-kernel/runs/<run>/`; `kernel publish` copies a passed run's
  `candidate` into a checkout of the kernel prebuilts repository
  ([FP6-KERNEL.md](FP6-KERNEL.md#publish-a-kernel-build)).
- `current` advances to that run's `candidate` only after all steps pass.
- Failed runs remain available.
- A retry creates a new run and reuses Bazel's completed work; there is no automatic retry loop.
- `--jobs` bounds the requested build parallelism.
- `--timeout` is a per-command limit, not a limit on the entire multi-step workflow.
- Bazel runs in batch mode so its JVM and workers remain in the owned command group; termination
  unwinds that group instead of leaving a detached build server.
- The workspace lock rejects a simultaneous preparation/build in the same workspace.

### Install the generated inputs

Make sure no build or source sync is running in the Android workspace. Then:

```sh
"$TOOLS_ROOT/bin/diamaneos" build inputs --source "$SOURCE_ROOT" \
  --vendor "$WORK_ROOT/vendor-product"
```

- Add `--replace` to move a differing installed tree aside instead of refusing it.
- This verifies the inventory and installs the complete tree at `vendor/fairphone/FP6`, which
  manifest synchronization alone does not populate.
- The kernel at `device/fairphone/FP6-kernel` is a manifest project and is not installed.
- Installation refuses symlink destinations and differing existing content and publishes the tree
  atomically.
- It writes the generated-input descriptor `.repo/diamaneos-generated-inputs.json` (see [the build
  commands](#the-build-commands)), which the full preflight checks.
- Do not hand-edit the generated tree.

The `android` step continues with the Android build, `package` with the image set and `verify` with
its checks.

## FP6 product

What the device tree (`device/fairphone/FP6`) and the generated vendor tree rely on.

- Device policy and hardware setup are source-owned by `device/fairphone/FP6`.
- The product builds its boot control, power, thermal, lights, vibrator, USB and health services
  from source.
- The kernel uses 4 KiB pages; the product explicitly selects that page size while keeping prebuilt
  alignment and ELF checks enabled.
- The power HAL is LineageOS's libperfmgr, built from source with the device's `power/`
  configuration; Qualcomm's perf2 performance daemon and its client libraries are not selected.
- The device's `libqti-perfd-client` stands in for the client the stock camera and SDM extension
  load by name; it passes only the camera's open, close and snapshot hints to the power HAL, as
  time-limited boosts.
- The power stats HAL is the device's own (`power/stats`): SoC and remote-processor sleep residency
  from the qcom_stats driver, without energy meters; stock has none.
- The stock thermal engine is a separate, explicitly selected input; the thermal HAL itself is
  built from source.
- The product:
  - Selects the generic first-stage ramdisk, GKI v4 headers and the published boot/recovery AVB
    chains.
  - Builds protected VM firmware (`pvmfw`) from source into the system AVB chain.
  - Installs the vendor module blocklist in both the first-stage ramdisk and `vendor_dlkm`.
  - Uses a 48-bit virtual address space kernel
    ([FP6-KERNEL.md](FP6-KERNEL.md#configuration-policy)).
- UFS access remains scoped to the boot-control service.

**API levels and VNDK.**

- The stock input fixes distinct API identities: the device's first API level is 35, while the
  board/vendor API and VNDK are 34. A newer framework version does not advance them.
- Do not copy a platform security-patch value onto unchanged vendor or boot input.
- Stock is a consistent VNDK 34 vendor.
- The DiamaneOS vendor is built from the pinned Android source and has no VNDK version: stock
  blobs link the current vendor variants of their VNDK core and same-process libraries, which are
  installed in the vendor partition, and reach LLNDK through the platform's
  `/system/etc/llndk.libraries.txt`.
- Do not set `ro.vndk.version` or add a VNDK APEX for them.
- With `ro.vndk.version=34` the linker takes LLNDK from the VNDK 34 APEX, so current vendor
  libraries such as `libbinder` cannot reach newer LLNDK dependencies (`libapexsupport.so`), and the
  display HALs fail to link.
- The stock VNDK 34 LLNDK/core/private/same-process lists remain the review gate for which platform
  libraries a selected blob may use.
- A symbol a blob expects but the current library lacks surfaces as a named link failure on the
  device.

**Selecting stock files.**

- Resolve vendor dependencies by ABI and linker namespace, not filename alone.
- Stock holds AArch64 executables alongside DSP firmware ELF files, and init declarations whose
  executables are absent: a declaration alone does not justify installing a service or its factory
  configuration.
- Look beyond the partition `lib64` directories: stock camera, graphics, sound-trigger and audio
  blobs also link libraries of the stock VNDK APEX, and
  `libcom.android.tethering.connectivity_native.so` comes from the tethering APEX.
- Do not import the stock Google tethering package to satisfy a filename match.

**Boot chain.**

- The FP6 bootloader requests the `pvmfw` partition whenever it exists and does not load a slot
  whose verified AVB data omits it, even when unlocked; `pvmfw.img` goes to the same slot as the
  other boot-chain images.
- The FP6 bootloader appends its own bootconfig keys, including `androidboot.fstab_suffix`,
  `androidboot.slot_suffix` and the verified-boot state.
- The kernel rejects the entire bootconfig if any key is assigned twice, leaving userspace without a
  slot suffix.
- Do not set bootloader-supplied keys in `BOARD_BOOTCONFIG`; check the booted `/proc/bootconfig`
  when changing it.
- Every `first_stage_mount` fstab entry must have its mount point in a verified image: first-stage
  init cannot create directories on the read-only partitions.
- A failed entry without `nofail` or `formattable` aborts normal boot, while recovery, which skips
  first-stage mount, still starts.
- The stock `/odm/persist` mount exists only to import device-generated product properties from
  persist; DiamaneOS does not ship that import and does not mount persist there.
- When changing the ramdisk, unpack `init_boot` and check its first-stage `init` (ARM64, static),
  snapuserd and build properties: a correctly sized, signed image can hold an empty ramdisk.
