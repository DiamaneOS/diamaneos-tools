# Resource overlay check

`bin/diamaneos overlays check` compares the DiamaneOS runtime resource overlays
(RROs, which replace another package's resources at runtime) with their
targets' resources at a GrapheneOS release. Most targets, including
framework-res, Settings and SystemUI, declare no `<overlayable>`, so an overlay
naming a resource upstream renamed, moved or removed silently does nothing. Run
it on every new GrapheneOS tag and overlay change, fetching target sources into
a private cache outside this repository. It reads sources only (no build,
install, signing or push) and uses the network only in fetch mode; tests are in
[TESTING.md](TESTING.md#other-checks).

It implements rules 1 to 5 of the DiamaneOS overlay rules (Tally is the
DiamaneOS interface): every overlaid name exists in the target (1); a product
overlay covers every target qualifier (2); product overlays stay within an
allowlist of types and reviewed names (3) and off a denylist (4); no brand
overlay defines what an FP6 overlay defines (5, the `overlap` error). Rule 6,
idmap2 against built targets, stays a check on the phone and the build host.
Product (Tally) overlays fail on rules 1 to 5 without `--strict`, which also
fails warnings (mostly the FP6 hardware overlays' own qualifier gaps). A pass
means names, qualifiers, allowlist and overlayable policies agree with the
sources, nothing more.

```sh
# Against the pinned release, fetching target sources into a private cache:
bin/diamaneos overlays check --root <WORK_ROOT> --fetch --cache <CACHE_DIR>

# Against a newer tag before the build environment moves to it:
bin/diamaneos overlays check --root <WORK_ROOT> --fetch --cache <CACHE_DIR> \
    --tag <TAG> --allowed-signers <GRAPHENEOS_ALLOWED_SIGNERS>

# Against a checked-out source tree, as JSON:
bin/diamaneos overlays check --root <WORK_ROOT> --source-tree <SOURCE_ROOT> --json
```

## Inputs

**Overlays**: every `runtime_resource_overlay` module in an `Android.bp` under
the roots in [`config/overlays.json`](../config/overlays.json), relative to
`--root` (default: the directory holding this checkout):

| Root | Role | Allowed partition |
| --- | --- | --- |
| `vendor_diamaneos/overlay` | product (DiamaneOS-wide) | product |
| `device_fairphone_FP6/rro` | device | vendor or odm |

`--product-overlays DIR` and `--device-overlays DIR` (repeatable) replace these.
Per module it reads the manifest (`targetPackage`, `targetName`, `isStatic`,
`priority`, `hasCode`), partition properties, `certificate` and resource
directories, and it searches every `.mk` file under the roots and `make_roots`
(their repositories) for overlays defined in make.

**Device**: `device` in the same file sets `density_dpi` (the FP6 runs at 480,
xxhdpi) and `smallest_width_dp` (372 at the default display size), which with
`api_level` decide which target variants always apply (see `shadowed`). A
larger display-size setting lowers the smallest width, so variants above 372 dp
are treated as never applying.

**Targets**: each target package is registered with the source projects (by
manifest path) and resource directories of its APK, static libraries such as
SettingsLib included. Patterns may use `*` (one segment) and `**` (any), but
entries list each directory from the module's `resource_dirs` and those of every
Android library it links via `static_libs` at the release, never a wildcard (an
unlinked directory would hide a `missing` resource). An unregistered target is
an error and needs a reviewed entry.

**Target sources**, one of:

- `--source-tree DIR`: a tree laid out by manifest path. `--source-layout name`
  reads projects from directories named after their repositories (such as fork
  clones) and needs the release manifest (`--manifest FILE`, or `--cache DIR`
  to fetch it).
- `--fetch --cache DIR`: first the release manifest (`default.xml` of
  `GrapheneOS/platform_manifest` at `--tag`, default the tag in
  [`config/build-environment.json`](../config/build-environment.json)), whose
  SHA-256 must equal the recorded `default_manifest_sha256` for the pinned tag;
  then each target project at its pinned full commit as a trees-only partial
  clone, downloading only `values*` XML. Everything is cached under `DIR` (one
  bare repository per remote) and reused; only `https` remotes are accepted.
  `--manifest FILE` uses a local manifest; the report says whether its digest
  matches the pinned one. A cold fetch of the current targets fills about 35 MB
  and takes under a minute; a cached run takes seconds.

To check a new release, update `config/build-environment.json` first, or pass
`--allowed-signers FILE` (the GrapheneOS `allowed_signers` file whose SHA-256
the build environment pins): the tag must then be an SSH-signed annotated tag
that `git verify-tag` accepts from the pinned signer identity and key
fingerprint, as the build checks the pinned release. `--allow-unpinned`
fetches on TLS alone, and the report says so. Git runs only on the cache,
without the caller's `GIT_*` variables or user/system configuration (so
`GIT_DIR`, `url.*.insteadOf`, `http.sslVerify=false` and hooks cannot reach
it), with TLS verification on and only the `https` transport.

## Findings

Errors (exit status 1):

- `missing`: the overlaid resource (type and name) is not in the target.
- `shadowed`: on the configured device a target variant beats every overlay
  variant, so the overlay never applies. AssetManager2 uses an overlay value
  only when the overlay's best configuration equals or beats the target's
  (`ResTable_config::isBetterThan`). Modelled qualifiers: smallest width
  (`sw360dp` applies at 372 dp), density (at 480 dpi an `xxhdpi`, `hdpi` or
  `nodpi` target variant beats an overlay default; `anydpi` beats every bucket)
  and API level (`v31` applies at API 37, `v99` never). An overlay variant
  equal to or better than the target's best, such as its own `xxhdpi` or `v33`
  against the target's `v31`, covers the target's variants.
- `target-qualifier`: product overlay only; the target defines the resource for
  qualifiers the overlay leaves uncovered, where its own value still applies (a
  locale, night mode, a carrier), with no `product_rules.qualifier_waivers`
  entry; for a translated string, every target locale (rule 2). Device overlays
  get a warning.
- `not-allowed`: product overlay resource not on the allowlist (rule 3);
  framework-res declares no `<overlayable>`, so this is its only guard.
- `denied`: product overlay name, name pattern or type on the target's denylist
  (rule 4), which beats the allowlist: PermissionController's privacy toggles,
  access notifications, password toggle, grant-dialog switches and all its
  booleans and strings (roles, visibility, help URLs), and SystemUI's
  privacy-indicator resources and every Tally token or indicator resource
  (`tally_*`: sensor and capture colours, the privacy dot's size and margin,
  lamps and springs; the lens config is a device value).
- `not-overlayable`: the target declares `<overlayable>` and the resource is in
  no group.
- `target-name`: `android:targetName` differs from the resource's overlayable
  group (idmap2 requires equality).
- `needs-key`: only a `signature`, `actor` or `config_signature` policy allows
  the resource, needing that key.
- `policy`: no overlayable policy allows the overlay's partition.
- `not-static`, `has-code`: not `android:isStatic="true"`, or no
  `android:hasCode="false"`.
- `partition`: a product overlay not on product, or a device overlay not on
  vendor or odm.
- `platform-key`, `certificate`: the module sets `certificate`; overlays use the
  default key, which release signing maps to its own.
- `same-priority`: two overlays on one partition and target share
  `android:priority`. Android orders static overlays by partition, then
  priority, then APK path, so the tie falls to file names; priority is never
  compared across partitions (a later partition, product after vendor, always
  wins), so overlays on different partitions may share one.
- `overlap`: two overlays on one target define the same resource; the message
  names the winner.
- `unknown-target`: the target package is not registered.
- `target-source`: a registered source project or directory is unavailable, or a
  target file unreadable.
- `module`, `manifest`, `resources-map`, `android-mk`, `static-libs`: an
  overlay the check cannot read or model: an unparsable or unreadable
  `Android.bp` or resource file, `defaults` on an overlay module, an
  `override_runtime_resource_overlay`, a manifest `<overlay>` no module builds,
  `android:resourcesMap`, overlays defined in make (`BUILD_RRO_PACKAGE`,
  `PRODUCT_PACKAGE_OVERLAYS`, `DEVICE_PACKAGE_OVERLAYS`) or resources from
  libraries (`static_libs`, `resource_libs`).

Warnings (exit status 0; `--strict` fails them):

- `qualifier-not-in-target`: the overlay adds a qualifier (such as a locale) the
  target lacks for that resource; it applies, but upstream may have dropped a
  variant.
- `target-qualifier`: device overlays, as the error.
- `flagged-in-target`: every target definition is behind an aconfig feature
  flag.
- `conditional`: the overlay depends on a system property.

Qualifiers that never match the device go unreported: tokens in
`inapplicable_qualifier_tokens` (pseudo-locales, TV and watch modes), smallest
widths above the device's and API levels above `api_level`. Others are compared
as written in the directory name, ignoring case; implied version qualifiers
(`night` and `night-v8`) are not normalised.

## Product rules

`product_rules` holds rules 2 to 4 for product overlays; the FP6 hardware
(device) overlays are exempt from 3 and 4.

- `allowed_types`: types a Tally overlay may change freely (styles, colours,
  drawables, dimens, fonts, mipmaps, reviewed anim files); never strings.
  Allowed unless the name starts with a `restricted_prefixes` entry.
- `restricted_prefixes`: prefixes (`config_`) needing a listed name even for an
  allowed type.
- `allowed_names`, `allowed_strings`: per target, each reviewed name with its
  reason (such as framework-res's `config_buttonTextAllCaps` or DocumentsUI's
  `force_material3`, and the listed rebrand strings); required for any other
  type and every string.
- `denied_names`, `denied_patterns` (regular expressions), `denied_types`: per
  target with a reason; they beat the allowlist.
- `qualifier_waivers`: `{overlay, resource, qualifiers, reason}` for target
  qualifiers a product overlay deliberately leaves to the target (`*` for all).

Adding a name is a review decision: give the reason, and update the threat
model in the same change if it touches a security or privacy surface.

## Output and limits

The text report lists each overlay with partition, target and priority, then
errors and warnings. `--json` gives the CI report: the source (release tag,
manifest digest and whether it matched the pinned one, or the tree), each
overlay, each target's resource count and source revisions, every finding with
code, overlay, target, resource and qualifiers, and a summary. The source's
`manifest` field is `pinned-digest`, `signed-tag` (a verified non-pinned tag),
`unverified-tag` (`--allow-unpinned`) or `unverified-file`. Exit status 0 means
no errors (with `--strict`, no warnings), 1 findings, 2 the check could not
run.

It reads sources, not built APKs, so it misses resources removed by resource
shrinking, resources from prebuilt libraries and build-time product variants,
and does not replace idmap2 on built targets or `cmd overlay dump` on a phone.
The device model covers smallest width, density and API level only; other
qualifiers are compared by name. Choosing allowlist and denylist names stays a
review of each overlay change.
