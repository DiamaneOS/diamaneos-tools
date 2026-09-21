# Contributing to diamaneos-tools

GitHub is the authoritative source, issue and pull-request host. Remote is `https://github.com/DiamaneOS/<slug>.git`.
Do not create empty repos to match the map; do not set public until the
rights/identity gate for that repo passes. Full plan stays out of git.

## Privacy

Never commit: serials, IMEI, SIM/eSIM data, purchase prices, recovery codes,
raw diagnostics, tester contacts, custody details, production secrets/shares.
Redact before staging. `.gitignore` is a last guard, not permission.

## Public documentation and identifiers

Write for a contributor who has this repository and no private planning documents. Describe behavior, component ownership, prerequisites and unresolved evidence in plain language. Use repository-relative file links and configurable workspace placeholders. Do not include a maintainer's home directory, private checkout paths, hostname, workstation inventory, account state or chat/review transcript. Document supported platforms and relevant tested tool versions only when they help reproduce a build or result.

Task IDs may accompany descriptive commit messages and remain in existing machine-readable ownership fields for compatibility. Such fields are tracking metadata, not the explanation of a requirement. Keep private task mappings and completion ledgers outside public repositories. CLI help, user-visible output and report values should describe behavior or status without private task IDs. Stable file paths, schema keys, source pins and test/requirement IDs remain part of the technical interface; do not rename them merely for editorial cleanup.

Review the entire staged diff, filenames and commit message for public suitability. Removing text from the current checkout does not remove it from Git history. History rewriting requires explicit authorization, a private recovery copy, reference migration and verification; never publish backup refs or old objects as part of cleanup. Preserve third-party source attribution and genuine unresolved safety requirements.

## Portability, snapshots and proportionality

Public host-specific code is acceptable only when it is clearly labelled as an
optional reference adapter behind a documented portable interface. Generic
build, test and release entry points must not silently assume a maintainer's
machine model, fan names, device paths, network layout or local directory
structure. Keep qualification results for a particular machine private unless
sanitized example data has a clear public use.

A qualified environment identifier is immutable. Exact upstream tags, commits,
tool versions and file digests are reproducibility data, not values to refresh
in place. Advancing an input creates a new environment identifier and repeats
the affected qualification. Prefer one validated machine-readable authority
for each pin and derive scripts, paths and documentation from it; tests should
reject duplicated authorities that drift.

Use the smallest permanent mechanism that satisfies a named requirement or
threat. It needs a clear owner, failure mode, verification and reusable
boundary. Keep one-off diagnosis, deployment glue and raw evidence in the
private runbook rather than turning them into public framework code. Security
boundaries and reproducibility checks are not optional simplification targets.

## Device security policy

Keep SELinux enforcing and native neverallow checks enabled. Each device-specific
permission must identify the selected implementation, the operation it performs,
the labelled resource or IPC endpoint, and the domain that needs access. Prefer
that service domain to a HAL-wide attribute that can also contain passthrough
clients. Review effective compiled permissions, including inherited grants.

Do not import stock policy wholesale, apply denial-to-allow output blindly, add
unused services to satisfy policy references, or weaken checks to make a build
pass. Removing a permission does not establish that a feature still works:
verify the affected startup, IPC and hardware behavior. Keep untested behavior
explicit until native and device checks provide the required evidence.

Check production `user` policy separately and require zero permissive domains.
Record any inherited development-only exception in `userdebug` qualification;
it is not production acceptance or permission to make hardware domains
permissive. A successful policy compilation proves neither least privilege nor
runtime functionality.

Reduce attack surface by omitting unnecessary components. Prefer maintained
source implementations over opaque prebuilts when their interfaces, security
properties and device behavior can be verified. Preserve required functionality
and identify a maintenance owner; source availability alone is not sufficient.
Keep necessary temporary prebuilts pinned and record their consumers and
replacement conditions in the component inventory.

## Upstream licences

