# Contributing to diamaneos-tools

GitHub is the source, issue and pull-request host; remotes are
`https://github.com/DiamaneOS/<slug>.git`.

## Privacy

Never commit serials, IMEI, SIM/eSIM data, purchase prices, recovery codes, raw diagnostics, tester
contacts, custody details or production secrets/shares. Redact before staging; `.gitignore` is a
last guard, not permission.

## Public documentation and identifiers

- Write for a reader who has only this repository: behaviour, component ownership and
  prerequisites, in plain language.
- Describe the current state: no status or history notes, plans or dates.
- Point at the config file or command that holds a release name, tag, commit or count instead of
  repeating the value; mark a literal value in a command line as an example.
- Use repository-relative links and configurable workspace placeholders; no home directory, private
  checkout path, hostname, workstation inventory, account state or chat/review transcript.
- Name supported platforms and tested tool versions only when they help reproduce a build or result.
- Keep every list item and paragraph to 45 words at most; split longer text into sub-bullets
  instead of extending it (`tests/docs/test_doc_length.py` enforces this).
- CLI help, user-visible output and report values describe behaviour, without internal identifiers.
- Stable file paths, schema keys, source pins and test/requirement IDs are technical interface; do
  not rename them for editorial cleanup.
- Review the whole staged diff, filenames and commit message for public suitability; removing text
  from the checkout leaves it in Git history.
- Preserve third-party source attribution and genuine unresolved safety requirements.

## Portability, pins and proportionality

- Host-specific public code only as a clearly labelled optional reference adapter behind a
  documented portable interface.
- Generic build, test and release entry points never silently assume one machine's model, fan
  names, device paths, network layout or directory structure.
- Machine-specific results stay out of this repository unless sanitized example data has a clear
  use.
- Exact upstream tags, commits, tool versions and file digests are reproducibility data: change one
  only together with the input it pins.
- One validated machine-readable authority per pin; derive scripts, paths and docs from it. Tests
  should reject duplicated authorities that drift.
- Use the smallest permanent mechanism that meets a named requirement or threat, with a clear
  owning component, failure mode, verification and reusable boundary.
- One-off diagnosis, deployment glue and raw evidence stay outside this repository.
- Security boundaries and reproducibility checks are not simplification targets.
- Shared mechanisms (bounded processes, strict JSON loading) live in their small owning modules.
  Domain workflows stay direct; no general workflow engine for similar-looking steps.
- A fix spanning provenance, locking or process completion needs a test of the composed acceptance
  boundary; passing helper tests do not prove the caller's end-to-end result.

## Device security policy

- Keep SELinux enforcing and native neverallow checks on.
- Each device-specific permission names the selected implementation, the operation, the labelled
  resource or IPC endpoint and the domain needing access.
- Prefer that service domain to a HAL-wide attribute that can also hold passthrough clients.
- Review effective compiled permissions, inherited grants included.
- Never import stock policy wholesale, apply denial-to-allow output blindly, add unused services to
  satisfy policy references, or weaken checks to make a build pass.
- Removing a permission does not prove a feature still works: verify the affected startup, IPC and
  hardware behaviour.
- Do not present behaviour as verified without native and device checks.
- Check production `user` policy separately; it needs zero permissive domains.
- Exceptions that only `userdebug` builds inherit are no production acceptance and no permission to
  make hardware domains permissive.
- Policy compilation proves neither least privilege nor runtime function.
- Omit unnecessary components.
- Prefer maintained source to opaque prebuilts when interfaces, security properties and device
  behaviour can be verified; keep required functionality (source availability alone is not enough).
- Keep necessary prebuilts pinned by hash, each with its purpose in the recipe that selects it
  (`config/fp6-minimal/vendor-files.json`).

## Upstream licences

- Every open-source component forked, copied, modified, linked, packaged or redistributed keeps its
  licence and copyright notices.
- Pin the exact upstream revision, inspect per-file SPDX/licence declarations and record downstream
  patches.
- Generate the notice and corresponding-source inventory from the files the build consumes; a public
  repository, another ROM's use or a top-level licence file is not sufficient evidence.
- Do not merge or release code whose licence is unknown, incompatible with the intended use, or
  carries an unmet attribution, notice, corresponding-source, relinking or Installation Information
  obligation.

## Project identity and upstream attribution

- DiamaneOS is the product identity.
- Repository descriptions state the component's purpose without making the current upstream base or
  device part of the permanent product name or tagline.
- Record the current base in the project overview and the relevant source, build and device docs,
  and update them when it changes.
- Follow the [GrapheneOS branding guidance](https://grapheneos.org/faq#trademarks): never present
  DiamaneOS as GrapheneOS itself, an official GrapheneOS port or merely an unofficial GrapheneOS
  build.
- Source lineage alone establishes no equivalent security or inherited certification.
- Keep accurate upstream names, source links, technical identifiers and required copyright/licence
  notices.
- A project overview or distribution page states the separate identity as the README does: based on
  GrapheneOS, not made or endorsed by GrapheneOS or Fairphone.
- Component READMEs and technical docs repeat it only if their presentation could suggest
  affiliation.
- Commit messages name the upstream or device when relevant; they need no tagline or disclaimer.

## Pull requests

- Say what changed and which trust, data or lifecycle boundary it touches.
- List the checks you ran with their results, and the mandatory checks you did not run.
- Source or format-only changes need review and validation, not new tests.

## Branches and repositories

- `main` for tools and standalone services; `android17` for every OS repository (manifest, device,
  shared product and kernel repositories).
- Preserve upstream history in forks; keep commits signed.
- ROM images and build output stay outside source Git.
- Forks and their upstreams: `config/forks.json`; exact build pins: the manifest and environment
  records.

## Translations and accessibility

- Source locale: English. A locale is advertised only after its critical flows are reviewed;
  AI-assisted locales are never advertised as fully reviewed.
- Low-risk AI drafts are allowed after structural checks.
- Critical wording (credentials, permissions, erase, updates, recovery, installation) ships only
  after fluent review, otherwise with a disclosed source-language fallback for that flow.
- Suggest via Git (resource/docs paths, review branches only), reporting app/screen, locale and
  affected text.
- No screenshot upload or personal content by default; no contributor personal-data or disability
  disclosure required.
- The translation path cannot touch code, keys or production branches.
- Accessibility reports are welcome in the same format; assistive checks are manual, on device,
  never proven by scanners alone.
