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

It implements rules 1 to 5 of the agreed overlay spec (the "All Tally overlays"
section of the private fork review): every overlaid name exists in the target
(1), a product overlay covers every target qualifier (2), product overlays stay
within an allowlist of types and reviewed names (3) and off a denylist (4), and
no brand overlay defines what an FP6 overlay defines (5, the `overlap` error).
Rule 6, idmap2 against the built targets, needs a build and stays a phone and
builder check.

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
partition properties, `certificate` and the resource directories. Every `.mk`
file under the overlay roots and under `make_roots` (the repositories that hold
them) is searched for overlays defined in make.

**Device.** `device` in the same file gives the phone the overlays are checked
for: `density_dpi` (the FP6 runs at 480, xxhdpi) and `smallest_width_dp` (372 at
the default display size). With `api_level` they decide which target variants
always apply on the phone (see `shadowed`). A larger display-size setting lowers
the smallest width, so a variant for a width above 372 dp is treated as never
applying.

**Targets.** The same file registers each target package with the source
projects (by manifest path) and resource directories that make up its APK,
including resources merged from static libraries such as SettingsLib. Patterns
may use `*` for one path segment and `**` for any number, but the registered
entries list each directory: they are derived from the target module's
`resource_dirs` and the `resource_dirs` of every Android library it links
through `static_libs` at the release, never from a wildcard, because a
directory the target does not link would hide a `missing` resource. A target
that is not registered is an error, so a new overlay target needs a reviewed
registry entry first.

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

  A tag other than the pinned one is fetched only with `--allowed-signers FILE`,
  the GrapheneOS `allowed_signers` file whose SHA-256 the build environment pins:
  the tag must be an SSH-signed annotated tag that `git verify-tag` accepts from
  the pinned signer identity and key fingerprint, as the build checks the pinned
  release. `--allow-unpinned` fetches it on TLS alone instead, and the report says
  so. The simplest way to check a new release is to update
  `config/build-environment.json` first.

  Git runs only on the cache: without the caller's `GIT_*` variables or user and
  system Git configuration (so `GIT_DIR`, `url.*.insteadOf`, `http.sslVerify=false`
  and hooks cannot reach it), with TLS verification on and only the `https`
  transport allowed.

For the current targets a cold fetch fills about 35 MB of cache and takes
under a minute; a cached run takes seconds.

## What it checks

Errors (exit status 1):