Every open-source component that is forked, copied, modified, linked, packaged
or redistributed must retain its applicable licence and copyright notices.
Pin the exact upstream revision, inspect per-file SPDX/licence declarations and
record downstream patches. Generate the notice and corresponding-source
inventory from the files actually consumed by the build; a public repository,
another ROM's use or a top-level licence file is not sufficient evidence. Do
not merge or release affected code while its licence is unknown, incompatible
with the intended use, or has an unmet attribution, notice,
corresponding-source, relinking or Installation Information obligation.

## Project identity and upstream attribution

Use DiamaneOS as the product identity. General repository descriptions explain
the component's purpose without making the current upstream base or device
target part of the permanent product name or tagline. Record the current base,
development status and device scope in the project overview and relevant
source, build, compatibility and device documentation. A future change updates
those current-scope records; historical attribution remains accurate for the
work it describes.

Follow the [GrapheneOS branding guidance](https://grapheneos.org/faq#trademarks):
do not present DiamaneOS as GrapheneOS itself, an official GrapheneOS port or
merely an unofficial GrapheneOS build. Source lineage alone does not establish
equivalent security or inherited certification.

Keep accurate upstream names, source links, technical identifiers and required
copyright/licence notices. Where a project overview or distribution page
describes the GrapheneOS-based OS, make its separate identity clear, for example:
"Based on GrapheneOS. Not affiliated with or endorsed by the GrapheneOS project."
Component READMEs and technical documents do not need to repeat that notice
unless their presentation could suggest affiliation. Commit messages name the
upstream or device when relevant to the change; no stock tagline or disclaimer
is required in each commit.

## Issue / PR handoff (one note per task)

```text
Descriptive issue/PR title and current outcome (task ID optional):
Repository names, commits and repository-relative target files:
Accepted prerequisites; selected design contract and resolved values:
Implementation change and affected trust/data/lifecycle boundaries:
Checks actually run, expected/observed results and evidence:
Unrun mandatory checks or unresolved questions:
Public source/docs/evidence changed; private data excluded:
Automation job impact and human action still required:
Next implementation step or unblock condition:
```

One active implementation per person; split oversized work into named children
preserving the parent acceptance criteria. Source/format-only changes use review/validation, not ritual tests.

## Roles and access

- Release signing authority: designated release maintainer. Second maintainer:
  independent builder/reviewer in the US (own build server and a planned
  US-region FP6), no signing authority. A US support claim requires evidence
  from the actual regional device once available; an EU-device result or
  matching version label is not a substitute.
- Routine administration uses individually assigned credentials
  and MFA; production signing tokens never attach to a development workstation
  after the ceremony and never serve as routine MFA.
- Account recovery details stay private (`PRIVATE_ROOT`); public records hold
  roles only, never codes/secrets. Credential changes require the responsible operator.

## Translations and accessibility

- Source locale English (complete); German first priority — advertised only
  after critical flows are reviewed. Other locales AI-assisted, never
  advertised as fully reviewed. Low-risk AI drafts allowed after structural
  checks; critical wording (credentials, permissions, erase, updates,
  recovery, installation) ships only after fluent review, else disclosed
  source-language fallback for that flow.
- Suggest via Git (resource/docs paths, review branches only); report format:
  app/screen, locale, affected text. No screenshot upload or personal content
  by default; no contributor personal-data or disability disclosure required.
  Translation path cannot touch code, keys, or production branches.
- Accessibility issue reports welcome with the same format; manual assistive
  checks happen on device, never proven by scanners alone.

Keep shared process, evidence and device mechanisms in their small owning
modules. Keep domain workflows direct; do not introduce a general workflow
engine for similar-looking steps. Test the composed acceptance boundary when a
fix spans provenance, locking or process completion. Passing individual helper
tests does not establish the caller's end-to-end result.

Use `main` for tools and standalone services, `android17` for the current
manifest/device/shared product line, and `android14-6.1` for the current
Fairphone kernel forks. Preserve upstream history and signed commits. Keep
independent Git backups; ROM images and build evidence belong outside source
Git. Repository discovery is in `config/repositories.json`; exact build pins
remain in the manifest and environment records.
