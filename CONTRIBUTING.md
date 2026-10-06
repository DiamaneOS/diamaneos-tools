# Contributing to diamaneos-tools

GitHub is the authoritative source, issue and pull-request host; remotes are
`https://github.com/DiamaneOS/<slug>.git`. Do not create empty repos to match
the map or make a repo public before its rights/identity gate passes.

## Privacy

Never commit serials, IMEI, SIM/eSIM data, purchase prices, recovery codes,
raw diagnostics, tester contacts, custody details or production secrets/shares.
Redact before staging; `.gitignore` is a last guard, not permission.

## Public documentation and identifiers

- Write for a contributor with this repository and no private planning
  documents: behaviour, component ownership, prerequisites and unresolved
  evidence, in plain language.
- Use repository-relative links and configurable workspace placeholders; no
  maintainer home directory, private checkout paths, hostname, workstation
  inventory, account state or chat/review transcript.
- Name supported platforms and tested tool versions only when they help
  reproduce a build or result.
- Task IDs may accompany descriptive commit messages and stay in existing
  machine-readable ownership fields for compatibility: tracking metadata, not
  the explanation of a requirement. Private task mappings and completion
  ledgers stay outside public repositories. CLI help, user-visible output and
  report values describe behaviour or status without private task IDs.
- Stable file paths, schema keys, source pins and test/requirement IDs are
  technical interface; do not rename them for editorial cleanup.
- Review the whole staged diff, filenames and commit message for public
  suitability; removing text from the checkout leaves it in Git history.
- History rewriting needs explicit authorization, a private recovery copy,
  reference migration and verification; never publish backup refs or old
  objects in a cleanup.
- Preserve third-party source attribution and genuine unresolved safety
  requirements.

## Portability, snapshots and proportionality

- Host-specific public code only as a clearly labelled optional reference
  adapter behind a documented portable interface. Generic build, test and
  release entry points never silently assume a maintainer's machine model, fan
  names, device paths, network layout or directory structure.
- Machine-specific qualification results stay private unless sanitized example
  data has a clear public use.
- A qualified environment identifier is immutable. Exact upstream tags,
  commits, tool versions and file digests are reproducibility data, not values
  to refresh in place; advancing an input creates a new identifier and repeats
  the affected qualification.
- One validated machine-readable authority per pin; derive scripts, paths and
  docs from it. Tests should reject duplicated authorities that drift.
- Use the smallest permanent mechanism that meets a named requirement or
  threat, with a clear owner, failure mode, verification and reusable boundary.
  One-off diagnosis, deployment glue and raw evidence stay outside this
  repository, not in public framework code.
- Security boundaries and reproducibility checks are not simplification
  targets.
- Shared mechanisms (bounded processes, strict JSON loading) live in their small
  owning modules. Domain workflows stay direct; no general workflow engine for
  similar-looking steps.
- A fix spanning provenance, locking or process completion needs a test of the
  composed acceptance boundary; passing helper tests do not prove the caller's
  end-to-end result.

## Device security policy

- Keep SELinux enforcing and native neverallow checks on.
- Each device-specific permission names the selected implementation, the
  operation, the labelled resource or IPC endpoint and the domain needing
  access. Prefer that service domain to a HAL-wide attribute that can also hold
  passthrough clients. Review effective compiled permissions, inherited grants
  included.
- Never import stock policy wholesale, apply denial-to-allow output blindly,
  add unused services to satisfy policy references, or weaken checks to make a
  build pass.
- Removing a permission does not prove a feature still works: verify the
  affected startup, IPC and hardware behaviour. Keep untested behaviour
  explicit until native and device checks provide the evidence.
- Check production `user` policy separately; it needs zero permissive domains.
  Record inherited development-only exceptions in `userdebug` qualification;
  they are no production acceptance or permission to make hardware domains
  permissive. Policy compilation proves neither least privilege nor runtime
  function.
- Omit unnecessary components. Prefer maintained source to opaque prebuilts
  when interfaces, security properties and device behaviour can be verified;
  keep required functionality and name a maintenance owner (source
  availability alone is not enough).
- Keep necessary temporary prebuilts pinned, with consumers and replacement
  conditions in the component inventory.

## Upstream licences

- Every open-source component forked, copied, modified, linked, packaged or
  redistributed keeps its licence and copyright notices.
- Pin the exact upstream revision, inspect per-file SPDX/licence declarations
  and record downstream patches.
- Generate the notice and corresponding-source inventory from the files the
  build consumes; a public repository, another ROM's use or a top-level licence
  file is not sufficient evidence.
- Do not merge or release code whose licence is unknown, incompatible with the
  intended use, or carries an unmet attribution, notice, corresponding-source,
  relinking or Installation Information obligation.

## Project identity and upstream attribution

- DiamaneOS is the product identity. Repository descriptions state the
  component's purpose without making the current upstream base or device part
  of the permanent product name or tagline.
- Record the current base and development status in the project overview and
  relevant source, build, compatibility and device docs, and update them when
  they change; historical attribution stays accurate for the work it describes.
- Follow the [GrapheneOS branding guidance](https://grapheneos.org/faq#trademarks):
  never present DiamaneOS as GrapheneOS itself, an official GrapheneOS port or
  merely an unofficial GrapheneOS build. Source lineage alone establishes no
  equivalent security or inherited certification.
- Keep accurate upstream names, source links, technical identifiers and
  required copyright/licence notices.
- A project overview or distribution page describing the GrapheneOS-based OS
  makes the separate identity clear, for example: "DiamaneOS is based on
  GrapheneOS. It is not made or endorsed by GrapheneOS or Fairphone." Component
  READMEs and technical docs repeat it only if their presentation could suggest
  affiliation.
- Commit messages name the upstream or device when relevant; they need no
  tagline or disclaimer.

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
keeping the parent acceptance criteria. Source/format-only changes get
review/validation, not ritual tests.

## Branches and repositories

- `main` for tools and standalone services; `android17` for every OS
  repository (manifest, device, shared product and kernel repositories).
- Preserve upstream history in forks; keep commits signed.
- Keep independent Git backups; ROM images and build evidence stay outside
  source Git.
- Repository discovery: `config/repositories.json`; exact build pins: the
  manifest and environment records.

## Roles and access

- Release signing authority: the designated release maintainer. A second
  maintainer builds and reviews independently, without signing authority.
- A US support claim needs US-region evidence
  ([TESTING.md](docs/TESTING.md#regional-fp6-qualification)).
- Routine administration uses individually assigned credentials and MFA.
  Production signing tokens never attach to a development workstation after
  the ceremony and never serve as routine MFA.
- Account recovery details stay private (`PRIVATE_ROOT`); public records hold
  roles only, never codes/secrets. Credential changes need the responsible
  operator.

## Translations and accessibility

- Source locale: English (complete). A locale is advertised only after its
  critical flows are reviewed; AI-assisted locales are never advertised as
  fully reviewed.
- Low-risk AI drafts are allowed after structural checks. Critical wording
  (credentials, permissions, erase, updates, recovery, installation) ships only
  after fluent review, otherwise with a disclosed source-language fallback for
  that flow.
- Suggest via Git (resource/docs paths, review branches only), reporting
  app/screen, locale and affected text. No screenshot upload or personal
  content by default; no contributor personal-data or disability disclosure
  required. The translation path cannot touch code, keys or production
  branches.
- Accessibility reports are welcome in the same format; assistive checks are
  manual, on device, never proven by scanners alone.
