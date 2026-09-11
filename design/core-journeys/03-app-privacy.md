# 03 — App privacy

Refined browser reference; native implementation remains pending. Owner: app privacy presets. Pages: `privacy`, `privacyPreview`;
sheets: preset selection and individual controls.

## Intent and inherited comparison

Entry: app privacy for Meadow in Personal profile. Goal: understand and apply
a scoped change. Retain inherited Network/Sensors/Storage/Contact Scopes
owners. Shared appearance does not create a permission authority.

## Path and states

App/profile → Change preset → exact preview → Apply → actual result.
Individual edits become Custom and persist. Cancel before Apply changes
nothing; Back does not undo completed changes or reapply an old preset.

The matrix below is **illustrative, not a frozen production decision**.
app privacy presets still owns the exact capability/default contract.

| Preset | Network | Sensors | File/contact selections |
| --- | --- | --- | --- |
| Untrusted | Block | Keep | Keep |
| Standard | Allow | Keep | Keep |
| Trusted | Allow | Allow | Keep |

Keep is shown as No change. Nothing grants broad storage/contacts or activates
detailed history. Trusted never means audited safe. The initial fixture is
Standard with network allowed and sensors blocked; preserve real overrides.

| State | Behavior | Recovery |
| --- | --- | --- |
| Loading | Read current permissions | Back/Home |
| Ready | Current preset and capabilities | Preview then Apply |
| Applied | Per-control state and acknowledgement | Individual controls |
| Partial failure | Failed capability remains unchanged; successful/unchanged controls are explicit | Retry failed request or individual control |
| Unavailable | Unknown state; no reliable summary or preset write | Retry individual state read |
| Empty | No app target means no applicable preset | Return to app selection in native UI |

The Failure scenario targets Network for Untrusted, or Sensors for Trusted;
Standard is a no-change success from the initial fixture. It does not model
every failure combination. Unsupported scopes, rejected authorization,
user-switch races and partial changes remain required native tests. Never
substitute a false all-applied status.

## Focus and locale

Order: Back → app/profile → preset → preview cells → Apply → result.
Switches expose name/role/state; sheets restore invoking focus and support
cancel without swiping. Partial result uses an alert and nearby retry.

In the independent Atlas example, permission alternatives are matching full-width
buttons. Approximate precision is a labelled information row, not an editable
field or a selectable pill. Allow and Don’t allow commit their respective
sample states; Close/Back cancels and preserves any previous grant. This visual
contract also applies to peer permission choices in native components. Different
roles (switch, navigation row, information, explicit decision) remain distinct.

Consequences wrap at large text sizes. Do not rely on colour alone. German
is partial draft, RTL is layout stress; capability meanings need fluent review.
Production uses complete localized strings rather than dynamic sentence joins.

## Implementation mapping

Resources/app components: rows, choice, preview and results. Narrow Settings
work: observe/request each inherited capability through authorized APIs.
No umbrella service, cross-profile action, permission database or watcher.
