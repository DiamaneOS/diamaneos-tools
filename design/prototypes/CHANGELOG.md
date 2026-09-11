# Interface study changes

## Unreleased

- Make the existing browser file work offline with bundled, attributed Lucide
  icons and a separate system/light/dark preview selector. Provide an exporter
  for the same study in hosts with their own icons and design controls.
- Replace the permanent Home search bar with middle pull-down search and
  immediate query focus, keeping All apps → Search available without gestures.
- Make the drawer enter upward and dismiss downward; make the shade and Home
  search enter downward and dismiss upward. Back/Home follow the surface's
  axis, RTL only reverses page navigation, and interrupted copies are removed.
- Give permission decisions matching buttons, distinguish information from
  input, and separate explicit denial from cancellation in the Atlas sample.
- Refine the six Facet journeys with shared geometry, simpler copy and a bound
  visual/motion reference for native implementation.
- Centre All apps and add labelled tap/keyboard and swipe affordances for the
  drawer and notification shade.
- Preserve focus across local updates, correct swipe follow-up input, and
  reflow large text without splitting clock digits or clipping long strings.
- Record rendered browser verification and keep native/device acceptance
  explicitly assigned to the implementing components.
