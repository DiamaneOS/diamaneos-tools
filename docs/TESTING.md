# DiamaneOS testing

How to run the tool tests and checks, and what is recorded about the stock
Fairphone 6 (FP6). Terms: see the [threat model](THREAT_MODEL.md#terms).

## Run the tool tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python bin/diamaneos endpoints validate
```

The suite covers the build, signing, CLI, overlay, font and endpoint checks
with fixtures and needs no phone; schema validation needs the development
dependencies. Test counts are recorded in the acceptance evidence for the exact
tree.

[SIGNING.md](SIGNING.md) describes `bin/diamaneos signing roles`,
`signing inventory` and `signing verify`. Their unit fixtures cover malformed
archives, development-key versus signed-output separation, presigned-package
refusal, source/role drift, incomplete proofs, path escape and artifact
tampering, and sign nothing; real APK, APEX, AVB, full-OTA and delta-OTA
evidence comes from the builder and offline qualification there.

## Regional FP6 qualification

US is `UNVERIFIED` until a US stock comparison and a run on a US-region FP6 are
accepted; no EU result, version-label similarity or reference-ROM support
replaces them. Before claiming one image for both regions, compare the exact EU
and US stock partition/super layout, AVB chain and rollback locations, boot and
vendor images, firmware, VINTF, init/SELinux policy, feature/permission files,
SKU properties, modem profiles and carrier/regulatory configuration. Only
byte-identical files enter the common set unadapted, recorded apart from the
regional delta. Select a runtime delta by an observed trustworthy hardware/boot
SKU property, never locale, language, timezone or location; a boot-critical
delta needs separately bound variants.

## Other checks

**Overlays.** [OVERLAYS.md](OVERLAYS.md) explains
`bin/diamaneos overlays check`; its tests use fixture trees and local Git
remotes, no network:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/overlays -t .
```

**Fonts.** `bin/diamaneos fonts check` reads a product
`fonts_customization.xml` and its fonts as Android does at boot
(FontCustomizationParser, FontListParser and SystemFonts at the pinned
release); a mistake there can drop every system font or stop boot. `--help`
lists the findings. Tests use synthetic fonts, no network. Run it on the build
module whenever the file or a font changes, with the weights the text styles
use, and on a built image before flashing:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/fonts -t .
```

```sh
bin/diamaneos fonts check --module-dir <FONTS_MODULE_DIR> --weights <WEIGHTS> --strict
```

```sh
bin/diamaneos fonts check --xml <PRODUCT_OUT>/product/etc/fonts_customization.xml \
    --font-dir <PRODUCT_OUT>/product/fonts --weights <WEIGHTS> --strict
```

It reads font tables without rasterising, cannot see the system font list (so
not whether a family replaces or an alias points at a system one), and is
stricter than Android's parser, which accepts a DTD and repeated attributes. A
pass is not a boot: `cmd font dump` and logcat stay part of the phone test.

## Endpoint contracts

See [ENDPOINTS.md](ENDPOINTS.md). `endpoints validate` (setup above) reports
inventory-only scope using `tests/endpoints/fixtures/services.json`, works in a
standalone clone and never looks for an infrastructure checkout. To accept a
real service selection, run the two-repository integration gate with the
actual path:

```sh
.venv/bin/python bin/diamaneos endpoints validate \
  --services /absolute/path/to/infrastructure/config/services.json
```

The tests exercise the CLI's validator: required fields, null or wrong types,
reference/ownership/isolation errors, bounded input, duplicate JSON keys,
Unicode byte limits and non-echoing privacy failures. Wire-shape examples check
204/body/time-unit conventions, not native cryptography or device
compatibility. Valid schema data still has implementation gates and is no
active deployment.

`tests/vendor/test_vendor_files.py` covers selected regular-file generation with
the stock identity check and filesystem publication (repeat generation,
retained image metadata, altered input/output, missing notices, wrong stock
identity, absent or self dependency, traversal, special files, concurrent
publication, interrupted copying), using synthetic bytes: no FP6 product
closure or hardware result.
