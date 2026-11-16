# diamaneos-tools

Host tooling and machine-readable project maps for DiamaneOS.

## Project context

DiamaneOS is a privacy and security focused operating system based on GrapheneOS, for the
[supported devices](https://diamaneos.de/devices/).

> DiamaneOS is based on GrapheneOS. It is not made or endorsed by GrapheneOS or by any device
> maker.

## Licence

- Original DiamaneOS code, documentation and artwork here are [Apache-2.0](LICENSE) unless another
  licence is identified.
- Third-party design assets are not in this repository. They carry their own licence notices
  ([NOTICE](NOTICE)).
- The copyright licence does not cover the DiamaneOS name and logo as trademarks (Apache licence,
  section 6).
- Referenced upstream projects and externally installed dependencies keep their own licences.

## Workspace and tools

Clone into any directory. Run commands from the repository root unless stated otherwise.

- `WORK_ROOT` in the docs: a directory you choose for related checkouts or work files, not a
  required path.
- `TOOLS_ROOT`: this checkout.

Needs Git, Python 3 and the official Android platform tools (`adb`, `fastboot`), on `PATH` or given
through the documented executable option:

```sh
git --version
python3 --version
adb --version
fastboot --version
```

- Host-side fixtures need no full OS checkout or device.
- Full Android builds run on Linux from a `repo` checkout of the [DiamaneOS
  manifest](https://github.com/DiamaneOS/platform_manifest), which includes these tools at
  `tools/diamaneos`. Run that copy.
- The steps are in [BUILDING.md](docs/BUILDING.md): `repo sync`, `build vendor <codename>`, `lunch` and `m`,
  then `build package`, or `tools/diamaneos/bin/diamaneos build all` in one command.

## Documentation

[docs/README.md](docs/README.md) lists every document and who it is for. To start:

- [Build DiamaneOS](docs/BUILDING.md), then the [build reference](docs/BUILD.md)
- [Signing](docs/SIGNING.md) and [testing](docs/TESTING.md)
- [Contribution and public-data rules](CONTRIBUTING.md)
- [Fork and upstream registry](config/forks.json): the repositories DiamaneOS forks and the pinned
  upstream inputs ([sources and upstream tracking](docs/SOURCES.md))
- [Verified stock factory packages](config/stock-inputs.json)
- Fairphone 6 (FP6) profile: [vendor files](docs/FP6-VENDOR.md), [firmware](docs/FIRMWARE.md) and
  [kernel](docs/FP6-KERNEL.md)
