# Journey 1 — Setup (interface design prototype, synthetic data)

Entry: first boot → SetupWizard2. Goal: finish setup with passphrase,
defaults, optional compatibility — resumable, no cloud account.
Implementation card: passphrase onboarding / credential-policy enforcement. Inherited path: GOS SetupWizard2 (+accessible journey validation
assistive path); compare before adding screens.

Normal path: welcome → language (default English, German offered) →
passphrase generate (6–8 words) → deliberate review → practice/confirm →
biometric explainer (optional, never mandatory) → defaults (Standard preset
pre-selected, changeable) → optional compatibility (Accrescent/UnifiedPush/
sandboxed-Play pointers) → done. Progress shown as step x/y; Back resumes,
never loses completed steps; cancel before credential commit leaves no
credential (passphrase onboarding states).

States: loading (word-list check) → error with retry, never a weak fallback;
empty N/A; passphrase screen blocks screenshots/recents; loss/recovery limits
stated honestly. Failure state: failed update-check or unavailable control →
actionable message + Skip-for-now + retry, never a dead end (pattern).

Keyboard/SR : order welcome→language→generate→review→confirm→defaults→
done; every primary action labeled (e.g. "Generate new passphrase",
"Review words aloud off by default"); credential review is deliberate
opt-in, no auto-announcement; errors move focus + announce recovery action.

Locale: long German strings + RTL layout + 200% text verified in annotation;
critical wording needs fluent review or disclosed source-language fallback.
Non-gesture: all actions reachable by tap/keyboard/switch; no timed step.

Reuse : SetupWizard2 resources + shared buttons/cards from token set;
no new privileged service — credential commit uses normal platform APIs only.
New screens only where prototype proves ordinary Settings cannot do the job.
