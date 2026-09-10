# DiamaneOS UX — Facet, direction 01

**Status: working design direction, 2026-09-10.** Aim for a fresh, enjoyable OS that remains straightforward for normal Android users, with clean, fast motion and consistency across system surfaces and apps where supported. Exact tokens, polished compositions and native behavior require design refinement and device validation.

## The vision

**Crisp, expressive, immediately understandable.** A DiamaneOS phone should
have personality before you open an app, then earn that personality through
how well it responds. A bold clock, a confident colour accent and generous
type make it recognisable. Clear rows, reachable actions and familiar Back
behaviour make it usable. Aim for “this feels good to use,” including on the
hundredth interaction.

The working name **Facet** describes one visual language seen across different
surfaces. It is a design-study name, not a product rename, logo or new feature.

1. **Give important content presence.** Use one large focal element per
   screen: the time, charge level, update identity or current task. Keep
   secondary settings compact and readable. Reflow at large text sizes rather
   than shrinking or truncating the action.
2. **Make colour purposeful.** The proposal uses graphite, warm neutral
   surfaces and bright citrine. Accent identifies the main action or selected
   state. It is not a security rating. Iris and Glacier are alternative paired
   palettes, and light and dark are equally intentional.
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

“Expressive” and “Quiet” are two compositions of the same system in the
prototype. Expressive is the recommended first exploration: larger display
type and more accent coverage. Quiet reduces those features. Neither changes
the information, navigation or capabilities. These comparisons do not commit
the OS to another appearance setting.

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

The illustrative Atlas app deliberately retains a different typeface and
warm brand surface while its location request uses DiamaneOS chrome. It is
fictional, not a proposed bundled app or a real app we claim to have modified.
Files and Settings explore inherited surfaces; they do not expand the scope
of any byte-identical mirror.

## Small visual system

Proposed values are in [tokens.json](../design/prototypes/tokens.json).
Use semantic roles rather than per-screen decisions. Native integration maps
the study's scoped variables to Android resources and app components.

- **Type:** local platform sans, regular/medium; 12/14/16 supporting/body,
  20/28/40 hierarchy, 88 for a focal number. The display clock is a deliberate
  composition exception. Use native scalable units and font fallback.
- **Spacing:** 4/8/12/16/24/32 dp; 24 dp reference content inset, reduced on
  narrow layouts.
- **Shape:** 16 dp controls, 24 dp grouped surfaces, 28 dp sheet tops, 18 dp
  icon containers. Home's accent shape never changes a hit target or status.
- **Elevation:** opaque groups and sheets with restrained separation. No
  frosted surface is required to read a control.
- **Icons:** familiar and labelled. Lucide is for this browser study only;
  reuse local platform assets before adding native dependencies. Preserve
  recognisable launcher app identity.
- **Motion:** 90 ms press, 140 ms state, 200 ms page, 240 ms sheet proposals;
  zero displacement under reduced motion. See the
  [motion contract](../design/prototypes/motion.md).

## Six reviewable journeys

The editable preview is [diamaneos-ui.html](../design/prototypes/diamaneos-ui.html).
It uses sample data and changes only in-memory state. The
[prototype README](../design/prototypes/README.md) describes controls and limits.

| Journey | Main improvement | Implementing owners |
| --- | --- | --- |
| [Setup](../design/core-journeys/01-setup.md) | Deliberate phrase review and practice in a short flow | passphrase onboarding / credential-policy enforcement / accessible journey validation |
| [Home](../design/core-journeys/02-home-discovery.md) | Distinctive Home with obvious app, file and settings access | everyday app flows / native UI integration |
| [App privacy](../design/core-journeys/03-app-privacy.md) | Preview capability changes and show actual partial results | app privacy presets |
| [Notifications](../design/core-journeys/04-notifications.md) | Scoped action after an identified cause | notification compatibility; shell candidate native UI integration |
| [Battery](../design/core-journeys/05-battery.md) | Requested and confirmed limits remain distinct | power and thermal validation / charge-limit integration / battery health reporting |
| [Updates](../design/core-journeys/06-updates.md) | Clear identity, stages, restart and recovery | OTA installation testing / interrupted-update recovery testing / offline signed OTA; updater integration |

