# Sources and upstream tracking

Where the DiamaneOS source comes from, how the patch series sits on top of it and how the tools
track upstream projects. For people who maintain the manifest, the forks or the patches. Builders
need only the [build reference](BUILD.md).

## The DiamaneOS manifest

[`platform_manifest`](https://github.com/DiamaneOS/platform_manifest) is a full `repo` manifest with
two files:

- `default.xml` is GrapheneOS's manifest at the release DiamaneOS is based on. Its only change is one
  line at the end that includes `diamaneos.xml`.
- `diamaneos.xml` removes the projects the DiamaneOS forks replace and the kernels of other devices.
  It adds the forks, DiamaneOS's own projects, the device maker's and Qualcomm's projects the FP6
  needs, the kernel prebuilts and these tools.

The kernel prebuilts are
[`device_fairphone_FP6-kernels`](https://github.com/DiamaneOS/device_fairphone_FP6-kernels) at
`device/fairphone/FP6-kernel`, fetched with `clone-depth="1"`. The tools are at `tools/diamaneos`, on
branch `main`.

GrapheneOS and AOSP projects keep the exact revisions of GrapheneOS's signed manifest. Its signed
release tag is verified when the manifest takes a new release, and the commit that takes it names
the tag.

A build does not check that tag again. It checks the manifest against the `upstream` record of the
[build environment](BUILD.md#pinned-build-environment):

- `default.xml` without its include line is the tag's `default.xml` byte for byte.
- The included file only adds remotes and projects, and removes projects.
- `repo manifest` gives exactly that composition.

Every build records the resolved manifest, so the commit of each project is known afterwards.

## The patch series

A fix that upstream is expected to make itself, or a library update that upstream will overtake, can
be kept as a patch instead of a fork commit.

The patches live in one manifest project at `diamaneos/patches`. A manifest without that project
builds without a series.

`series.json` there names every patch in order, with:

- its id, project, patch file, SHA-256, title and kind (`fix`, `backport` or `import`)
- for each file it touches, the file's mode and Git blob before and after

These pins decide everything. A patch is applied only to the exact file contents it was reviewed
against, so other commits in the same project do not disturb it. A patch that changes other files, or
gives other contents, than its pins is refused.

**During a build.** `build sync` takes the series off before `repo sync` and applies it again after.
A patched project stays at its manifest commit with the series staged on top:

- `git diff --cached` in a project shows the patches, and `git diff` shows your own edits.
- The resolved manifest names the same commits with and without the series.
- The source check accepts the staged series and nothing else on top. An official build refuses
  local changes in the patch project.
- `build.json` records the series' SHA-256, the patch project's commit and, per project, the base
  commit, the patched tree and the patch ids.
- When a pinned file changes upstream, the sync stops and names the patch.

**Commands.** `--source` is the `repo` checkout, the directory that holds `.repo`:

```sh
tools/diamaneos/bin/diamaneos patches status --source SRC   # applied, unpatched or other, per project
tools/diamaneos/bin/diamaneos patches check --source SRC    # per patch: fits, refresh, upstream or conflict
tools/diamaneos/bin/diamaneos patches apply --source SRC
tools/diamaneos/bin/diamaneos patches reset --source SRC
tools/diamaneos/bin/diamaneos patches pins --source SRC PROJECT FILE.patch   # SHA-256 and pins for series.json
```

`check` judges each patch against the project's manifest commit:

| Result | Meaning |
| --- | --- |
| `fits` | The pinned files are unchanged. |
| `refresh` | A pinned file changed elsewhere and the patch still applies. Renew its pins with `pins`. |
| `upstream` | The project already has the change, so the patch can go. |
| `conflict` | The patch no longer applies and its change is not there. It needs a new version, or removal when its test passes without it. |

## Upstream tracking

[`config/forks.json`](../config/forks.json) lists every upstream that builds and server deployments
use, in two groups, and the DiamaneOS repositories that have no upstream.

**`forks`** are the repositories DiamaneOS forks and changes: the forks the manifest selects, the app
forks and their prebuilt projects, the common kernel and the server forks.

- A fork exists only where DiamaneOS changes the code. A fork that stops carrying a needed change
  leaves the registry, and the manifest takes the upstream project unmodified.
- Once such a fork is deleted, builds whose manifests pinned it can no longer be synced from GitHub.
- A fork's branch is its pin, and `upstream` is the reference it follows. A `follow_note` says what the
  fork changes and, where needed, how it follows upstream.
- GrapheneOS forks follow `@release`, the release tag of the build environment that the registry's
  `release` entry names. A new release is one edit there.
- Server entries have `scope: "server"`, branch `main` and an `upstream_revision` that records the
  adopted upstream commit. The Android manifest does not select them.

To add a fork, add its entry here and select the fork in the manifest. A fork can be watched before
the manifest selects it.

**`sources`** are pinned inputs that are not forked: the GrapheneOS release, the `repo` tool, the
device maker's source manifest, the stock factory package, platform projects taken unmodified, and
the upstream projects the kernel was ported from.

Each names the file and field that hold its pin, so the registry never repeats a revision. An
unmodified mirror on the DiamaneOS organisation, such as `hardware_google_pixel`, is a source pinned
through its manifest path (`xml_project_path`).

**`own_projects`** are the DiamaneOS repositories with no upstream: device, vendor and kernel
repositories, own apps and services, the patch series, the manifest, these tools, the installer and
the website. Each has a one-line `reason`.

**Newer releases.** `newer` patterns name the branches or tags that would replace a followed
reference, such as the device maker's next public-build branch or Qualcomm's next release tag.
Matches are ordered by the pattern's capture group.

`reviewed` names the newest upstream tag that was handled without moving the pin: backported, nothing
to take, or already in a fork. Tags up to it are not newer. On an entry that follows a branch,
`reviewed` can name a reviewed head commit instead.

**Kernel projects.** The kernel's upstream projects, except the common kernel, are folders of
[`kernel_qcom-6.18`](https://github.com/DiamaneOS/kernel_qcom-6.18). That repository was ported from
the projects of the device maker's kernel source release and has no `upstream` branch.

- [`config/kernel-upstream-fp6.json`](../config/kernel-upstream-fp6.json) records the upstream commit
  each project was ported from and the newest release that commit contains.
- `fairphone_changes` marks a port that also carries the device maker's FP6 changes, which a later
  upstream change must keep.
- To take an update, port the change into `kernel_qcom-6.18` as its own commit, and set the project's
  `commit` and `release` in that record.

### The forks commands

```sh
bin/diamaneos forks check           # remote refs only, exit 1 when something moved
bin/diamaneos forks status --fetch  # commits behind and patches carried, per fork
bin/diamaneos forks update <id> --ref <tag> [--rerere DIR]
```

The commands look for the fork clones next to this checkout, each named after its slug. `--root DIR`
names another directory.

`check` downloads no history. It reads each remote's branch and tag names and answers "is this commit
already in the fork?" in the local clone. Its states:

| State | Meaning |
| --- | --- |
| `current` | Nothing to take. |
| `reviewed` | The followed branch moved to a head that `reviewed` names. |
| `update-available` | The followed branch moved. |
| `newer-release` | A newer branch or tag exists. |
| `followed-branch-missing`, `followed-tag-missing` | The followed reference is gone upstream. |
| `pinned-commit` | The entry follows no branch or tag. |
| `clone-missing` | The local clone needed for the answer is not there. |
| `manual-check` | Not a Git source. |
| `error` | The check could not run. |

It exits with 1 when something moved or went missing, and with 2 on an error.

`status` and `update` work on forks only. `update` works like GrapheneOS's own rebases: the new
upstream release is the base, and the fork's commits are replayed on it, on a new local branch
`update/<date>-<commit>`. The fork branch is never moved and nothing is pushed.

- **The fork's commits** are those after the tag or commit the fork is pinned to, not after the
  merge-base. Upstreams that rewrite their history between releases would otherwise make their own
  older commits look like the fork's.
- **Merges are dropped** and their commits replayed one by one. A conflict resolution inside a merge is
  lost, so bring a topic in by rebasing it onto the branch and fast-forwarding.
- **`fixup!` commits fold** into the commit whose title they repeat, so a feature stays one commit
  across releases. Name follow-up fixes that way.
- **`--rerere DIR`** loads git's recorded resolutions from `DIR/<fork slug>` before the rebase and saves
  new ones after. A conflict resolved once then resolves itself later, and the result lists those
  files as `rerere_resolved`. Review them like any other resolution.

Adopting an update is a reviewed, signed change: rebase or port, run the tests (for the kernel, the
kernel build with its checks), build, then push the fork branch or update the pinned revision.

### The coverage check

```sh
bin/diamaneos forks coverage --manifest diamaneos.xml                  # offline
bin/diamaneos forks coverage --manifest diamaneos.xml --org DiamaneOS  # also the organisation
```

`coverage` reads a `repo` manifest with its includes, removals and extensions. Every project on the
`diamaneos` remote (`--remote` names another) must be one of:

- a fork whose `slug` is the project's name, at the fork's `path` when it has one
- a source pinned to the project's path through `xml_project_path`
- an own project of that name

`--org` also lists the organisation's repositories through `gh`, signed in to an account that sees
all of them. Every fork and own project must be there, and every GitHub fork there that is not
archived must be a watched fork.

The output is JSON. `covered` names the entry for each project. The problems are in `uncovered`,
`path_differs`, `not_on_org` and `unwatched_org_forks`. It exits with 1 when there is a problem and
with 2 on an error.

- Without `--org`, it needs no network, so tests and offline checks can run it.
- Forks the manifest does not select are found only by `--org`, and only when GitHub marks them as
  forks. A fork made by pushing an upstream into a new repository needs its entry added by hand.
- Run it with `--org` on the manifest's `diamaneos.xml` before each rebase dry run, and when a
  repository is added to the organisation.
