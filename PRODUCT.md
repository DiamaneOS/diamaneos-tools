# DiamaneOS interface study

<!-- impeccable:product-schema 1 -->

This record scopes design work in this repository. The repository also holds
engineering tools; those are not part of this UI assignment.

## Platform

web

The artifact is an interactive browser prototype of an Android operating
system. Native Android conventions guide its interaction design. Browser
results are not native implementation, hardware, or usability qualification.

## Users

Ordinary Android users who should be able to adopt DiamaneOS comfortably and
complete everyday tasks without learning unfamiliar navigation conventions.

## Product Purpose

Explore and refine a beautiful, distinctive, fresh, smooth OS experience with
strong security and privacy and excellent everyday functionality and usability.
Facet is the working name of the design study, not a product rename.

## Operating Context

The existing HTML/CSS/JavaScript study is in `design/prototypes/`. Its six
journeys cover setup, home/app discovery, app privacy, notifications, battery,
and updates, with supporting Settings, Files, app, and permission screens.
All phone data and actions are simulated. Real credentials are not needed.

## Capabilities and Constraints

Fairphone 6 and a GrapheneOS-derived base are the current engineering context;
the owner wants room for other devices and bases. Verify native capabilities
against the implementing task and actual source before making integration claims.
Preserve truthful state, app/profile boundaries, and actual security requirements.

## Design Mandate

The owner's 2026-09-13 brief explicitly allows completely different visual and
UX directions when they work better. Existing guidelines, tokens, timings, and
mockups are revisable. Review the current rendered study, then either implement
focused improvements or build two or three interactive alternatives for Home,
the notification shade, and Settings. Recommend a direction and explain tradeoffs
before extending a redesign across the remaining journeys. Preserve the baseline.

## Evidence on Hand

`docs/UX.md`, `design/core-journeys/`, and the running browser prototype record
the existing proposal. Prior verification is documented in the prototype README;
new observations must be distinguished from those historical checks.
No intended-user acceptance or native performance results are established by
this design review.

## Accessibility & Inclusion

The current brief requires relevant light/dark, enlarged text, longer-label,
reduced-motion, and error-state checks. Existing journey requirements include
accessible alternatives to gestures, logical focus, readable state, and native
TalkBack and device verification at implementation time.

## Selected direction — 13 September 2026

The owner chose Lumen ("let's start with lumen") after interacting with the
comparison. Extend its familiar app grid, visible search, grouped Settings,
porcelain/cobalt roles and reversible navigation across all six journeys. The
current reference is `design/prototypes/facet-lumen.html`; preserve the original
baseline and three-direction comparison. This authorizes adoption for design
work, not changing platform capability/security policy or declaring native
implementation complete.

## Selected branding — 15 September 2026

Lift is the selected DiamaneOS branding direction: three asymmetric folded
planes with softened outer corners. Its source assets, usage guidance,
wallpapers, banners and motion reference live in `design/branding/`.
The public product name remains DiamaneOS. Lift complements Lumen without
changing interface semantics, native capability claims, or independent app
identities. Earlier branding concepts remain available for comparison.
