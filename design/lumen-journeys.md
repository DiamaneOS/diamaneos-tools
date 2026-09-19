# Lumen — six journey reference

Selected by the project on 13 September 2026 after the Still/Lumen/Contour
comparison. Open [the interactive reference](prototypes/facet-lumen.html).
This selects the browser design direction, not a native implementation or
security qualification. [The previous baseline](prototypes/diamaneos-ui.html)
and [three-direction comparison](prototypes/facet-directions.html) remain.

The review rail starts each journey with fresh fixtures. Changing a scenario
also resets them. Appearance changes preserve state; ordinary navigation keeps
queries, permissions, confirmed values, and update progress. Every reading,
person, package, public word, and outcome is fictional. No credential, network
request, hardware change, download, export, profile unlock, or restart occurs.

## Setup

Welcome → language/accessibility if needed → deliberate reveal → full sample
phrase practice → completion → Home. A wrong order clears the attempted order
and offers review/another try. Failure at completion leaves nothing set and
offers retry. Loading, unavailable, and empty word-source scenarios do not
allow an empty credential success. Back to review hides the phrase; Home clears
the visible review and practice selection.

Six words are a public composition fixture. Actual generation, count, list,
normalization, confirmation, lifecycle, and credential mutation remain with
passphrase onboarding / credential-policy enforcement. No secret is entered.
This study does not enforce production prerequisites or protect screenshots.

Passphrase-only normal credentials and optional supported fingerprint use are
compatible requirements: the credential-policy task explicitly preserves
biometric and strong-auth semantics. Fingerprint does not bypass required
passphrase entry after restart or other strong-auth events. The earlier
critique's categorical conflict finding was incorrect and is corrected.

## Home and discovery

Visible search or All apps → app/Settings result → destination → Back with query
and scroll restored. A Home pull-down also opens Search; a status-area pull-down
opens the shade. All apps enters from below and retreats downward. Home search
and the shade enter from above and retreat upward. Accessible tap routes remain.

Files → sample document → share preview. Atlas → scoped location request →
Allow / Don't allow, with matching action treatment. Closing the sheet cancels
and preserves the previous grant. Independent app identities remain recognisable;
the study does not promise to restyle all third-party app interiors.

Search covers sample apps/settings only. Loading/unavailable retain direct
Files and Settings routes. No result is recoverable by editing the query.
Native owners: Launcher3, Settings search, DocumentsUI, permission controller.
No new indexer, persistent watcher, or expanded permission authority is implied.

## App privacy

Meadow / Personal → current preset → choose → exact preview → Apply → actual
result. The choice starts with the current Standard/Untrusted/Trusted preset.
A Custom state has no preselected replacement; Review stays disabled until
the user chooses one.

| Illustrative preset | Network | Sensors | File/contact selections |
| --- | --- | --- | --- |
| Untrusted | Block | Keep | Keep |
| Standard | Allow | Keep | Keep |
| Trusted | Allow | Allow | Keep |

Keep means no change. Trusted is not an audit or safety rating. The feature
task still owns the production capability/default contract.

Failure rejects the first changed capability while applying another requested
change, if one exists. The result identifies the failed capability and retains
successful changes. Retry requests the remaining difference. Return to app
leaves completed changes in place; it is not labelled Cancel after mutation.
Before Apply, Cancel changes nothing. Individual edits produce Custom.
Unavailable/loading/empty app targets do not offer a reliable preset write.
Owner: inherited capability APIs and Settings app privacy presets.

## Notifications

Shade → Missing notifications? → Meadow / Personal → confirmed notification
block → enable this app → permission on → sample test → received. Permission
enabled and delivery confirmed are separate states. Real app tests require
a supported test path or a user-triggered event; the OS is not assumed to
originate arbitrary apps' notifications.

Failure opens the stopped Work-profile case. It explains that Personal settings
cannot start Work, links to profile settings, and presents an honest device
authentication handoff. Closing it does not unlock Work. Home returns to
Personal; app privacy there remains scoped to Personal.

Empty shade still exposes controls and troubleshooting. Unknown cause permits
retry or inspecting app settings. Existing notification dismissal has Undo.
Owners: app notification controls, profile lifecycle, notification compatibility,
and a justified SystemUI layout patch if native evidence supports it.

## Battery and health

Requested slider → Apply → last confirmed limit. Changing the slider alone does
not alter the confirmed value. Failed Apply preserves that value and offers
Retry. Confirmation describes acceptance, not measured enforcement. Native
thermal and hardware safety remain authoritative.

Health shows sample cycles, temperature, unavailable capacity, and three scoped
sessions. Missing readings are not zero. Empty history disables export. Export
previews the exact session fields and scope; completion explicitly writes no
file. Production export needs a scoped destination picker and user action.
Owners: charge adapter, power/thermal validation, battery health reporting.

## Updates

Device/source/current/target identity → download → verify → install → ready
to restart → confirmation → completed fixture. The sample continues while
Home/Settings is open, and Settings reflects the current stage. Later keeps
the update pending. Closing restart confirmation keeps the current build active.

Failure rejects a damaged download at verification, before installation, and
offers a fresh copy. Unavailable/checking/no-update states are explicit.
Restart reminds the user of required passphrase entry. Completion updates the
sample running build only. Native acceptance still needs booted-target, data
retention, next-update, and rollback evidence.

Owners: inherited Updater and validated A/B pipeline. No new signer, trust
path, update backend, or privileged service follows from the visual design.
Wrong device/source/key, downgrade, low space, interrupted apply, failed boot,
and rollback require distinct native recovery states beyond this study.

## Accessibility and verification boundary

Fixed-height phones scroll internally. Text controls cover 100/150/200%; German
is partial draft copy and RTL uses English. Larger text stacks choices and
readings. Focus follows actions, sheets contain Tab navigation, Escape/Back
cancel sheets, and Home leaves the current flow. Reduced motion removes
displacement while retaining immediate state changes.

Browser target sizes and pointer gestures do not establish native touch,
TalkBack, Switch Access, IME insets, predictive Back, refresh-rate behavior,
haptics, or frame times. See [the review record](reviews/2026-09-13-lumen-journeys.md)
and [motion guidance](prototypes/lumen-motion.md). Keep those native checks and
ordinary Android user sessions separate from designer walkthroughs.
