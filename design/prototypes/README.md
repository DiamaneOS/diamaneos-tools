# DiamaneOS interface studies

`diamaneos-ui.html` is the editable, self-contained interface fragment for the
six documented journeys. The fragment expects a preview host that supplies Lucide icons and may supply design controls. The host APIs are optional at runtime; a plain browser can render the fragment but will lack the host-supplied icon library. No package install,
network request, Android device, account, or real credential is needed.

The gallery selects Home, Setup, Privacy, Notifications, Battery or Updates.
Ordinary controls inside the phone demonstrate the flows. Design controls
offer Expressive/Quiet composition, Citrine/Iris/Glacier palettes, system/light/
dark appearance, reduced motion, text scaling, German draft copy, RTL layout
stress, and Normal/Failure/Unavailable/Loading/Empty scenarios. Scenario
selection resets the current journey. Appearance controls preserve the current
page so treatments can be compared directly. Navigation keeps journey state.

Files contains two individually selectable sample documents. Meadow shows a
theme-aware independent messaging app; Atlas shows an independent app with
its own brand. The sample Meadow reply is local and sends nothing. These
illustrate two realistic levels of consistency without selecting bundled apps.

Every person, app named Meadow or Atlas, file, measurement, network and build
is a fixture. The prototype changes only its in-memory state. It does not
create credentials, alter permissions, write charging controls, export files,
download an update, or contact a service. Reloading resets it. The sample
passphrase is deliberately public and must never be used as a real credential.

The phone grows with content and large type instead of clipping into a fixed
device viewport. It is a composition model, not a pixel-accurate Fairphone 6
screenshot. Browser keyboard semantics are included; native TalkBack,
Switch Access, haptics, predictive Back and device performance still require
Android implementation and testing.

See [the design vision](../../docs/UX.md), [motion specification](motion.md),
[tokens](tokens.json), and [journey annotations](../core-journeys/README.md).

Status: first preview, 2026-09-10. Alignment, copy, All apps and notification-shade refinements remain in the [design refinement requirements](../../docs/UX.md#design-refinement-requirements).
Those refinements are pending; native tokens/behavior and intended-user testing
are not complete. No claim of completed usability or accessibility testing.

Recorded design checks: JavaScript and token JSON parse; local
links resolve across ten design documents; tracked diffs pass whitespace
checks. A temporary Node harness exercised 180 combinations (six journeys,
five scenarios, three language/layout modes, two text scales), plus the six
main success/recovery paths. Its DOM was stubbed: it validates rendering
functions and local state transitions, not actual browser geometry, focus,
icon rendering, screen-reader output or native behavior. No browser/device
visual or accessibility pass is claimed.
