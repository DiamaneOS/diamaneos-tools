# Contributing to diamaneos-tools

Local-first. Remote is `https://codeberg.org/DiamaneOS/<slug>.git`.
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

- Release signing authority: designated release maintainer. Second maintainer: independent
  builder/reviewer (device + own build server), no signing authority.
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
