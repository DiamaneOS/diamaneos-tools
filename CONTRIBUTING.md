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
