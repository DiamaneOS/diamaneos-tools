# FP6 component decisions and artifact closure

`config/components.json` is the authoritative FP6 component decision model.
It binds each decision to the selected EU stock build, the immutable Fairphone
source inventory in `config/fp6-sources.json`, and the pinned platform build
environment. The source inventory remains the authority for discovered source
families and investigation blockers; the component model assigns each of
those records to exactly one engineering owner instead of copying their
contents into another list.

The existing `components` array remains the project dependency and notice
registry. FP6 hardware decisions live in the separate `fp6_components` array,
so extending the hardware model does not change the meaning or identifiers of
the earlier records.

The model groups the known FP6 integration surface into twelve categories.
Every category records its purpose, dependency edges, relevant interfaces,
source candidates, private bring-up boundary, present decision state, next
experiment and conditions that invalidate the decision. A missing category,
unowned source record, unowned blocker, unknown dependency or dependency cycle
is a validation error.

## Decision states and dispositions

Investigation states are deliberately distinct from accepted public
dispositions:

- `unknown` means the available records do not yet identify a viable path;
- `candidate` means a source or removal path has been identified but has not
  passed exact-target qualification;
- `private-bringup` permits only the explicitly listed non-source inputs for a
  named development purpose;
- `qualification-failed` retains a rejected experiment and its next step; and
- `accepted` requires qualification evidence and exactly one public
  disposition: `source-built`, `necessary-prebuilt`, `removed` or
  `research-only`.

A source candidate from a different stock revision or implementation remains
reference-only until it is proven compatible with the exact selected inputs.
Unknown components never acquire a public disposition by default. A private
bring-up decision is not a public qualification and fails public closure.
Accepted dispositions require typed evidence classes appropriate to the
decision. Source-built decisions require exact source/build, interface,
security, functional, maintenance and update/recovery evidence. A necessary
prebuilt additionally names its exact allowed inventory references and requires
necessity, alternative, integrity and exposure evidence. Removal requires
absence plus functional, security and recovery evidence; research-only requires
verified absence.

## Validate the decision model

Prepare the development environment as documented in `docs/BUILD.md`, then run:

```sh
.venv/bin/python bin/diamaneos components validate
```

The command is read-only. It checks both JSON schemas, all immutable input
bindings, exact ownership of source records and blockers, the dependency graph,
and the evidence required by each state. A successful result validates the
decision model only; it does not claim that an FP6 image builds, boots or passes
device qualification.

## Validate generated artifact closure

The later FP6 input and product pipeline produces an artifact closure matching
`schemas/component-closure.schema.json`. Each generated or included artifact
must have a relative path, SHA-256, component owner, source/prebuilt class,
source-inventory reference and its artifact dependencies. Each model component
must also have one result, including components which are absent.

Validate a generated closure for private development with:

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
undeclared dependency, source/prebuilt mismatch and artifact that contradicts
an accepted removal or research-only decision. The closure is bound to the
SHA-256 of the exact decision model and to the selected stock build and region.
This technical gate remains one input to the broader release process and does
not replace its other approvals.

The repository intentionally does not carry a hand-maintained extracted-file
list. Artifact paths and hashes are generated from the build inputs, while this
model supplies reviewable decisions. When the stock build, region, source
revision, platform manifest, artifact set, dependency graph or qualification
result changes, regenerate the closure and re-evaluate every affected decision.
