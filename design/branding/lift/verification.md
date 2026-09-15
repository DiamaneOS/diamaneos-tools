# Lift reference verification

Verified 15 September 2026 for the selected branding asset set.

- All 16 exported SVGs parse, use vector paths for lettering, and have no
  script or external font dependency. All four mark variants match the three
  selected source paths exactly. Bundled font hashes match the pinned source
  record. Both 1024 px PNG avatars were rendered from their SVG masters.
- The actual browser reference loaded the local logo, banner and wallpaper
  assets in light and dark appearances. The monochrome marks were visible in
  both surrounding themes. Theme changes update the download destinations.
- At 320 px, the reference reflows and has no horizontal document overflow.
  The Lumen header's added mark and branding link also fit at 320 px. Its
  rendered Home remained present after the header change.
- Replay starts the three-plane animation. Enabling reduced motion during
  playback cancels it immediately, leaving the complete static mark and an
  accurate status. The system preference is respected by both CSS and script.
- The archived Fold comparison loads with local resources. Selecting Arc
  changes the application heading and selected artwork. The initial direction
  comparison is also preserved with its original geometry and local font.
- The UTF-8 preview server supplies an explicit charset for Markdown and other
  text resources. HTTP checks confirm correct dashes, minus signs and degree
  symbols in the original document bytes, including requests that revalidate
  previously cached responses. Binary image content retains its original MIME
  type and bytes. This encoding correction was checked at the HTTP layer.

Validation used the served browser reference. Direct-file launch was not
exercised; the inspection browser does not permit navigation to local file
URLs. The preview's fonts, scripts and artwork are local resources.

No native boot asset integration, frame-pacing measurement, physical print
proof, spoken screen-reader test or intended-user acceptance is claimed.
