# Motion: immediate response, a clear destination

Motion is part of navigation, not a loading screen. These are proposed timing
targets, not measurements from Android or Fairphone hardware.

| Event | Proposed treatment | Duration |
| --- | --- | --- |
| Press | Immediate tonal response; control compresses at most 2% | 90 ms |
| Local state | State label and selection update together | 140 ms |
| Open a page | Content enters 12–20 dp from its logical destination; no bounce | 200 ms |
| Close / Back | Reverse direction; restore prior context and focus | 200 ms |
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
entry, reversible state changes and reduced motion. It uses a short staged
update fixture only to expose the updater state machine. It does not implement
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
