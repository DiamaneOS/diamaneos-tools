# DiamaneOS branding — Lift

Lift is the selected branding direction, adopted on 15 September 2026. Its
three asymmetric planes and softened outer corners preserve the chosen Fold
silhouette. The first asset set uses that geometry without redesigning it.

Open the [Lift reference](lift/preview.html) to compare light/dark applications,
download assets, and play the motion study. It works offline with its adjacent
files. See the [recorded checks](lift/verification.md) for the tested scope.
The [initial directions](archive/brand-directions.html) and
[Fold variations](archive/fold-options.html) preserve the alternatives.

From the repository root, run `python3 design/serve.py --port 8766`, then open
`http://127.0.0.1:8766/design/branding/lift/preview.html`. The server binds only
to the local machine and declares UTF-8 for text resources, including linked
Markdown with dashes, minus signs and degree symbols.

## Identity and scope

**DiamaneOS** is the public product name. **Facet** names the interface design
language; **Lumen** describes its current visual direction; **Lift** describes
the brand mark and graphic treatment. These working names are not extra
consumer-facing products. The master logo does not include an OS base, device,
affiliation claim, or security claim. Relevant project/source documentation
continues to carry accurate upstream attribution and device scope.

Lift supplies the mark, wordmark treatment, banners, wallpaper artwork and
brand motion. It does not change semantic status colours, permission meaning,
Android navigation, or independent app identities. The Lumen journey reference
remains the interface baseline.

## Logo assets

The [asset folder](lift/assets/) contains:

| Asset | Use |
| --- | --- |
| `lift-mark-light.svg` / `lift-mark-dark.svg` | Cobalt mark for light surfaces; pale blue mark for dark surfaces |
| `lift-mark-black.svg` / `lift-mark-white.svg` | A single solid colour for printing, engraving or contrasting backgrounds |
| `diamaneos-lockup-*.svg` | Mark with outlined DiamaneOS lettering; no installed font required |
| `lift-avatar-*.svg` / `.png` | Square profile image with room for a circular crop |
| `lift-banner-*.svg` | 1200 × 400 banner with quiet space around the name |
| `lift-wallpaper-portrait-*.svg` | 1440 × 3200 portrait artwork; no baked-in clock or controls |
| `lift-wallpaper-desktop-*.svg` | 3840 × 2160 landscape artwork |

`light` and `dark` describe the intended background, not the colour of the
mark. Keep an SVG master when making other sizes. The square PNG avatars are
1024 × 1024; they do not replace the vector source.

### Proportion and spacing

Use the three paths together, at their original relative positions. Do not
stretch, mirror, close the gaps, add a fourth plane, outline the mark, or apply
a different rotation to the mark itself. Cropped and rotated planes belong
to supporting artwork; the identifying logo remains complete and upright.

Allow at least 20 source units of clear space around the 108 × 106 mark
canvas, approximately one fifth of its displayed width. The transparent SVG
canvas is not a complete clear-space allowance. The lockup already sets the
internal mark-to-lettering gap; leave at least one capital-letter height
between it and surrounding text or edges.

Use the mark alone for compact contexts. Prefer 24 px or larger for ordinary
screen use; 16 px is a small-size specimen requiring inspection at the actual
display scale. Use the full lockup at 160 px wide or larger. These are first
asset-set guidance, not native-device qualification. At constrained sizes,
choose the separate mark and accessible product text instead of squeezing the
lockup. Supply `alt="DiamaneOS"` when the image carries the name; use an empty
alt when adjacent text already names it.

## Colour and typography

The [source record](lift/source.json) holds geometry and colour roles. Light
uses porcelain `#f2f5fa`, ink `#1e2a40`, and cobalt `#305dd0`; dark uses
`#151e30`, ink `#edf1fc`, and pale blue `#acc8ff`. The logo itself uses a single
colour. Supporting artwork may separate planes with the tonal and middle-blue
roles. The middle-blue values are illustration colours, not new UI states.

The wordmark uses Instrument Sans Medium for Diamane and Regular for OS at
the same size. The assets contain outlined glyphs with explicit tracking;
they are not a custom typeface. The preview bundles the unmodified variable
webfont locally. This does not select a replacement font for native Android.
Font sources, hashes, authors and the OFL are in [lift/fonts/](lift/fonts/).

## Graphic system

Scale and crop the three planes for banners and wallpapers. Keep large quiet
areas where a clock, icon labels or page heading will sit. The source identity
comes from the shape and spacing, so artwork can evolve beyond this first
palette. Avoid repeating the complete logo behind every piece of UI.

For release graphics, pair a full upright mark or lockup with an oversized
crop. Use real release information only when available. Artwork never implies
a completed feature, successful update, certification or security score.

## Motion

The [motion reference](lift/motion.md) specifies the three planes settling
into alignment. The browser preview includes replay and a static reduced-motion
alternative. It is an authored timing reference, not a native boot animation
package or evidence of device performance.

## Maintenance

Edit [lift/source.json](lift/source.json), then run
`python3 design/branding/lift/build_assets.py` from the repository root using
Python with `fonttools==4.60.1`. The builder reads only bundled fonts and emits
SVGs, browser geometry data and a hash manifest. No network is required.
Review generated marks, lettering, crops and contrast after any source change.
Raster avatars are exported from their SVGs; see [the asset export notes](lift/exports.md).

Original Lift artwork was created for DiamaneOS. This design record adds no
new blanket licence or trademark permission. Existing project licensing
decisions apply separately; the bundled font retains its own notices.
