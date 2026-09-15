# Lift motion reference

Three planes settle into the upright Lift silhouette. The complete mark is
the resting state and the default before playback; there is no automatic loop.

| Plane | Initial offset | Initial rotation | Initial skew Y | Delay |
| --- | --- | --- | --- | --- |
| Upper left | −17, −8 source units | −17° | −8° | 0 ms |
| Right | +17, −10 source units | +15° | +8° | 90 ms |
| Lower left | 0, +19 source units | −6° | 0° | 180 ms |

Each plane animates for 1550 ms with `cubic-bezier(.16,1,.3,1)`. The lower 22%
of each plane is initially clipped, then revealed as its transform returns to
identity by 70% of the duration. The last plane finishes at 1730 ms. Hold the
complete mark afterwards. The name remains stable; it does not indicate
progress, successful verification, or readiness to unlock.

Replay restarts immediately. Turning on reduced motion during playback cancels
movement and leaves the complete mark visible. The browser honours both the
system preference and the preview checkbox. No flashing, repeated pulse, or
unbounded rotation is used.

These values describe the browser reference. Native boot rendering needs its
own resource export and timing integration. It must not delay boot completion
to finish an animation. Select a static native alternative through a preference
available to that implementation; a browser media query alone does not solve
early-boot accessibility. Validate actual frame pacing, memory use, boot
handoff and light output on supported devices before shipping native assets.
