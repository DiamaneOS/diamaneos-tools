# DiamaneOS UX — Facet, Lumen

**Status: Lumen selected for browser design, 2026-09-13.** The project selected Lumen
from three interactive alternatives and requested its extension across all six
journeys. [Open the current reference](../design/prototypes/facet-lumen.html).
Native integration, device qualification, and intended-user acceptance remain
separate work. The [previous UX record](../design/reviews/2026-09-13-pre-lumen-ux.md)
and original mockup remain available for comparison.

**Branding: Lift selected, 2026-09-15.** The [brand guide](../design/branding/README.md)
and [offline reference](../design/branding/lift/preview.html) contain the folded-plane
mark, wordmark assets, wallpapers, banners and motion study. DiamaneOS remains
the public identity; Lift's artwork does not replace semantic status colours,
ordinary Android navigation or app identity. Native boot integration is separate.

## The vision

**Crisp, expressive, immediately understandable.** A DiamaneOS phone should
have personality before you open an app, then earn that personality through
how well it responds. A bold clock, a confident colour accent and generous
type make it recognisable. Clear rows, reachable actions and familiar Back
behaviour make it usable. Aim for “this feels good to use,” including on the
hundredth interaction.

The working name **Facet** describes one visual language seen across different
surfaces. It is a design-study name, not a product rename, logo or new feature.

1. **Give important content presence.** Choose emphasis for the task:
   a clock on Home, exact permission changes in privacy, and readable stages
   in updates. Avoid repeating a dominant accent panel on every screen. Keep
   secondary settings compact and readable. Reflow at large text sizes rather
   than shrinking or truncating the action.
2. **Make colour purposeful.** Lumen uses porcelain surfaces and
   cobalt accents with paired dark roles. Accent identifies the main action or selected
   state. It is not a security rating. Light and dark are equally intentional. Historical
   palette alternatives remain in the original study.
3. **Keep the next action close.** Put completion controls toward the bottom
   when content fits. A short contextual decision can use an opaque bottom
   sheet; a deeper task remains a normal page. Long content scrolls in the
   native implementation. Do not force every setting into a sheet.
4. **Make motion explain continuity.** Immediate press feedback, short page
   transitions and reversible sheets give each action a clear destination.
   Respond to a new touch before an animation finishes. Gestures supplement
   visible controls; Android Back and Home retain their meaning.
5. **Make state trustworthy.** Show what changed, what did not, and the
   specific recovery action. A failure can be well composed without being
   hidden. No blanket “secure” score, false all-applied preset, invented health
   estimate, or successful update inferred from a download.

Lumen retains ordinary app icons, visible search, grouped Settings, and a
consistent tonal language. Still and Contour remain in the
[comparison](../design/prototypes/facet-directions.html). Expressive/Quiet and
Citrine/Iris/Glacier belong to the older study, not additional committed OS
appearance settings.

## What the Dank reference contributes

