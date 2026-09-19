⚠️ DEGRADED: single-context (both spawned assessment workers became unavailable with `not_found`; the rendered review continued directly).

# Facet review — 13 September 2026

**Recommendation: compare a new direction for Home, the shade, and Settings before extending it to the remaining journeys. Lumen is the strongest starting candidate.** The existing study has useful state and recovery design, but its visual hierarchy is repetitive and some everyday entry points need work. This is an expert design judgment, not evidence from intended users.

## Follow-up correction and selection

The project subsequently selected Lumen for the six-journey extension.
[Current reference](../prototypes/facet-lumen.html).

The original fingerprint finding below was too strong. On reviewing the
credential-policy task (FP6-071), it explicitly preserves biometric and
strong-auth semantics. Passphrase-only normal credentials do not categorically
prohibit optional supported fingerprint use. Lumen clarifies when the passphrase
is required; it does not remove biometrics or change policy.

## Evidence and scope

Reviewed the running original `design/prototypes/diamaneos-ui.html`, backed by
commit `7d797d3cf42f04ca1574e85d66a32acfec0abba7`, through the in-app browser.
The original HTML remains unchanged. The companion `baseline-review.html`
exposes the original study's existing review hooks for theme, text, language,
motion, and scenario selection. Its adapter changes preview controls only.

The initial CLI detector artifact returned `[]`. This did not establish good
usability or verify the design's native behavior. No live detector overlay was
injected: the available page evaluation API is read-only. Browser interactions
used the supported accessibility and Playwright locator APIs.

## What works

- **Permission changes have understandable consequences.** Meadow's Untrusted
  preview names Network changing from Allowed to Blocked, identifies the Personal
  profile, and shows the other capabilities as unchanged. Applying it returned
  to the app page with Network Blocked and a confirmation.
- **Requested state is distinct from confirmed state.** Moving the battery
  slider from 80% to 85% kept the confirmed value at 80% until Apply. This is a
  valuable pattern to preserve in any visual direction.
- **Recovery copy is specific.** The updater's damaged-download state names
  verification failure, says the current build remains active, and offers a
  fresh download. Notification diagnosis scopes the action to Meadow in the
  Personal profile. These are stronger foundations than generic error banners.

## Priority findings

| Priority | Observed example | Why it matters | Proposed response |
| --- | --- | --- | --- |
| Corrected | Setup says “Fingerprint unlock is optional later.” | This is not inherently inconsistent with passphrase-only primary credentials; FP6-071 preserves biometric/strong-auth semantics. The original P1 finding is withdrawn. | Explain required passphrase entry after restart/strong-auth events and qualify fingerprint support. Lumen now does this. |
| P2 | Home shows Files and Settings as both large tiles and smaller app icons. Search is only exposed by an unlabelled pull-down interaction or All apps → Search. Settings itself has no search field. | Duplicate destinations consume space while discovery requires extra learning or navigation. | Give search a visible entry, retain recognisable apps, and test searchable Settings. All three concepts do this. |
| P2 | Setup's “Your space,” Privacy's preset, Battery's percentage, and the update identity all occupy large, similarly shaped accent panels. | Different tasks get similar emphasis. In Battery, the actionable limit control follows a dominant percentage panel. | Vary density and hierarchy by task. Carry a shared visual language without assigning every task the same hero panel. |
| P2 | Opening Change preset from Standard initially marks Untrusted as selected. | The selection can be mistaken for the current setting, even though nothing has applied yet. | Initialize the choice from the current preset, or explicitly separate current and proposed choices. Preserve the consequence preview. |
| P2 | At 200% text with German draft labels, the baseline Privacy phone measured 390 × 1386.57 CSS px. | The composition avoids horizontal clipping by growing the phone, leaving reachability and fixed-screen scrolling unproven. This is a documented prototype limitation, not a discovered native defect. | Use fixed-height phones with internal scrolling in the concepts, then validate native insets, text scaling, and focus on-device. |

The visual system is coherent, but its repeated accent-panel treatment and
generic app tiles account for much of its identity. The opportunity is to make
hierarchy and movement feel authored for ordinary phone tasks.

## Heuristic assessment of the baseline

Scores are expert judgments on a 0–4 scale, not usability measurements.

| Heuristic | Score | Main evidence |
| --- | --- | --- |
| Visibility of system status | 3 | Requested/confirmed charge state and staged updates are explicit. |
| Match with the real world | 3 | Familiar app/settings labels; strong-auth wording can be clearer. |
| User control and freedom | 3 | Back, cancellation, retry, and Home routes are present. |
| Consistency and standards | 3 | Components are consistent; familiar search access could improve. |
| Error prevention | 3 | Preview-before-apply and explicit public sample warnings. |
| Recognition rather than recall | 2 | Home search depends on discovering a gesture or a second screen. |
| Flexibility and efficiency | 2 | Duplicate Home routes; no Settings search in the study. |
| Aesthetic and minimalist design | 2 | Repeated hero panels and generous inactive space obscure task differences. |
| Error recovery | 3 | Actionable setup, search, notification, and update outcomes. |
| Help and documentation | 2 | Some contextual explanation; ordinary-user discovery remains untested. |
| **Total** | **26/40** | Useful foundation, with material design improvements available. |

