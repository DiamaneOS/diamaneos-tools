# Motion: immediate response, a clear destination

> Historical baseline. See [Lumen motion](lumen-motion.md) for the selected direction.

Motion is part of navigation. These selected timings bind the browser
reference; they are not measurements from Android or Fairphone hardware.

| Event | Reference treatment | Duration |
| --- | --- | --- |
| Press | Immediate tonal response; control compresses at most 2% | 90 ms |
| Local state | State label and selection update together | 140 ms |
| Open a page | Content enters 12–20 dp from its logical destination; no bounce | 200 ms |
| Page Back | Reverse logical horizontal direction; restore context and focus | 200 ms |
| All apps | Rises from below; retreats downward on dismiss, Back or Home | 240 ms |
| Home search | Enters from above after Home pull-down; retreats upward to Home | 240 ms |
| Notification shade | Enters from above; retreats upward on dismiss, Back or Home | 240 ms |
| Context sheet | Opaque surface rises from the bottom, retaining its parent | 240 ms |
| Large native app transition | Follow the platform transition, with continuous gesture progress where supported | Native, to measure |
| Reduced animation | Immediate state change; no translation, scale or simulated spring | 0 ms |

Use cubic-bezier(0.2, 0.8, 0.2, 1) for entry and (0.4, 0, 1, 1) for exit.
Never queue input behind an animation. A second action interrupts or replaces
the visual transition; the underlying state changes once. Loading indicators
describe actual work; they cannot be used to make a fast operation look grander.
No staggered list entrance, ambient shimmer, endless background animation,
parallax wallpaper, or looping ornament is proposed.

The HTML study demonstrates press response, directional page entry, sheet
entry, vertical shell entry/exit, reversible state changes and reduced motion.
All apps and the shade translate their full surface extent. During shell exit,
the outgoing surface moves away to reveal its destination; the destination
does not slide sideways. Back from a Search result or a nested page follows
the logical horizontal hierarchy, mirrored in RTL. Vertical axes stay unchanged
in RTL. Transient visual copies are inert, hidden from assistive tools and
removed on completion/interruption; new input acts on the current state.

Pointer
swipes commit at 32 px with a predominantly vertical trajectory; these are
browser inspection thresholds, not native gesture physics. Home clock/empty
middle pull-down opens Search; status-area pull-down opens the shade. Drawer
handle pull-down dismisses All apps; shade handle/date pull-up dismisses the
shade. Native gesture arbitration must respect widgets, content scrolling and
platform navigation. A swipe suppresses
only its own trailing click, so the next control responds immediately. A short
staged update fixture is used only to expose the updater state machine. It does not implement
Android's predictive Back, true shared-element app transitions, native fling
physics or haptics. Those should reuse supported platform behavior first.

Optional haptic vocabulary at native integration: one light tick for a
committed toggle or a stepped adjustment; a distinct confirmation only for a
meaningful completion. Respect platform settings, avoid vibration on every
page transition, and retain visible and spoken equivalents. Hardware strength
and scheduling must be tuned on the FP6, not inferred from this browser study.

Before adopting a shell patch in native UI integration, compare the same task with inherited
navigation. Record frame times, missed frames, input-to-visible-response,
interruption behavior, cold/warm launches and reduced-animation behavior on
the actual refresh rates and under realistic load. Do not claim a refresh rate
or a latency result from CSS durations. Test gesture navigation and three-button
navigation. Ownership and maintenance evidence remain prerequisites for broad
SystemUI or recents changes.