| Code | Meaning |
| --- | --- |
| `missing` | The overlaid resource (type and name) does not exist in the target. |
| `shadowed` | On the configured device a target variant beats every variant the overlay has, so the overlaid value never applies there. AssetManager2 uses an overlay's value only when the overlay's best configuration is equal to or better than the target's (`ResTable_config::isBetterThan`). The check models qualifiers made of a smallest width (`sw360dp` applies at 372 dp), a density (at 480 dpi an `xxhdpi`, `hdpi` or `nodpi` target variant beats an overlay's default; `anydpi` beats every bucket) and an API level (`v31` applies at API 37, `v99` never does); an overlay variant that equals or beats the target's best, such as its own `xxhdpi` or `v33` against the target's `v31`, covers the target's variants. |
| `target-qualifier` | Product overlays: the target defines the resource for qualifiers the overlay does not cover, where the target's own value still applies (a locale, night mode, a carrier), and no entry in `product_rules.qualifier_waivers` names them. For a translated string this means every target locale (rule 2). Device overlays get a warning instead. |
| `not-allowed` | Product overlays: the resource is not on the allowlist (rule 3). Types in `allowed_types` are allowed unless the name starts with a `restricted_prefixes` entry (`config_`); any other type, and every string, needs its name in `allowed_names` or `allowed_strings` for the target, each with the reason it was reviewed. framework-res declares no `<overlayable>`, so this list is its only guard. |
| `denied` | Product overlays: the name, a name pattern or the type is on the denylist for the target (rule 4), which wins over the allowlist: PermissionController's privacy toggles, access notifications, password toggle, grant-dialog switches, and all its booleans and strings (roles, visibility, help URLs); SystemUI's privacy-indicator resources and every Tally token or indicator resource (`tally_*`: sensor and capture colours, the privacy dot's size and margin, lamps and springs; the lens config is a device value). |
| `not-overlayable` | The target declares `<overlayable>` and the resource is in no overlayable group. |
| `target-name` | `android:targetName` differs from the resource's overlayable group (idmap2 requires them to be equal). |
| `needs-key` | Only a `signature`, `actor` or `config_signature` policy allows the resource, so the overlay would have to be signed with that key. |
| `policy` | No overlayable policy allows the overlay's partition. |
| `not-static`, `has-code` | The overlay is not `android:isStatic="true"`, or does not declare `android:hasCode="false"`. |
| `partition` | A product overlay is not on product, or a device overlay is not on vendor or odm. |
| `platform-key`, `certificate` | The module sets `certificate`; overlays are signed with the default key, which release signing maps to its own key. |
| `same-priority` | Two overlays on the same partition and target have the same `android:priority`. Android orders static overlays by partition, then priority, then APK path, so such a tie is decided by file names. Priority is never compared across partitions: an overlay on a later partition (product after vendor) always wins, so overlays on different partitions may share a priority. |
| `overlap` | Two overlays on one target define the same resource; the message names the one that wins. |
| `unknown-target` | The target package is not registered. |
| `target-source` | A registered source project or directory is unavailable, or a target file is unreadable. |
| `module`, `manifest`, `resources-map`, `android-mk`, `static-libs` | An overlay the check cannot read or model: an unparsable or unreadable `Android.bp` or resource file, `defaults` on an overlay module, an `override_runtime_resource_overlay`, a manifest with an `<overlay>` that no module builds, `android:resourcesMap`, an overlay defined in make (`BUILD_RRO_PACKAGE`, `PRODUCT_PACKAGE_OVERLAYS`, `DEVICE_PACKAGE_OVERLAYS`), or resources taken from libraries (`static_libs`, `resource_libs`). |

Warnings (exit status 0; `--strict` makes them fail):

| Code | Meaning |
| --- | --- |
| `qualifier-not-in-target` | The overlay adds a qualifier (for example a locale) that the target does not define for that resource. It still applies, but it can mean upstream dropped a variant. |
| `target-qualifier` | Device overlays: as the error above. |
| `flagged-in-target` | Every target definition of the resource is behind an aconfig feature flag. |
| `conditional` | The overlay depends on a system property. |

Qualifiers that never match the device are not reported: a token listed in
`inapplicable_qualifier_tokens` (pseudo-locales, TV and watch modes), a smallest
width above the device's, or an API level above `api_level`. Other qualifiers
are compared as written in the directory name, ignoring case; implied version
qualifiers (`night` and `night-v8`) are not normalised.

## Product rules

`product_rules` in the configuration holds rules 2 to 4 for product overlays;
the FP6 hardware overlays (device role) are exempt from rules 3 and 4.

- `allowed_types`: the types a Tally overlay may change freely (styles,
  colours, drawables, dimens, fonts, mipmaps, reviewed anim files). Strings are
  never on it.
- `restricted_prefixes`: name prefixes (`config_`) that need a listed name even
  for an allowed type.
- `allowed_names` and `allowed_strings`: per target, each reviewed name with its
  reason, for example framework-res's `config_buttonTextAllCaps` or
  DocumentsUI's `force_material3`, and the listed rebrand strings.
- `denied_names`, `denied_patterns` (regular expressions) and `denied_types`:
  per target, with the reason; they win over the allowlist.
- `qualifier_waivers`: `{overlay, resource, qualifiers, reason}` entries for
  target qualifiers a product overlay deliberately leaves to the target (`*`
  for all of them).

Adding a name is a review decision: record why in the reason, and update the
threat model in the same change when the name touches a security or privacy
surface.

## Output

The text report lists each overlay with its partition, target and priority,
then the errors and warnings. `--json` prints the machine-readable report for
CI: the source (release tag, manifest digest and whether it matched the pinned
one, or the tree), each overlay, each target's resource count and source
revisions, every finding with its code, overlay, target, resource and
qualifiers, and a summary. Exit status 0 means no errors (and, with
`--strict`, no warnings), 1 means findings, 2 means the check could not run.

The `manifest` field of the source says how the release manifest was
identified: `pinned-digest`, `signed-tag` (a verified tag other than the pinned
one), `unverified-tag` (`--allow-unpinned`) or `unverified-file`.

```sh
# Against the pinned release, fetching target sources into a private cache:
bin/diamaneos overlays check --root <WORK_ROOT> --fetch --cache <CACHE_DIR>

# Against a newer tag before the build environment moves to it:
bin/diamaneos overlays check --root <WORK_ROOT> --fetch --cache <CACHE_DIR> \
    --tag <TAG> --allowed-signers <GRAPHENEOS_ALLOWED_SIGNERS>

# Against a checked-out source tree, as JSON:
bin/diamaneos overlays check --root <WORK_ROOT> --source-tree <SOURCE_ROOT> --json
```

## Limits

The check reads sources, not built APKs. It does not see resources that resource
shrinking removes from a target, resources that come from prebuilt libraries,
or product variants chosen at build time, and it does not replace running
idmap2 against the built targets or `cmd overlay dump` on a phone. The device
model covers smallest width, density and API level only; other qualifier
combinations are compared by name. The product allowlist and denylist enforce
the reviewed names, but choosing them stays a review of each overlay change.
