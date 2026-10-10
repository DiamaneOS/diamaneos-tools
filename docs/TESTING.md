# DiamaneOS testing

How to run the tool tests and the standalone checks. Terms: see [TERMS.md](TERMS.md).

## Run the tool tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
```

The suite covers the build, signing, CLI, overlay and font checks with fixtures and needs no phone;
schema validation needs the development dependencies.

- **Signing.**
  - [SIGNING.md](SIGNING.md) describes build-bound plans and native publication verification.
  - Unit fixtures cover verified-build hashes, complete reports, role mappings, archive boundaries,
    external trust material and artifact tampering.
  - Native fixtures generate disposable keys and check APK, APEX, AVB, OTA and SSH verification,
    including corrupt, unsigned, wrong-key, wrong-role and public-test-key rejection.

- **Vendor files.**
  - `tests/vendor/test_vendor_files.py` covers selected regular-file generation with the stock
    identity check and filesystem publication, using synthetic bytes.
  - Cases: repeat generation, retained image metadata, altered input/output, missing notices, wrong
    stock identity, absent or self dependency, traversal, special files, concurrent publication and
    interrupted copying.

## Regions

- Vendor files and firmware come from the one stock package selected in `config/stock-inputs.json`,
  recorded there as the EU input.
- The US package that file lists as excluded is never an input: the recipes pin the selected archive
  by hash, and the tools reject any other.
- Results on an EU phone say nothing about a US-region FP6.

## Other checks

**Overlays.** [OVERLAYS.md](OVERLAYS.md) explains `bin/diamaneos overlays check`; its tests use
fixture trees and local Git remotes, no network:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/overlays -t .
```

**Fonts.** `bin/diamaneos fonts check` reads a product `fonts_customization.xml` and its fonts as
Android does at boot (FontCustomizationParser, FontListParser and SystemFonts at the pinned
release); a mistake there can drop every system font or stop boot. `--help` lists the findings.
Tests use synthetic fonts, no network:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/fonts -t .
```

Run it on the build module whenever the file or a font changes, with the weights the text styles
use, and on a built image before flashing:

```sh
bin/diamaneos fonts check --module-dir <FONTS_MODULE_DIR> --weights <WEIGHTS> --strict
```

```sh
bin/diamaneos fonts check --xml <PRODUCT_OUT>/product/etc/fonts_customization.xml \
    --font-dir <PRODUCT_OUT>/product/fonts --weights <WEIGHTS> --strict
```

- It reads font tables without rasterising, cannot see the system font list (so not whether a family
  replaces or an alias points at a system one), and is stricter than Android's parser, which accepts
  a DTD and repeated attributes.
- A pass is not a boot: `cmd font dump` and logcat stay part of the phone test.
