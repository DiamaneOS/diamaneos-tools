# Contributing to diamaneos-tools

Local-first. Remote is `https://codeberg.org/DiamaneOS/<slug>.git`.
Do not create empty repos to match the map; do not set public until the
rights/identity gate for that repo passes. Full plan stays out of git.

## Privacy

Never commit: serials, IMEI, SIM/eSIM data, purchase prices, recovery codes,
raw diagnostics, tester contacts, custody details, production secrets/shares.
Redact before staging. `.gitignore` is a last guard, not permission.

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
preserving parent AC. Source/format-only changes use review/validation, not ritual tests.

## Roles and access

- Release signing authority: designated release maintainer. Second maintainer: independent
  builder/reviewer (device + own build server), no signing authority.
- Routine administration uses individually assigned credentials
  and MFA; production signing tokens never attach to a development workstation
  after the ceremony and never serve as routine MFA.
- Account recovery details stay private (`PRIVATE_ROOT`); public records hold
  roles only, never codes/secrets. Credential changes require the responsible operator.

## Translations and accessibility (localization and accessibility)

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
  checks happen on device (accessible journey validation), never proven by scanners alone.
