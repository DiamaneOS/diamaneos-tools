# DiamaneOS UX — Visual System and Journey Index (interface design draft)

Direction: distinctive DiamaneOS theme (owner choice), calm, near-stock
navigation. Tokens provisional-selected here; visual integration waits on this
selection (stop condition cleared for prototyping, device proof still required
in native UI integration). Prototype intent only — not implementation or a11y certification.

## Token set (small, light/dark pairs, local assets only)

- Color: primary deep-teal `#0E5F5B`/`#7FD1C7`; accent diamond-amber `#B7791F`/
  `#F2C14E` (accents only, never sole status signal); surface `#FFFFFF`/
  `#121414`; error `#B3261E`/`#F2B8B5`; text high-contrast pairs both modes.
- Type: system stack (no bundled font cost); scale 12/14/16/20/24/32.
- Spacing: 4pt base (4/8/12/16/24/32). Shape: cards 12dp, controls 8dp;
  diamond-notch motif in headers/logo only, never on interactive controls.
- Motion: ≤150ms fade/slide clarifying state; `prefers-reduced-motion`
  respected; no ornamental animation.
- Navigation: familiar Android Back/gestures preserved; dashboards link to
  controlling settings, never duplicate state; active profile visible where it
  matters.

## Journey index (synthetic data; each file: entry/goal/path/states/Back/
completion/failure/SR-order/labels/locale/reuse)

1. `design/core-journeys/01-setup.md` → passphrase onboarding / credential-policy enforcement / accessible journey validation
2. `design/core-journeys/02-home-discovery.md` → everyday app flows / native UI integration
3. `design/core-journeys/03-app-privacy.md` → app privacy presets
4. `design/core-journeys/04-notifications.md` → notification compatibility
5. `design/core-journeys/05-battery.md` → charge-limit integration / battery health reporting
6. `design/core-journeys/06-updates.md` → OTA installation testing / interrupted-update recovery testing / offline signed OTA

## Implementation ladder (per change, recorded at implementation)

Resource/RRO → standard APIs + shared components → narrow Settings/launcher/
SystemUI patch (prototype benefit + owner + measured rebase + regressions) →
broad shell rewrite only via separate architecture decision. Shared visuals
never combine authorities (threat-model boundary). No security scores, no
technical dashboard blocking ordinary use . Resource-vs-patch mapping per
journey lives in its file .
