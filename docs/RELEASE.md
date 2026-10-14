# Releases

`diamaneos release` turns a verified official image set into a release: the files DiamaneOS
publishes for one build and the record of what they were made from.

## Make a release

```sh
tools/diamaneos/bin/diamaneos build all --official
tools/diamaneos/bin/diamaneos release --channel alpha
```

- It takes the workspace's latest image set, or the image directory given, and the verify report
  `build verify` wrote next to it (`--verify-report FILE` names another).
- `--channel` names an update channel the release goes to: `alpha`, `beta` or `stable`. Give it once
  for each channel.
- `--incremental-from RELEASE` also makes an incremental OTA package from an earlier release
  directory.
- `--check` runs every check and lists the files the release would hold. It writes nothing.
- Making the OTA packages needs Linux on x86-64, Java (the workspace's `prebuilts/jdk`, or one on
  `PATH`) and `openssl`, as the build host has.

## What a release holds

The release goes to `releases/FP6-<build number>/` in the workspace (`--output DIR` names another
directory for the releases).

| File | What it is |
| --- | --- |
| `publish/FP6-factory-<n>.zip` | The factory package: the image set as the user flashes it |
| `publish/FP6-ota_update-<n>.zip` | The full OTA package |
| `publish/FP6-incremental-<old>-<n>.zip` | With `--incremental-from`: the update from release `<old>` |
| `publish/FP6-<channel>` | The update information of each channel |
| `provenance/` | `build.json`, `resolved-manifest.xml`, the set's `SHA256SUMS`, the verify report and `release.json` |
| `FP6-target_files-<n>.zip` | The build's target-files, for a later incremental OTA package. Never published |
| `release.log` | The output of the OTA tool |

- **Factory package.**
  - Every file of the image set except `target-files.zip` and `otatools.zip`, in one directory
    `FP6-factory-<n>/`, with its own `SHA256SUMS`.
  - `flash-all.sh` and `flash-all.bat` are the scripts `build package` writes ([BUILDING.md, step
    5](BUILDING.md#5-flash)). The command writes them again from `build.json` and refuses a set whose
    copies differ.
  - Its entries are sorted and carry the build date, so a rerun gives the same package.
- **OTA packages.**
  - `ota_from_target_files` from the set's `otatools.zip` makes them from the set's target-files,
    signed with the public test key that the target-files names.
  - The command then checks what the Updater checks: an A/B package for this device, with the build
    number and build date of `build.json`, bound to no serial number, with a payload offset.
  - It also checks the build fingerprint, that the package carries the test key's certificate, and
    for an incremental package the earlier build it updates from.
- **Update information.**
  - One line, `<build number> <build date> <device> <channel>`, taken from the full OTA package, as
    GrapheneOS's metadata script writes it.
  - The Updater reads `https://releases.diamaneos.de/FP6-<channel>`, then downloads
    `FP6-incremental-<installed>-<n>.zip`, or `FP6-ota_update-<n>.zip` when there is none.
  - `publish/` therefore goes to the root of the update server. Upload the channel files last, so a
    phone that reads one finds its packages.
- **release.json.**
  - The hash and size of every file, the channels, the build fingerprint and the signing.
  - The commits of the manifest, the kernel prebuilts and the tools that built, packaged and released
    the set, the hashes of every file of the image set and the verify result.
  - Whether `build.json` says the wipe and the firmware steps of `flash-all` are tested on a phone.

A run that fails leaves `FP6-<n>.partial` with its log, and the next run removes it.

## What it refuses

- An image set that is not an official `user` build, or that `build.json` marks not reproducible.
- A verify report that is missing, failed, belongs to another set or lacks a check, and one made with
  other image checks than these tools have.
- A set that lacks a file `build.json` names or holds one it does not name, a file that does not
  match `SHA256SUMS`, and a `SHA256SUMS` that disagrees with `build.json`.
- Flash scripts other than the ones these tools write, and tools with local changes.
- An OTA package that fails a check above.
- An existing release directory: a release is made once.
- For `--incremental-from`: a release that is not older, is for another device or has other keys.

## Signing

- Every image set these tools make is signed with the public test keys, and so is its release:
  `release.json` says so (`public-test-keys`) and lists no signature.
- The Updater of such a build checks for updates but installs none, because anyone can sign with
  these keys.
- A `build.json` that claims other signing (another `signing`, or `release: true`) is refused, and
  so is an OTA package with another certificate. The command writes no signature.
- [SIGNING.md](SIGNING.md) describes the signing roles and the publication verification.
