# diamaneos-tools

Host tooling and machine-readable project maps for DiamaneOS.

## Project context

DiamaneOS is an operating system project under development. The current port
targets Fairphone 6 and uses GrapheneOS as its upstream OS base.

> Based on GrapheneOS. Not affiliated with or endorsed by the GrapheneOS project.

## Licence

Original DiamaneOS code, documentation and artwork in this repository are
licensed under [Apache-2.0](LICENSE), except where another licence is identified.
Third-party design assets moved with the interface design and branding to the
separate diamaneos-design repository, which carries their licence notices; see
[NOTICE](NOTICE).

The copyright licence is separate from use of the DiamaneOS name and logo as
trademarks; see section 6 of the Apache licence. Referenced upstream projects
and externally installed dependencies retain their own licences.

## Workspace and tools

Clone into any directory. Commands in this repository are run from its root unless stated otherwise. `WORK_ROOT` in repository maps is a configurable parent of related checkouts, not a required path on a maintainer's computer. `TOOLS_ROOT` is this checkout; `PRIVATE_ROOT` is a caller-selected directory outside public repositories for raw device evidence. `OFFLINE_ROOT` refers to isolated release-signing storage and is not a development checkout.

Use Git, Python 3 and the official Android platform tools (`adb` and `fastboot`) for host capture. Put tools on `PATH` or supply the documented executable option. Full Android builds run on Linux with `bin/diamaneos build all` ([BUILDING.md](docs/BUILDING.md)); running host-side fixtures does not require a full OS checkout or device.

```sh
git --version
python3 --version
adb --version
fastboot --version
```

## Documentation

- [Build DiamaneOS for the Fairphone 6](docs/BUILDING.md)
- [Build reference](docs/BUILD.md)
- [Reproduce the current generic build](docs/REPRODUCIBILITY.md)
- [Testing and command setup](docs/TESTING.md)
- [FP6 component decisions and artifact closure](docs/COMPONENTS.md)
- [FP6 firmware inventory](docs/FIRMWARE.md)
- [Resource overlay check](docs/OVERLAYS.md)
- [Contribution and public-data rules](CONTRIBUTING.md)
- [Repository map](config/repositories.json) — checkout discovery and lifecycle
  state only, not a release lock. Exact multi-repository build inputs are
  recorded by the build or release manifest that consumes them.
- [Verified stock recovery inputs](config/stock-inputs.json)
- [Threat model and product boundaries](docs/THREAT_MODEL.md)
- Interface design and branding live in the separate, private
  `diamaneos-design` repository.

Only implemented commands can be run; unresolved evidence is identified in the component contracts.