For a first-time Android user, the clearest concern is finding search without
coaching. For a distracted one-handed user, duplicated Home tiles and content
that grows beyond a physical screen deserve attention. For a large-text user,
fixed viewport and focus behavior need direct testing. These are review lenses,
not claims that actual participants were recruited.

## Three interactive directions

Open [the comparison](../prototypes/facet-directions.html). All phones use the
same task content, four sample apps, time, and notification fixtures.

| Direction | Composition and character | Benefit | Tradeoff |
| --- | --- | --- | --- |
| Still | Sage fields, centred clock, labelled app list, quiet settings rows | Clear labels and little visual competition | Lower Home density; a list is less familiar than a conventional icon grid |
| Lumen | Porcelain/cobalt palette, generous Home, conventional app grid, grouped settings | Familiar structure with a more deliberate tonal and typographic treatment | Greater vertical space use; keep evaluating content density |
| Contour | Coral/ink field, stacked clock, heavier type, crisp unboxed settings rows | Most distinctive graphic personality | Stronger visual weight may become tiring; needs ordinary-use feedback |

**Recommend Lumen for the next implementation pass.** Its visible search,
familiar app grid, and grouped Settings make the smallest learning demand
among these proposals while leaving room for a distinct identity. The claim
is a hypothesis to test with normal Android users. At the time of this comparison, the user had not yet selected a direction;
the follow-up selection above supersedes that status.

The prototypes support independent navigation, searchable apps/settings,
Back/Home/Recents buttons, shade entry/dismissal, quick toggles, brightness,
notification expansion/dismissal/Undo, connection-error retry, empty states,
and representative Files, messaging, battery, and settings detail screens.
Additional content is explicitly fictional. No service or device action runs.

## Validation and remaining work

Direct baseline checks included setup reveal/practice with a wrong and correct
answer, privacy selection/preview/apply, app drawer and empty/matching search,
Files navigation, notification diagnosis/enable/test feedback, charge slider
and acknowledgement, battery health, and updater download/verification-error
states. Light/dark and 200% German text were inspected. Reduced motion was
enabled through the original review integration. This pass does not repeat
the historical exhaustive baseline test matrix.

For the new concepts, the first rendered pass covered Home, shade, Settings,
light/dark, 200% German text, RTL error states, a 390 px browser viewport,
empty notifications, settings search and Back query restoration, quick toggles,
dismiss/Undo, and requested/confirmed charge state. It found a narrow large-text
overflow in Lumen, wrapped brightness percentages, undersized dismiss/navigation
targets, scroll resets, and the mixed gesture-pill/button navigation treatment.
The correction batch addressed those findings and made shade entry/exit
directional. The mechanical detector found two low-contrast host text colours;
both were darkened. A separate token-pair calculation found two light-theme
muted-on-field pairs below 4.5:1; both were darkened too.

The final confirmation record is in [verification](2026-09-13-facet-verification.md).
Browser sample controls are not a native Android implementation. Native gesture
arbitration, predictive Back, keyboard insets, spoken TalkBack/Switch Access,
haptics, real refresh rates, frame times, and hardware/service state remain
unverified. Larger study fonts are a browser stress approximation, not a native
font-scale certification. German is draft copy; RTL uses English strings.

## Implementation handoff

Keep the existing six-journey reference available until a direction is chosen.
After selection, refine it across all six journeys, clarify setup strong-auth
copy, and bind each surface to the actual launcher/SystemUI/Settings owner.
Assess resource changes and standard component theming first, then justify
layout/behavior patches and their maintenance cost. Do not assume every
third-party app can share the OS's internal composition or motion.

## Run notes

Target slug: `design-prototypes-diamaneos-ui-html`. No critique ignore list was
present. Two separate workers were spawned, but their handles later returned
`not_found`; no independent assessment result was claimed. The completed
review was single-context. Baseline CLI output was retained and not rerun.
The replacement-artifact detector ran once; later confirmation uses targeted
checks rather than another scan. No detector overlay/live-server was used.
The original temporary HTTP server was stopped; a logged localhost server
serves the interactive deliverable, with its PID and stop command recorded in
`/private/tmp/facet-preview-server.pid` and the verification note. Temporary
validation output stays outside the repository.

Questions skipped: the user already authorized review, refinement, and concrete
redesign alternatives, and specified that the recommendation precedes extending
a redesign across the remaining flows. Direction selection is the next user
decision; no new policy, architecture, or device capability is inferred here.