The [journey index](../design/core-journeys/README.md) maps examples to
interface design. It replaces old assertions that language expansion was
already verified. Stress controls expose cases, not native test evidence.

## Accessibility, language and truthfulness

Use 48 dp effective native touch targets, natural focus order, visible labels,
text as well as colour, and no gesture-only task. The browser uses semantic
buttons, labelled fields, focus restoration, modal focus containment and
restrained status announcements. Externalise critical production wording as
complete strings rather than concatenated translated sentences.

The study provides 100/150/200% text, German draft labels with English fallback,
and RTL layout with English strings. It is not a full German translation or
Arabic locale. Complete critical-copy review, TalkBack, switch navigation,
bidirectional text and fixed-device viewport checks remain with localization and accessibility / accessible journey validation / native UI integration
and each feature owner. No accessibility certification is implied.

Passphrase-only remains required across independently challenged users and
profiles. The recorded compatibility conflict remains unresolved; this design
introduces no weak-credential or test-only exception. The public sample words
and short practice are fixtures, not a generator or production confirmation
algorithm. No real secret should be entered.

## Adoption and validation

Refine the selected working direction and freeze necessary tokens before native integration.
Compare Expressive, Quiet and the inherited baseline on the same tasks:
finish setup, find a download, restrict an app, restore missing notifications,
set a charge limit and recover from an update failure. Record completion,
wrong turns, misunderstood labels, assistance and access failures. Do not
infer user reception from our judgement or the Dank discussion.

Use resources/RRO, then standard APIs and shared components, then justified
narrow patches. The shade composition and launcher transitions are candidates,
not approval for a broad shell rewrite. native UI integration must bind actual source, name
an owner, measure rebase cost and define regressions. Shared visuals carry no
platform authority and require no new background service.

This is design work. Hardware proof, source integration, native performance
and user testing remain with later owners. There are no Android implementation,
hardware-enforcement or measured-usability claims in this proposal.

## Design refinement requirements

The first preview establishes direction, not final screen geometry. The first preview has uneven alignment/spacing, redundant text, the All apps label/arrow
treatment and page-like chrome on the notification shade. These require explicit
design work, not instructions to reproduce the first preview verbatim.

| Item | Required refinement | Ownership |
| --- | --- | --- |
| UI-01 | Consistent insets, baselines, grouping and vertical spacing; inspect actual rendered screens | interface design design → native UI integration native |
| UI-02 | Remove repeated headings, redundant text and preview-only explanations from ordinary product flows; retain meaningful state and critical consequences | interface design / translation integration → native UI integration |
| UI-03 | Refine All apps to a centred chevron with a labelled accessible tap target and familiar swipe-up route; keep app discovery obvious | interface design → everyday app flows / native UI integration |
| UI-04 | Treat the notification shade as a pull-down/swipe-up system surface; remove redundant page-style Back/bell chrome while preserving Android Back and assistive alternatives | interface design → notification compatibility / native UI integration |
| UI-05 | Immediate, interruptible feedback and consistent transitions; no queued taps or lost input | Feature owners / native UI integration |
| UI-06 | Real viewport, keyboard, focus, large text, long/RTL strings and reduced-motion checks | localization and accessibility / accessible journey validation / native UI integration |
| UI-07 | Preserve actual permission, charging and update state; never promote sample constants or success fixtures into production policy/evidence | passphrase onboarding / app privacy presets / security status / charge-limit integration / battery health reporting / offline signed OTA / native UI integration |
| UI-08 | Check owned/inherited surfaces and representative cooperating/custom-branded apps; record supported consistency and remaining app-owned differences | everyday app flows / native UI integration / usability acceptance |

UI-01–04 capture the observed first-pass feedback. UI-05–08 carry the existing
interaction, accessibility and authority requirements into the same handoff.
interface design refines the affected designs before they become implementation
references. native UI integration checks them on-device; usability acceptance evaluates actual tasks with
intended users and routes defects back to their owners. These are refinement
items within the existing design and implementation scope. The current
preview has not yet received these refinements.