The useful lesson is the relationship between response, continuity and
enjoyment. In the [original discussion](https://www.reddit.com/r/Android/comments/8iuqsm/dank_is_a_new_reddit_app/),
people praised the fresh presentation, animations and gesture interactions.
The same thread asked for bottom controls, non-gesture actions, light mode,
text sizing and familiar Back/refresh behaviour. Those requests matter as much
as the enthusiasm: novelty must survive ordinary use. This is a qualitative
reading of a self-selected discussion, not a representative usability study.

The [developer's introduction](https://saket.me/dank/) describes contextual
expansion, inline replies and preserving context; it also records disabling
an experimental tab feature because it was not obvious. Facet takes the
principle of understandable continuity without adopting Dank's historical
style or introducing its action gestures into Android navigation.

## A recognisable system, including apps

| Surface | Intended consistency | Implementation boundary |
| --- | --- | --- |
| Setup, owned Settings additions, battery and updater | Full colour/type/spacing/component/motion vocabulary | Existing owners and platform controls; source bindings at each implementing task |
| Home, notification shade, system panels | Shared hierarchy, colour roles and state language | Resources first; layout/behavior may require a justified narrow launcher/SystemUI patch |
| Permission prompts, sharing and file selection | Consistent system-owned boundary around an app request | Preserve app/profile identity, scope, authentication and authority |
| Maintained or inherited apps we can modify | Shared theme/components where supportable | Assess scope, licence, security and maintenance; no commitment to fork every utility |
| Independent apps using compatible system preferences | Available theme roles and supported transitions | App adoption and platform version determine the result |
| Independent apps with their own rendering or brand | Coherent system entry/exit and system-owned dialogs | Preserve app identity; no promise to replace its internal layout or animation |

Android's [dynamic-colour API](https://developer.android.com/develop/ui/views/theming/dynamic-colors)
is applied by an app or activity. A system palette does not guarantee every
installed app uses it. Likewise, [predictive Back support](https://developer.android.com/guide/navigation/custom-back/predictive-back-gesture)
depends on app integration. [Runtime resource overlays](https://source.android.com/docs/core/runtime/rros)
change permitted resource values, not arbitrary application behaviour. Verify
these mechanisms against the pinned upstream during implementation.

The illustrative Atlas app retains its identity while the system-owned location
request identifies the app/profile and offers equally weighted decisions.
It is fictional, not a proposed bundled app or an app claimed to be modified.
Files and Settings explore inherited surfaces without expanding mirror scope.

## Small visual system

The selected roles are in [lumen-palette.json](../design/prototypes/lumen-palette.json)
and [DESIGN.md](../DESIGN.md). Native integration must map semantic roles to
actual platform resources rather than treating CSS values as device policy.

- **Type:** platform sans-serif; 11–14 px supporting text, 19–34 px task
  hierarchy, and a 76 px Home clock at the comparison's desktop base. Enlarged
  text reflows and reduces heading/clock bases before scaling. Native units,
  font fallback and font scaling still need implementation and verification.
- **Spacing:** 16–22 px content insets and task-specific density. Desktop phone
  is at most 390 × 800 px; the narrow review uses 780 px height. Content scrolls
  inside the phone rather than growing its frame.
- **Shape:** 18 px primary groups/actions, 12 px search fields, 16 px phrase and
  choice containers, 26 px decision-sheet tops. Shapes reinforce hierarchy;
  labels and semantics still communicate state.
- **Elevation:** opaque tonal groups and sheets; no blur is needed to read controls.
- **Icons:** bundled Lucide for the browser only. Prefer native platform assets
  before adding dependencies; preserve recognisable app identities.
- **Motion:** 120 ms control state, 170 ms page movement, 230/220 ms shell
  entry/exit; immediate decision sheets. Reduced motion removes displacement.
  See [Lumen motion](../design/prototypes/lumen-motion.md). These are browser
  intent, not native performance measurements.

## Six reviewable journeys

The editable preview is [facet-lumen.html](../design/prototypes/facet-lumen.html).
It uses sample data and changes only in-memory state. The
[prototype README](../design/prototypes/README.md) describes controls and limits.

| Journey | Main improvement | Implementing owners |
| --- | --- | --- |
| [Setup](../design/lumen-journeys.md#setup) | Deliberate phrase review and practice in a short flow | passphrase onboarding / credential-policy enforcement / accessible journey validation |
| [Home](../design/lumen-journeys.md#home-and-discovery) | Familiar Home, visible search, accessible All apps discovery | everyday app flows / native UI integration |
| [App privacy](../design/lumen-journeys.md#app-privacy) | Preview capability changes and show actual partial results | app privacy presets |
| [Notifications](../design/lumen-journeys.md#notifications) | Scoped action after an identified cause | notification compatibility; shell integration candidate |
| [Battery](../design/lumen-journeys.md#battery-and-health) | Requested and confirmed limits remain distinct | power and thermal validation / charge-limit integration / battery health reporting |
| [Updates](../design/lumen-journeys.md#updates) | Clear identity, stages, restart and recovery | OTA installation testing / interrupted-update recovery testing / offline signed OTA; updater integration |

The [journey reference](../design/lumen-journeys.md) describes successful and unsuccessful outcomes for each journey. Text and layout stress controls expose cases; native language and accessibility validation is still required.

## Accessibility, language and truthfulness

Use 48 dp effective native touch targets, natural focus order, visible labels,
text as well as colour, and no gesture-only task. The browser uses semantic
buttons, labelled fields, focus restoration, modal focus containment and
restrained status announcements. Externalise critical production wording as
complete strings rather than concatenated translated sentences.

Peer permission decisions use comparable buttons and clear consequence labels.
Read-only information has a labelled value; editable fields, switches and
navigation rows retain their own roles. Closing a decision sheet cancels;
explicit denial changes the sample grant. Native permission controllers retain
authority over the actual grant and any supported precision choices.

The study provides 100/150/200% text, German draft labels with English fallback,
and RTL layout with English strings. It is not a full German translation or
Arabic locale. Complete critical-copy review, TalkBack, switch navigation,
bidirectional text and fixed-device viewport checks remain with localization and accessibility / accessible journey validation / native UI integration
and each feature owner. No accessibility certification is implied.

Passphrase-only normal credentials remain required across independently
challenged users/profiles. The credential-policy contract preserves supported
biometric and strong-auth semantics; fingerprint does not bypass required
passphrase entry. This distinction corrects the initial review’s overly broad
fingerprint conflict finding. The recorded compatibility conflict remains unresolved; this design
introduces no weak-credential or test-only exception. The public sample words
and short practice are fixtures, not a generator or production confirmation
algorithm. No real secret should be entered.

## Adoption and validation

Use the selected mockup baseline for implementation references. Bind its semantic
roles to the actual upstream resources and validate native font/layout results
before accepting visual integration. Prototype constants are not device policy.
Compare Lumen and the inherited baseline on the same tasks:
finish setup, find a download, restrict an app, restore missing notifications,
set a charge limit and recover from an update failure. Record completion,
wrong turns, misunderstood labels, assistance and access failures. Do not
infer user reception from our judgement or the Dank discussion.

Use resources/RRO, then standard APIs and shared components, then justified
narrow patches. The shade composition and launcher transitions are candidates,
not approval for a broad shell rewrite. Native UI integration must bind actual source, name
an owner, measure rebase cost and define regressions. Shared visuals carry no
platform authority and require no new background service.

This is design work. Hardware proof, source integration, native performance
and user testing remain with later owners. There are no Android implementation,
hardware-enforcement or measured-usability claims in this proposal.

## Design refinement requirements

The refined browser reference resolves the first preview’s geometry, copy and
navigation issues. These requirements continue to apply when porting the
reference into native components; browser checks cannot close device gates.

| Item | Required refinement | Ownership |
| --- | --- | --- |
| UI-01 | Consistent insets, baselines, grouping and vertical spacing; inspect actual rendered screens | Interface design → native UI integration |
| UI-02 | Remove repeated headings, redundant text and preview-only explanations from ordinary product flows; retain meaningful state and critical consequences | interface design / translation integration → native UI integration |
| UI-03 | Refine All apps to a centred chevron with a labelled accessible tap target and familiar swipe-up route; keep app discovery obvious | interface design → everyday app flows / native UI integration |
| UI-04 | Treat the notification shade as a pull-down/swipe-up system surface; remove redundant page-style Back/bell chrome while preserving Android Back and assistive alternatives | interface design → notification compatibility / native UI integration |
| UI-05 | Immediate, interruptible feedback and consistent transitions; no queued taps or lost input | Feature owners / native UI integration |
| UI-06 | Real viewport, keyboard, focus, large text, long/RTL strings and reduced-motion checks | localization and accessibility / accessible journey validation / native UI integration |
| UI-07 | Preserve actual permission, charging and update state; never promote sample constants or success fixtures into production policy/evidence | passphrase onboarding / app privacy presets / security status / charge-limit integration / battery health reporting / offline signed OTA / native UI integration |
| UI-08 | Check owned/inherited surfaces and representative cooperating/custom-branded apps; record supported consistency and remaining app-owned differences | everyday app flows / native UI integration / usability acceptance |

UI-01–04 capture the observed first-pass feedback. UI-05–08 carry the existing
interaction, accessibility and authority requirements into the same handoff.
The current reference applies shared insets, symmetric toolbar tracks, aligned
cards, reflowing text and effective 48 px controls (UI-01). Setup and app context
are stated once; sample-data context lives outside the phone, with explicit
warnings retained where a public passphrase or simulated side effect could be
misunderstood (UI-02). All apps is a centred chevron with an accessible name,
tap/keyboard activation and an upward swipe on its handle. It opens upward
and closes downward. Home's permanent search bar is replaced by clock/empty
middle pull-down search; All apps → Search remains available without gestures
(UI-03). The shade opens downward from the status area and dismisses upward
through its centred chevron, date/edge swipe or Back/Escape. Home also closes
the drawer/shade along the same vertical axis. The shade has no page Back/bell
header (UI-04). Gestures supplement reachable controls; native arbitration must
preserve widget interaction, ordinary scrolling and platform navigation.

The [prototype record](../design/prototypes/README.md) describes actual browser
checks. [Journey annotations](../design/core-journeys/README.md) carry the
reading/focus order, state and component handoff. Native UI integration checks
fixed viewports and Android navigation on-device; accessibility and usability
owners evaluate assistive tools and real tasks with intended users. Defects
return to the responsible component maintainer. Native performance, haptics,
TalkBack, Switch Access and actual hardware/service state remain unverified.

## Lumen adoption record

The browser direction was selected on 13 September 2026. This supersedes the
older visual treatment for new design work while retaining its useful state
and recovery contracts. The native acceptance requirements above remain.
See [Lumen implementation and verification](../design/reviews/2026-09-13-lumen-journeys.md).
