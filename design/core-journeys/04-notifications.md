# Journey 4 — Notification troubleshooting (interface design prototype, synthetic data)

Entry: "Why did notifications stop?" from app's notification settings.
Goal: understand the actual cause and fix only that. Implementation: notification compatibility.
Inherited path: notification channels + profile/distributor state; guidance
reads state, never silently writes global exemptions.

Normal path: diagnosis card names the known cause ("profile stopped",
"distributor missing", "battery optimization for this app", "optional Play
absent") → one scoped action → retest hint. Completion: test notification
path stated; Back returns without changing unrelated settings.

States: loading (checking profile/app/distributor) → known-cause vs
diagnostic-possibility distinguished; error (cannot determine) → safe
manual checklist, no global toggles. Failure state: stopped profile →
explains limitation + offers profile start, never disables security/battery
policy globally.

Keyboard/SR : order diagnosis→action→retest; cause announced as plain
outcome + action; no color-only status.

Locale: cause strings externalized with context (button vs description);
German expansions verified; no concatenation.

Reuse : Settings/notification controllers + shared diagnosis/action
components; guidance proposes scoped user actions only. UnifiedPush explained
as compatible-apps-only, never universal push.
