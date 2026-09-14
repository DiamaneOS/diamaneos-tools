# Facet comparison verification — 13 September 2026

Historical status at comparison completion: ready for direction review.
The owner subsequently selected Lumen; see [the extension record](2026-09-13-lumen-journeys.md).

## Preservation and reproducibility

- Original `design/prototypes/diamaneos-ui.html` is byte-identical to commit
  `49c92b9abe12f32f2f8dc95086e7bf276d99ac06`.
- Original HTML SHA-256:
  `c46317e4a4880736ca1a66fa5e9177279e46c0ab2280c8f87e26691c02d5593a`.
- Baseline review wrapper exposes the original preview hooks without editing
  the original study. Both baseline and comparison remain available locally.
- `node --check` passed for the comparison JavaScript and baseline control
  adapter. `git diff --check` passed. No new dependencies were installed.
- The comparison uses the already bundled Lucide assets. Sample interaction
  state is in memory and resets on reload. It does not contact a device/service.

To run from the tools repository:

```sh
python3 -m http.server 8766 --bind 127.0.0.1
```

Open `http://127.0.0.1:8766/design/prototypes/facet-directions.html`.
The review session left that loopback server running with PID `61967`.
Its log is `/private/tmp/facet-preview-server.log` and its PID file is
`/private/tmp/facet-preview-server.pid`. Stop this session's server with
`kill 61967` after confirming that PID still identifies this server.

## Direct browser observations

The in-app browser was used to render and interact with the prototypes.
Screenshots were inspected in the conversation; no screenshot bundle was
saved to the repository. Findings below are browser observations, not results
from Android or recruited participants.

| Area | Exercised behavior and result |
| --- | --- |
| Baseline six journeys | Setup reveal/practice, Home drawer/search, privacy preview/apply, notification diagnosis/enable/test, battery requested/confirmed limit, and update download/verification-error states were exercised. Supporting Settings, Files, and battery health screens were inspected. See the review for concrete findings. |
| New Home, shade, Settings | All three compositions rendered and were inspected. Each phone navigates independently. Search, app entry, Settings categories, Back, Home, and Recents are connected to sample views. |
| Settings context | Searching Battery opened the matching setting. After changing the requested limit from 80% to 85%, the confirmed value stayed at 80% until Apply. Back restored the search query. In the final pass, a 133 px Settings scroll offset survived theme change, detail navigation, Back, and shade opening/dismissal. |
| Shade interactions | Wi-Fi toggling persisted; notification dismissal followed by Undo restored Maya's message. The final pointer drag check opened Lumen's shade downward and dismissed it upward to Home. |
| Error and empty states | Connection-error Retry removed the error while preserving sample settings. Empty notifications showed the empty-state copy. Baseline search and updater failures were also inspected. |
| Light and dark | Home, shade, and Settings were reviewed in paired themes. Final dark Settings showed distinct hierarchy in all three proposals. |
| Enlarged/longer text | 200% German draft Settings and RTL error states had no horizontal screen overflow in the first pass. Final 390 px browser / 200% German Home measured 360 px content and viewport widths for all three phones, with no page overflow. Phone outer heights remained 760 px. Lumen's enlarged app drawer kept the long Einstellungen label readable. |
| Target sizes | Final normal shades had no visible phone buttons below 48 × 48 CSS px. The hidden toast Close button was correctly excluded from this claim. Brightness percentages remained on one line. This is CSS geometry, not an Android touch-target measurement. |
| Reduced motion | The control disabled CSS transitions and skipped the JavaScript motion path. Opening and closing the shade restored Settings with no residual animated overlay. Native motion settings and actual device behavior remain untested. |
| Browser errors | No warning/error console entries were returned in the final interaction pass. |

The first rendered pass found and the correction pass addressed: percentage
wrapping, small dismiss/navigation targets, the mixed gesture-pill/button
navigation treatment, narrow large-text Lumen overflow, scroll resets, and
shade motion direction. These were targeted corrections, not an additional
unbounded redesign round.

## Contrast confirmation

The baseline CLI detector returned `[]`. The comparison's single detector run
found two low-contrast host text colours; both were darkened. A separate
calculation found two light-theme muted-on-field combinations below 4.5:1;
those were also darkened.

Final WCAG relative-luminance calculations covered ink/background,
ink/surface, muted/background, muted/surface, muted/field, on/accent,
hero-ink/hero, and error/error-background for each of the six role sets:

| Role set | Lowest tested text-pair ratio |
| --- | --- |
| Still light | 4.99:1 |
| Lumen light | 4.71:1 |
| Contour light | 5.08:1 |
| Still dark | 6.44:1 |
| Lumen dark | 6.45:1 |
| Contour dark | 5.00:1 |

This is a scoped token-pair check, not a complete accessibility audit. It does
not establish contrast for every focus ring, icon, overlap, or animated frame.

## Review limits and native follow-through

Both spawned assessment workers became unavailable before returning a review.
The completed critique and final confirmation were performed directly in one
context; no independent review approval is claimed. No detector overlay was
injected because page evaluation was read-only.

Before adoption, validate the chosen direction with ordinary Android users,
then on the target device for touch gesture arbitration, predictive Back,
keyboard/IME insets, system bars, screen cutouts, TalkBack, Switch Access,
native font scaling, haptics, frame times, high refresh rates, and service
acknowledgement/failure behavior. Browser pointer drags and CSS sizes do not
prove any of these. German remains draft copy; RTL is English layout stress.

The setup policy check was completed in the follow-up below. Keep
requested/confirmed state and scoped recovery in the selected direction.

Follow-up correction: the credential-policy task explicitly preserves biometric
and strong-auth behavior. The initial fingerprint/passphrase conflict finding
was withdrawn; [Lumen](../prototypes/facet-lumen.html) clarifies required
passphrase entry without removing supported biometric use.
