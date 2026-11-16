# Resource overlay check

`overlays check` compares DiamaneOS's runtime resource overlays (RROs) with the resources of their
target packages at a GrapheneOS release. For anyone who changes an overlay or moves the build to a
new release. Terms are explained in [TERMS.md](TERMS.md).

## Why it exists

An overlay replaces another package's resources at run time. Most targets, including framework-res,
Settings and SystemUI, declare no `<overlayable>`. An overlay that names a resource upstream renamed,
moved or removed then silently does nothing.

Run the check on every new release tag and every overlay change. It reads sources only: nothing is
built, installed, signed or pushed, and it uses the network only to fetch.

## Running it

```sh
# Against the pinned release, fetching the target sources into a cache:
bin/diamaneos overlays check --root <WORK_ROOT> --fetch --cache <CACHE_DIR>

# Against a newer tag, before the build environment moves to it:
bin/diamaneos overlays check --root <WORK_ROOT> --fetch --cache <CACHE_DIR> \
    --tag <TAG> --allowed-signers <GRAPHENEOS_ALLOWED_SIGNERS>

# Against a checked-out source tree, as JSON:
bin/diamaneos overlays check --root <WORK_ROOT> --source-tree <SOURCE_ROOT> --json
```

Run it from a checkout of these tools. `--root` is the directory that holds the overlay repositories
(default: the directory holding this checkout).

The exit status is 0 without errors, 1 with findings and 2 when the check could not run. `--strict`
also fails on warnings.

A pass means that names, qualifiers, the allowlist and the overlayable policies agree with the
sources, nothing more. Whether idmap2 accepts the overlays against the built targets stays a check
on the phone.

## Inputs

**Overlays.** Every `runtime_resource_overlay` module in an `Android.bp` under the roots in
[`config/overlays.json`](../config/overlays.json):

| Root | Role | Partition |
| --- | --- | --- |
| `vendor_diamaneos/overlay` | Product: the DiamaneOS-wide (Tally) overlays | product |
| `device_fairphone_FP6/rro` | Device: the FP6 hardware overlays | vendor or odm |

`--product-overlays DIR` and `--device-overlays DIR` (repeatable) replace these roots. For each module
the check reads the manifest, the partition, the `certificate` and the resource directories. It also
searches the `.mk` files of the overlay repositories for overlays defined in make, which it cannot
model.

**Device.** `device` in the same file gives the display density and smallest width at the default
display size, and `api_level` the API level.

These decide which target variants always apply. A larger display size lowers the smallest width, so
variants above the configured width never apply.

**Targets.** Each target package is registered in `targets` with its source projects and resource
directories, static libraries such as SettingsLib included.

- Entries list each directory from the module's `resource_dirs` and those of every library it links,
  never a wildcard, because an unlinked directory would hide a missing resource.
- An unregistered target is an error and needs a reviewed entry.
- A presigned app has no resource sources. Its entry lists the default resources read from the APK
  with `aapt2 dump resources` and the project revision they were read at. Another revision in the
  manifest is an error until they are read again.

**Target sources**, one of:

- `--source-tree DIR`: a tree laid out by manifest path. `--source-layout name` reads projects from
  directories named after their repositories instead, and needs the release manifest (`--manifest` or
  `--cache`).
- `--fetch --cache DIR`: the release manifest of `--tag` (default: the tag of
  [`config/build-environment-fp6.json`](../config/build-environment-fp6.json)), whose SHA-256 must
  equal the recorded one, then each target project at its pinned commit.

  Only the `values*` XML is downloaded, cached under `DIR` and reused, so a cached run needs no
  network.
- `--manifest FILE` uses a local manifest, and the report says whether its digest is the pinned one.

To check a tag other than the pinned one, pass `--allowed-signers FILE`, the allowed signers file
whose SHA-256 the build environment pins. The tag must then be a signed tag from the pinned signer.
`--allow-unpinned` fetches on TLS alone, and the report says so.

