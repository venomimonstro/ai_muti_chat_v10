# Chat daily UX follow-up — 2026-10-02

The user authorized publishing directly to `main`, without a new pull request or CI/Actions configuration. This follow-up includes the preceding chat reliability and daily UX fixes from the existing draft work, plus the changes below. Updating the repository does not establish that a production deployment occurred.

## Fixed behavior

- Selected attachments belong to their conversation and survive switching chats and reloading the page. Only file IDs are persisted locally; authenticated file API requests restore metadata and current processing status. Inaccessible or removed files (403/404/410) are discarded. Transient API failures preserve the stored selection and expose the error instead of silently discarding files.
- A file upload finishing after navigation is saved to its original conversation. An image upload finishing after navigation cannot insert its suggested prompt into another conversation.
- Finishing an accepted send clears the submitted attachment IDs in its original conversation even when the user has switched chats; other selected files remain.
- Starter prompts focus the composer. The profile button now has readable contrast despite shared legacy stylesheet rules.
- Auxiliary data errors no longer promise automatic recovery that the code does not implement.
- The existing local verification script no longer depends on the exact degraded-state wording. No CI or Actions configuration was added or changed.

## Verification of final frontend

- `npm run test:chat`: 14 passed, including attachment persistence, isolation, duplicate IDs and corrupt storage.
- `npm run build`: passed, including TypeScript and production page generation.
- `tests/chat-ux.browser.mjs` against `next start`: 9 scenario groups passed with mocked authenticated APIs and no application page errors. Covered hydration, navigation races, dialogs/search, cost confirmation, extraction readiness, attachment navigation/reload/removal, late image upload into a different empty chat, live and recovered Stop, mobile layout/touch Enter and offline draft preservation.
- Inspected screenshots at 1440×960 and 390×844; added a computed-color assertion for profile text contrast.
- These browser checks verify the actual production frontend with controlled backend responses. They do not verify real LLM providers or payment settlement.

## Backend verification limitation

A fresh targeted backend pytest run was rejected by automatic safety review because execution attempted to contact an external Microsoft telemetry host with unknown payload and authorization. It was not retried or counted as a successful check. No backend source changes were made in this follow-up. Earlier successful targeted results and the 27 unresolved broad-suite failures remain documented in `CHAT_RELIABILITY_AUDIT_2026_10_01.md` and `CHAT_DAILY_UX_2026_10_01.md`.

Production PostgreSQL/Redis concurrency, real LLM responses and wallet/ledger/reservation reconciliation remain unverified. This follow-up does not claim 100% availability or production readiness.
