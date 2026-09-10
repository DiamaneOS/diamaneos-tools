# Journey 2 — Home and app discovery (interface design prototype, synthetic data)

Entry: unlocked home (Launcher3). Goal: find a download, app, or setting
without learning internals. Implementation cards: everyday app flows / native UI integration.
Inherited path: Launcher3 + Settings search + DocumentsUI; compare first.

Normal path: search from home/app drawer ("downloads", "battery", app name) →
ranked results (apps → files → settings) → open. Recent downloads row on
demand, not a persistent watcher. Completion: target opened; query kept for
refinement; Back returns to prior context without losing input.

States: empty (no match → suggest spelling/settings path), loading (brief
skeleton, no spinner trap), error (index unavailable → offer Settings path).
Failure state: search backend unavailable → visible message + direct links to
Files/Settings, never a blank screen.

Keyboard/SR : order search field→results→recent→clear; results announced
with type + position ("App 2 of 5"); no content indexed beyond explicit scope
(sensitive indexing needs separate review per §27.6).

Locale: expanded German labels wrap, never truncate actions; RTL mirrors
result rows; large text keeps targets ≥48dp.

Reuse : Launcher3 + shared search-field/list components; no new indexer
service, no clipboard/storage crawl. A new discovery surface only if this
prototype's task failures (wrong turns, assistance needed) prove search
insufficient — measured in native UI integration.
