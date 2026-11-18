# Testing

How to run the tests of these tools, and the standalone checks for fonts, strings, overlays and the
patch series. For anyone who changes the tools or the files they check. Terms are explained in
[TERMS.md](TERMS.md).

## The test suite

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -t .
```

The suite covers the build steps, signing, the command line and the standalone checks with fixtures.
It needs Python 3.11 or newer, and no phone, Android source or network. Install
`requirements-dev.txt` first: without `jsonschema`, the schema and recipe tests fail.

To run one area, give its directory, such as `-s tests/build`. The suite also holds the document
checks: no list item or paragraph in a document may exceed 45 words (`tests/docs`).

### Signing

The unit fixtures cover build hashes, complete reports, role mappings, archive boundaries, external
keys and tampered artifacts. The factory fixtures run the real publication code with controlled native
steps, so they prove the orchestration, not the cryptography.

The native fixtures make disposable keys and check APK, APEX, AVB, OTA and SSH verification with the
real platform tools. They need a source checkout and a build's `otatools.zip`:

```sh
DIAMANEOS_SIGNING_NATIVE_SOURCE="$SOURCE" \
DIAMANEOS_SIGNING_OTATOOLS="$IMAGES/otatools.zip" \
PYTHONPATH=src .venv/bin/python -m unittest tests.signing.test_native_signatures
```

On Linux the full suite requires both variables, and missing inputs fail the native fixtures. On
other hosts they are skipped, unless `DIAMANEOS_SIGNING_REQUIRE_NATIVE=1` makes them required there
too. They check that corrupt, unsigned, wrong-key, wrong-role and public-test-key artifacts are
refused.

## Standalone checks

Run these from a checkout of these tools. Each check reads files only, and `--help` on each command
lists its findings.

### Fonts

`fonts check` reads a product `fonts_customization.xml` and its fonts the way Android does at boot. A
mistake there is not local to the added fonts: a malformed file makes Android drop every system font,
and some mistakes stop boot.

Run it on the build module whenever the file or a font changes, with the weights the text styles use,
and on a built image before flashing:

```sh
bin/diamaneos fonts check --module-dir <FONTS_MODULE_DIR> --weights <WEIGHTS> --strict
bin/diamaneos fonts check --xml <PRODUCT_OUT>/product/etc/fonts_customization.xml \
    --font-dir <PRODUCT_OUT>/product/fonts --weights <WEIGHTS> --strict
```

It reads font tables without drawing, cannot see the system font list, and is stricter than Android's
parser. A pass is not a boot: `cmd font dump` and logcat stay part of the phone test.

### Strings

`strings check` reads string resources as aapt2 does and compares each translation with its default:
escaping, placeholders, spans, plurals and arrays. For a fork, give the upstream base. The check then
covers the strings changed since the base, in every locale:

```sh
bin/diamaneos strings check --repo <FORK> --base <UPSTREAM_COMMIT> --head <BRANCH>
```

A repository of its own takes the empty tree as base, which `--help` names. The check does not know
which strings the code formats, so a lone `%` counts only in strings with placeholders. It cannot see
an overlay target's defaults.

### Overlays and patches

- [OVERLAYS.md](OVERLAYS.md) describes `overlays check`. Its tests use fixture trees and local Git
  remotes.
- [SOURCES.md](SOURCES.md#the-patch-series) describes `patches`. Its tests use small local Git
  repositories.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/overlays -t .
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests/patches -t .
```

### Carrier configuration

After a build, `carrier_data` compares the built `CarrierConfig.apk` with the vendor generation
([CARRIER-INTEGRATION.md](CARRIER-INTEGRATION.md#checks)).

## Fuzz targets

`tests/fuzz` holds Atheris fuzz targets for the parsers of signing, publication and image inputs.
Each checks properties, not only crashes, and records the first input of each kind of finding. The
test suite above does not use them.

`tests/fuzz/regress.tar.gz` is a small merged corpus of every target. Replay it on the builder
whenever a signing, publication or image parser changes.

The targets need Linux on x86_64 with Python 3.13, the only wheel `requirements-fuzz.txt` allows. On
Debian 13 without `python3-venv` or root, as on the builder, set up a work folder once:

```sh
cd "$WORK"
apt-get download python3-pip-whl
dpkg-deb -x python3-pip-whl_*.deb root
python3 -m venv --without-pip venv
pip=$(echo root/usr/share/python-wheels/pip-*.whl)
venv/bin/python "$pip/pip" install --require-hashes \
    -r "$TOOLS/requirements-fuzz.txt"
```

Then replay from the checkout:

```sh
FUZZ_PYTHON="$WORK/venv/bin/python" nice -n 19 tests/fuzz/run.sh replay "$WORK/replay"
```

- The replay runs every input once. It fails when a target stops or records a kind of finding that
  `tests/fuzz/known-findings.txt` does not list.
- `run.sh TARGET SECONDS DIR` fuzzes one target, named by its file without `fuzz_`. The corpus,
  findings, log and throwaway test keys stay in DIR.
- `run.sh merge DIR` writes `DIR/regress.tar.gz` from the archive and the corpora in `DIR/<target>`,
  minimised. It takes only the public test keys.
- The ssh and attestation targets need `ssh-keygen` and `openssl` in `/usr/bin`.
