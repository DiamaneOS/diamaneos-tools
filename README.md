# diamaneos-tools

Host tooling and machine-readable project maps for DiamaneOS, a GrapheneOS-derived operating system for Fairphone 6.

## Workspace and tools

Clone into any directory. Commands in this repository are run from its root unless stated otherwise. `WORK_ROOT` in repository maps is a configurable parent of related checkouts, not a required path on a maintainer's computer. `TOOLS_ROOT` is this checkout; `PRIVATE_ROOT` is a caller-selected directory outside public repositories for raw device evidence. `OFFLINE_ROOT` refers to isolated release-signing storage and is not a development checkout.

Use Git, Python 3 and the official Android platform tools (`adb` and `fastboot`) for host capture. Put tools on `PATH` or supply the documented executable option. Full Android builds use the reproducible Linux build environment; running host-side fixtures does not require a full OS checkout or device.

```sh
git --version
python3 --version
adb --version
fastboot --version
```

## Documentation

- [Testing and command setup](docs/TESTING.md)
- [Contribution and public-data rules](CONTRIBUTING.md)
- [Repository map](config/repositories.json)
- [Threat model and product boundaries](docs/THREAT_MODEL.md)

Only implemented commands can be run. Planned features and unresolved evidence are identified in their component contracts; a planning record is not a runtime result.
