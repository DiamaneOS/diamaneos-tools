# FP6 component decisions and artifact closure

How each Fairphone 6 (FP6) hardware component's decision is recorded, and how
a build's shipped files are checked against those decisions.

`config/components.json` is the authoritative FP6 component decision model,
bound to the selected EU stock build, the immutable Fairphone source inventory
`config/fp6-sources.json` and the pinned platform build environment. The
source inventory stays the authority for source families and investigation
blockers; the model assigns each record to exactly one engineering owner
without copying it. Hardware decisions live in the `fp6_components` array; the
older `components` array stays the dependency and notice registry, with its
meanings and identifiers unchanged.

The model has thirteen categories. Remote-processor daemons and QMI/QRTR
libraries (Qualcomm's messaging with the modem and DSPs) get their own category,
apart from the firmware they talk to, because replacing them needs no OEM
signing authority. Each category records its purpose, dependency edges,
interfaces, source candidates, private bring-up boundary, decision state, next
experiment and invalidating conditions. A missing category, unowned source
record or blocker, unknown dependency or dependency cycle fails validation.

## States and dispositions

| State | Meaning |
| --- | --- |
| `unknown` | No viable path identified yet. |
| `candidate` | A source or removal path is identified but not qualified on the exact target. |
| `private-bringup` | Only the listed non-source inputs, for a named development purpose. |
| `qualification-failed` | A rejected experiment, kept with its next step. |
| `accepted` | Qualification evidence and exactly one public disposition: `source-built`, `necessary-prebuilt`, `removed` or `research-only`. |

A source candidate from another stock revision or implementation stays
reference-only until proven compatible with the exact selected inputs. Unknown
components never get a public disposition by default, and a private bring-up
decision fails public closure. Evidence per disposition: `source-built` needs
exact source/build, interface, security, functional, maintenance and
update/recovery evidence; `necessary-prebuilt` names its exact allowed inventory
references and needs necessity, alternative, integrity and exposure evidence;
`removed` needs absence plus functional, security and recovery evidence;
`research-only` needs verified absence.

## Validate

With the development environment from `docs/BUILD.md`:

```sh
.venv/bin/python bin/diamaneos components validate
```

This read-only check covers both JSON schemas, all immutable input bindings,
exact ownership of source records and blockers, the dependency graph and each
state's evidence. It validates the model only, not that an FP6 image builds,
boots or qualifies.

The later FP6 pipeline produces an artifact closure (every generated or
included file, tied to its component) matching
`schemas/component-closure.schema.json`: each artifact has a relative path,
SHA-256, component owner, source/prebuilt class, source-inventory reference and
artifact dependencies, and every model component has one result, absent ones
included. Validate it for private development, and with `--public` before
public promotion:

```sh
.venv/bin/python bin/diamaneos components validate \
  --closure /path/to/generated-component-closure.json
```

```sh
.venv/bin/python bin/diamaneos components validate \
  --closure /path/to/generated-component-closure.json \
  --public
```

The public gate rejects every unaccepted component, unmapped artifact,
undeclared dependency, source/prebuilt mismatch and artifact contradicting an
accepted removal or research-only decision. The closure is bound to the
decision model's SHA-256 and the selected stock build and region. It is one
input to the release process, not a replacement for its other approvals.

There is deliberately no hand-maintained extracted-file list: paths and hashes
are generated from build inputs, and the model supplies reviewable decisions.
When the stock build, region, source revision, platform manifest, artifact set,
dependency graph or qualification result changes, regenerate the closure and
re-evaluate every affected decision.
