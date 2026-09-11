# 01 — Setup

Refined browser reference; native implementation remains pending. Owners: passphrase onboarding / credential-policy enforcement / accessible journey validation. Pages: `setup`, `review`,
`practice`, `setupDone`. All words are public examples.

## Intent and inherited comparison

Entry: first boot. Goal: understand passphrase setup and finish a short,
accessible flow. Preserve SetupWizard2 and platform credential APIs. The
proposal changes hierarchy, deliberate review and contextual practice, not
credential authority. Compare the inherited stage before adding screens.

## Path and states

Welcome → deliberate review → practice → completion → Home. Primary controls
stay low when content fits; accessibility/language is available before review.

| State | Behavior | Exit / recovery |
| --- | --- | --- |
| Not started | Welcome and no account requirement | Start or Home |
| Ready | Words hidden; restart, optional fingerprint and loss consequences | Intentional reveal |
| Reviewed | Six numbered public words and hide action | Practice or hide |
| Practice | Identify a word from the example | Incorrect choice → retry/review; correct fixture → completion |
| Complete | Selected Standard defaults and completion | Home |
| Loading | Preparing setup with an exit | Back/Home; Normal scenario to continue |
| Failure/unavailable | Nothing set; retry available | Retry to review; never a weak fallback |
| Empty | No empty-credential success | Remain before commit |

Practice demonstrates an incorrect answer and recovery; it is not production
confirmation. passphrase onboarding must confirm the entire phrase through the real adapter
after freezing word list, count, edit policy and accessible input. Generation
remains 6–8 independent words from an audited list using the platform CSPRNG.
No credential is set here and there is no test-only policy exception.

Back before commit leaves no new credential. Native recreation must restart
the sensitive portion without persisting secrets, retaining only non-secret
progress. Home clears displayed fixture review. Back from practice must not
auto-announce words. Screenshot/recents protection requires native work.

## Focus and locale

Order: navigation → context → reveal/hide → practice → completion. Words are
not live-announced; deliberate reveal exposes numbered words to the reading
cursor. Errors have retry and review. Native spoken review must work without
requiring sight or compulsory biometrics.

Large text stacks the word grid. This English example keeps its word order
left-to-right during RTL stress. A localized list needs appropriate direction
metadata. German is partial draft copy. Loss/generation/confirmation wording
requires fluent critical-string review.

## Implementation mapping

Resources/app components: type, colours, spacing, copy, shared controls.
Narrow SetupWizard2/Settings integration: review, practice and real commit
states under passphrase onboarding / credential-policy enforcement. Secret lifecycle, strong authentication and profile
enforcement remain mandatory. No new service or privilege for visual reuse.
