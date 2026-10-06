# diamaneos-tools

Host tooling and machine-readable project maps for DiamaneOS.

## Project context

DiamaneOS is an operating system project under development for the Fairphone 6,
with GrapheneOS as its upstream OS base.

> DiamaneOS is based on GrapheneOS. It is not made or endorsed by GrapheneOS or
> Fairphone.

## Licence

- Original DiamaneOS code, documentation and artwork here are
  [Apache-2.0](LICENSE) unless another licence is identified.
- Third-party design assets are not in this repository: they live with the
  interface design and branding sources, which carry their licence notices and
  are not public ([NOTICE](NOTICE)).
- The copyright licence does not cover the DiamaneOS name and logo as
  trademarks (Apache licence, section 6).
- Referenced upstream projects and externally installed dependencies keep their
  own licences.

## Workspace and tools

Clone into any directory; run commands from the repository root unless stated
otherwise.

- `WORK_ROOT` in repository maps: a configurable parent of related checkouts,
  not a required path.
- `TOOLS_ROOT`: this checkout.
- `PRIVATE_ROOT`: a caller-selected directory outside public repositories for
  private records.
- `OFFLINE_ROOT`: isolated release-signing storage, not a development checkout.

Needs Git, Python 3 and the official Android platform tools (`adb`, `fastboot`),
on `PATH` or given through the documented executable option:

```sh
git --version
python3 --version
adb --version
fastboot --version
```

Host-side fixtures need no full OS checkout or device. Full Android builds run
on Linux from a `repo` checkout of the
[DiamaneOS manifest](https://github.com/DiamaneOS/platform_manifest), which
includes these tools at `tools/diamaneos`: `tools/diamaneos/bin/diamaneos build
all` ([BUILDING.md](docs/BUILDING.md)).

## Documentation

- [Build DiamaneOS for the Fairphone 6](docs/BUILDING.md)
- [Build reference](docs/BUILD.md)
- [Testing and command setup](docs/TESTING.md)
- [FP6 firmware inventory](docs/FIRMWARE.md)
- [Resource overlay check](docs/OVERLAYS.md)
- [Contribution and public-data rules](CONTRIBUTING.md)
- [Repository map](config/repositories.json): checkout discovery and lifecycle
  state only, not a release lock; the consuming build or release manifest
  records exact multi-repository build inputs.
- [Verified stock recovery inputs](config/stock-inputs.json)
- [Threat model and product boundaries](docs/THREAT_MODEL.md)
- Interface design and branding sources are not public.

Only implemented commands can be run; the component contracts identify
unresolved evidence.
