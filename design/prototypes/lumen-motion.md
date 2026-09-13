# Lumen motion reference

This is the selected browser direction. The older [motion.md](motion.md)
belongs to the preserved six-journey baseline. Durations below describe the
prototype, not measured Android performance.

| Interaction | Behavior | Browser timing |
| --- | --- | --- |
| Press | Immediate tonal/brightness feedback | Immediate |
| Control change | State label and pressed/checked state update together | 120 ms colour/switch transition |
| Nested page / Back | Small logical horizontal movement, reversed for RTL | 170 ms, cubic-bezier(.16,1,.3,1) |
| All apps | Full surface rises from below | 230 ms entry |
| All apps exit | Outgoing surface retreats downward | 220 ms exit |
| Home search / shade | Full surface enters from above | 230 ms entry |
| Home search / shade exit | Outgoing surface retreats upward | 220 ms, cubic-bezier(.4,0,.2,1) |
| Decision sheet | Immediate opaque sheet with protected focus | Immediate |
| Reduced motion | State changes with no animated displacement | 0 ms |

Each navigation commits once. New input cancels the outgoing animation;
temporary visual copies are inert and removed after completion or cancellation.
Back restores the query, scroll, and invoking control. A swipe suppresses only
its trailing click. Browser pointer thresholds are 40 px with a predominantly
vertical trajectory, not native gesture physics. Status and shade handles
reserve their pointer drag; ordinary content retains scrolling.

The updater uses 250 ms sample progress ticks with four ticks per stage. These
are solely to expose download/verification/installation and leaving the screen.
They are not targets for actual work or a reason to delay a fast operation.
Resetting the review journey cancels its timers; ordinary navigation does not.

Native integration should reuse platform gesture navigation, predictive Back,
app transitions, and animation preferences before introducing custom motion.
Measure interruptions, input-to-visible response, frame times, missed frames,
IME behavior, and refresh-rate changes on the target device. Haptics remain a
native proposal, respectful of user settings, with visual/spoken equivalents.
