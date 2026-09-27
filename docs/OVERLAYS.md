# Resource overlay check

`bin/diamaneos overlays check` compares the DiamaneOS runtime resource overlays
with the resources of their target packages at a GrapheneOS release. Most
targets, including framework-res, Settings and SystemUI, declare no
`<overlayable>`, so nothing in the build stops an overlay from naming a
resource that upstream has renamed, moved or removed: the overlay then does
nothing, silently. Run the check on every new GrapheneOS tag and whenever an
overlay changes.

It reads source files only. It does not build, install, sign or push anything,
and it contacts the network only in fetch mode.

## What it reads

**Overlays.** Every `runtime_resource_overlay` module in an `Android.bp` under
the overlay roots in [`config/overlays.json`](../config/overlays.json), paths
relative to `--root` (default: the directory that holds this checkout):

| Root | Role | Allowed partition |
| --- | --- | --- |
| `vendor_diamaneos/overlay` | product (DiamaneOS-wide) | product |
| `device_fairphone_FP6/rro` | device | vendor or odm |

`--product-overlays DIR` and `--device-overlays DIR` (repeatable) replace the
configured roots. For each module the check reads the manifest
(`targetPackage`, `targetName`, `isStatic`, `priority`, `hasCode`), the
partition properties, `certificate` and the resource directories.

**Targets.** The same file registers each target package with the source
projects (by manifest path) and resource directories that make up its APK,
including resources merged from static libraries such as SettingsLib. Patterns
may use `*` for one path segment and `**` for any number. A target that is not
registered is an error, so a new overlay target needs a reviewed registry entry
first; derive its directories from the target module's `resource_dirs` and
static libraries at the release.

Choose one source for the targets:

- `--source-tree DIR`: a checked-out source tree laid out by manifest path. With
  `--source-layout name`, projects are read from directories named after their
  repositories (for example a set of fork clones), which needs the release
  manifest (`--manifest FILE`, or `--cache DIR` to fetch it).
- `--fetch --cache DIR`: fetch from each project's manifest remote. The release
  manifest (`default.xml` of `GrapheneOS/platform_manifest` at `--tag`,
  default: the tag in [`config/build-environment.json`](../config/build-environment.json))
  is fetched first; for the pinned tag its SHA-256 must equal the recorded
  `default_manifest_sha256`. Each target project is then fetched at the full
  commit the manifest pins, as a partial clone with trees only, and only its
  `values*` XML files are downloaded. Everything is cached under `DIR` (one bare
  repository per remote) and reused. Only `https` remotes are accepted.
  `--manifest FILE` uses a local manifest instead; the report says whether its
  digest matches the pinned one.

For the current targets a cold fetch fills about 35 MB of cache and takes
under a minute; a cached run takes seconds.

## What it checks

Errors (exit status 1):

| Code | Meaning |
| --- | --- |
| `missing` | The overlaid resource (type and name) does not exist in the target. |
| `shadowed` | The target also defines the resource under an API-level qualifier (`v31`, …) that applies on every device at the configured `api_level`, and the overlay does not, so the target value wins. |
| `not-overlayable` | The target declares `<overlayable>` and the resource is in no overlayable group. |
| `target-name` | `android:targetName` differs from the resource's overlayable group (idmap2 requires them to be equal). |
| `needs-key` | Only a `signature`, `actor` or `config_signature` policy allows the resource, so the overlay would have to be signed with that key. |
| `policy` | No overlayable policy allows the overlay's partition. |
| `not-static`, `has-code` | The overlay is not `android:isStatic="true"`, or does not declare `android:hasCode="false"`. |
| `partition` | A product overlay is not on product, or a device overlay is not on vendor or odm. |
| `platform-key`, `certificate` | The module sets `certificate`; overlays are signed with the default key, which release signing maps to its own key. |
| `same-priority` | Two overlays on one target have the same `android:priority`. Android orders static overlays by partition, then priority, then APK path, so a tie within one partition is decided by file names. |
| `overlap` | Two overlays on one target define the same resource; the message names the one that wins. |
| `unknown-target` | The target package is not registered. |
| `target-source` | A registered source project or directory is unavailable, or a target file is unreadable. |
| `module`, `manifest`, `resources-map`, `android-mk` | An overlay the check cannot read or model: an unparsable `Android.bp`, a manifest with an `<overlay>` that no module builds, `android:resourcesMap`, or an overlay defined in make. |

Warnings (exit status 0; `--strict` makes them fail):

| Code | Meaning |
| --- | --- |
| `qualifier-not-in-target` | The overlay adds a qualifier (for example a locale) that the target does not define for that resource. It still applies, but it can mean upstream dropped a variant. |
| `target-qualifier` | The target defines the resource for qualifiers the overlay does not cover, where the target's own value still applies (a locale, night mode, a carrier). Qualifiers containing a token listed in `inapplicable_qualifier_tokens` (pseudo-locales, TV and watch modes, tablet widths) are not reported. |
| `flagged-in-target` | Every target definition of the resource is behind an aconfig feature flag. |
| `conditional`, `static-libs` | The overlay depends on a system property, or takes resources from libraries that the check does not read. |

Qualifiers are compared as written in the directory name, ignoring case; implied
version qualifiers (`night` and `night-v8`) are not normalised.

## Output

The text report lists each overlay with its partition, target and priority,
then the errors and warnings. `--json` prints the machine-readable report for
CI: the source (release tag, manifest digest and whether it matched the pinned
one, or the tree), each overlay, each target's resource count and source
revisions, every finding with its code, overlay, target, resource and
qualifiers, and a summary. Exit status 0 means no errors (and, with
`--strict`, no warnings), 1 means findings, 2 means the check could not run.

```sh
# Against the pinned release, fetching target sources into a private cache:
bin/diamaneos overlays check --root <WORK_ROOT> --fetch --cache <CACHE_DIR>

# Against a checked-out source tree, as JSON:
bin/diamaneos overlays check --root <WORK_ROOT> --source-tree <SOURCE_ROOT> --json
```

## Limits

The check reads sources, not built APKs. It does not see resources that resource
shrinking removes from a target, resources that come from prebuilt libraries,
or product variants chosen at build time, and it does not replace running
idmap2 against the built targets or `cmd overlay dump` on a phone. It also does
not decide which resources an overlay may change; that review stays with each
overlay change.
