---
version: 1
slug: "design-prototypes-facet-directions-html"
primary_target: "design/prototypes/facet-directions.html"
related_targets: ["design/prototypes/facet-directions.css","design/prototypes/facet-directions.js"]
---

# Facet direction comparison

Scope: web prototypes of Android Home, notification shade, Settings, and supporting interactions. Mode: Operate. Three alternatives are review candidates, not an adopted OS baseline.

## Direction contract

THESIS: Make everyday phone use beautiful through clear hierarchy, discoverable search, coherent system surfaces, and immediate reversible actions. Each concept uses a fixed phone viewport and internal scrolling.

OWN-WORLD: Still uses sage fields and calm compact lists; Lumen uses porcelain surfaces, cobalt accents, generous headings, and familiar grouped settings; Contour uses ink, coral fields, strong aligned type, and crisp unboxed rows. App identity remains consistent across concepts. All have paired light/dark tokens and scalable text.

STORY: Open apps, find settings, change a quick control, inspect and dismiss a notification, recover from an error, and return without learning a new navigation model.

FIRST VIEWPORT: Three equally usable phone previews with explicit review controls outside them. Each Home has a clock/date, visible search and All apps, and four labelled apps. The signature transition is the shade descending and retreating while Back returns to its previous context.

FORM: Grounded candidates: (1) Lumen/familiar tonal launcher, (2) Contour/graphic wayfinding, (3) Still/quiet task lists, (4) dense professional utility, (5) spatial translucent shell, (6) monochrome typographic launcher, (7) playful kinetic tiles. Seed c2988422 assigned index 3; Still is fully built alongside Lumen and Contour, as the user explicitly requested two or three interactive comparisons before adoption. All three preserve the same core tasks. The catalog's diving, sneaker-box, camera, instrument, and cracktro metaphors offer less immediate Android familiarity; their useful discipline is consistent hierarchy and continuity, not literal themed navigation.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

The user's requested comparison takes precedence over the generic pre-build concept-lock flow. No raster assets are needed: icon-library vectors and exact geometric wallpaper forms are sufficient. The user decides adoption after the reviewable concepts and recommendation.