Git runs only on the cache, over `https` with TLS verification. It runs without your `GIT_*` variables
or Git configuration, so settings such as `url.*.insteadOf` or hooks cannot reach it.

## Findings

`--help` lists every finding code. The main ones:

| Code | Meaning |
| --- | --- |
| `missing` | The overlaid resource is not in the target. |
| `shadowed` | On the configured device, a target variant beats every overlay variant, so the overlay never applies. |
| `target-qualifier` | The target defines the resource for qualifiers the overlay leaves uncovered, such as a locale or night mode. An error for product overlays without a waiver, a warning for device overlays. |
| `not-allowed`, `denied` | A product overlay changes a resource that is not on the allowlist, or is on the denylist. |
| `not-overlayable`, `target-name`, `needs-key`, `policy` | The target declares `<overlayable>`, and the overlay does not fit its groups or policies. |
| `not-static`, `has-code`, `partition`, `certificate`, `platform-key` | Packaging rules for preinstalled overlays: static, no code, the right partition and the default key. |
| `same-priority` | Two overlays on one partition and target share `android:priority`, so the order falls to file names. |
| `overlap` | Two overlays on one target define the same resource. The message names the winner. |
| `unknown-target`, `target-source` | The target is not registered, or its sources are unavailable. |
| `module`, `manifest`, `resources-map`, `android-mk`, `static-libs` | An overlay the check cannot read or model. |

**How `shadowed` is decided.** Android uses an overlay value only when the overlay's best
configuration equals or beats the target's. With the FP6 values:

- Smallest width: `sw360dp` applies at 372 dp.
- Density: at 480 dpi an `xxhdpi`, `hdpi` or `nodpi` target variant beats an overlay default, and
  `anydpi` beats every density.
- API level: `v31` applies at API 37, and `v99` never does.

Qualifiers that never match the device go unreported: `inapplicable_qualifier_tokens` (pseudo-locales,
TV and watch modes), smallest widths above the device's and API levels above `api_level`. Other
qualifiers are compared as written, ignoring case.

**Warnings**, which fail only with `--strict`: `qualifier-not-in-target` (the overlay adds a qualifier
the target lacks), `target-qualifier` on a device overlay, `flagged-in-target` (every target definition
is behind a feature flag) and `conditional` (the overlay depends on a system property).

## Product rules

`product_rules` in `config/overlays.json` limits what the product (Tally) overlays may change. The
device overlays are exempt from the allowlist and denylist.

| Field | Meaning |
| --- | --- |
| `allowed_types` | Types a product overlay may change freely: styles, colours, drawables, dimensions, fonts, mipmaps and reviewed animations, never strings. |
| `restricted_prefixes` | Name prefixes, such as `config_`, that need a listed name even for an allowed type. |
| `allowed_names`, `allowed_strings` | Per target, each reviewed name with its reason. Required for any other type and every string. |
| `denied_names`, `denied_patterns`, `denied_types` | Per target, with a reason. They beat the allowlist, for example for PermissionController's privacy settings and SystemUI's privacy indicators and Tally tokens. |
| `qualifier_waivers` | Target qualifiers a product overlay leaves to the target on purpose, with a reason. |

Adding a name is a review decision: give the reason.

## Output and limits

The text report lists each overlay with its partition, target and priority, then the errors and
warnings.

`--json` adds the source (release tag and manifest digest, or the tree), each target's resource
count and source revisions, and every finding with its code, overlay, target, resource and
qualifiers.

- It reads sources, not built APKs. It misses resources removed by shrinking, resources from prebuilt
  libraries and build-time variants.
- The device model covers smallest width, density and API level. Other qualifiers are compared by
  name.
- Choosing allowlist and denylist names stays a review of each overlay change.

The tests use fixture trees and local Git remotes ([TESTING.md](TESTING.md#standalone-checks)).
