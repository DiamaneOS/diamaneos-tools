# FP6 component decisions and artifact closure

How DiamaneOS records a decision for every Fairphone 6 (FP6) hardware
component, and how it checks that the files a build ships match those
decisions. For anyone deciding how a component is sourced, replaced or removed.

## The decision model

`config/components.json` is the authoritative FP6 component decision model. It
binds each decision to the selected EU stock build, the immutable Fairphone
source inventory in `config/fp6-sources.json`, and the pinned platform build
environment. The source inventory stays the authority for discovered source
families and investigation blockers; the component model assigns each of those
records to exactly one engineering owner instead of copying their contents.

The file's existing `components` array stays the project dependency and notice
registry. FP6 hardware decisions live in the separate `fp6_components` array,
so extending the hardware model does not change the meaning or identifiers of
the earlier records.

The model groups the known FP6 integration surface into thirteen categories.
Userspace code is kept apart from the firmware it talks to: the
remote-processor daemons and QMI/QRTR libraries (Qualcomm's messaging with the
modem and signal processors) have their own category, because replacing them
does not need the OEM signing authority that replacing firmware does.

Every category records its purpose, dependency edges, relevant interfaces,
source candidates, private bring-up boundary, current decision state, next
experiment and the conditions that invalidate the decision. A missing
category, unowned source record, unowned blocker, unknown dependency or
dependency cycle is a validation error.

## Decision states and dispositions

Investigation states are deliberately distinct from accepted public
dispositions:

| State | Meaning |
| --- | --- |
| `unknown` | The available records do not yet identify a viable path. |
| `candidate` | A source or removal path has been identified but has not passed exact-target qualification. |
| `private-bringup` | Only the explicitly listed non-source inputs are permitted, for a named development purpose. |
| `qualification-failed` | A rejected experiment, kept with its next step. |
| `accepted` | Needs qualification evidence and exactly one public disposition: `source-built`, `necessary-prebuilt`, `removed` or `research-only`. |

- A source candidate from a different stock revision or implementation stays
  reference-only until it is proven compatible with the exact selected inputs.
- Unknown components never get a public disposition by default.
- A private bring-up decision is not a public qualification and fails public
  closure.

Accepted dispositions need typed evidence classes suited to the decision:

| Disposition | Required evidence |
| --- | --- |
| `source-built` | exact source/build, interface, security, functional, maintenance and update/recovery |
| `necessary-prebuilt` | its exact allowed inventory references, plus necessity, alternative, integrity and exposure evidence |
| `removed` | absence plus functional, security and recovery |
| `research-only` | verified absence |

## Validate the decision model

Prepare the development environment as documented in `docs/BUILD.md`, then run:

```sh
.venv/bin/python bin/diamaneos components validate
```

The command is read-only. It checks both JSON schemas, all immutable input
bindings, exact ownership of source records and blockers, the dependency graph
and the evidence each state requires. Success validates the decision model
only; it does not claim that an FP6 image builds, boots or passes device
qualification.

## Validate generated artifact closure

The artifact closure is the complete list of files a build generates or
includes, each tied to its component. The later FP6 input and product pipeline
produces a closure matching `schemas/component-closure.schema.json`:

- each generated or included artifact has a relative path, SHA-256, component
  owner, source/prebuilt class, source-inventory reference and its artifact
  dependencies;
- each model component has one result, including components that are absent.

Validate a generated closure for private development:

```sh
.venv/bin/python bin/diamaneos components validate \
  --closure /path/to/generated-component-closure.json
```

Before public promotion, require the stronger gate:

```sh
.venv/bin/python bin/diamaneos components validate \
  --closure /path/to/generated-component-closure.json \
  --public
```

The public gate rejects every unaccepted component, unmapped artifact,
undeclared dependency, source/prebuilt mismatch and artifact that contradicts an
accepted removal or research-only decision. The closure is bound to the SHA-256
of the exact decision model and to the selected stock build and region. This
technical gate is one input to the wider release process and does not replace
its other approvals.

The repository deliberately has no hand-maintained list of extracted files.
Artifact paths and hashes are generated from the build inputs; this model
supplies the reviewable decisions. When the stock build, region, source
revision, platform manifest, artifact set, dependency graph or qualification
result changes, regenerate the closure and re-evaluate every affected decision.
