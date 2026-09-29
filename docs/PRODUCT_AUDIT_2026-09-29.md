# Product audit — stability, GitHub, Dev Studio, Agent Studio, client UX

Date: 2026-09-29
Scope: commercial client cabinet and critical execution paths. This document records confirmed code findings and required runtime evidence. It does not replace `docs/SPRINT_PLAN.md` and does not mark Sprint 74/75 complete.

## Product principles

1. The primary UI must show one obvious next action; technical details are progressive disclosure.
2. A control in the client cabinet must correspond to real persisted/runtime behavior. No decorative or pseudo-settings.
3. External integrations fail closed for dangerous actions but must expose an actionable diagnostic state to the user.
4. Partial failure of one external connection must not unnecessarily disable unrelated working connections.
5. Tenant ownership, explicit confirmation, idempotency, billing limits and immutable operation logs remain mandatory.
6. Release status is evidence-based: no runtime PASS without server output.

## Confirmed findings fixed in main

### P0/P1 — GitHub integration

- `/github/connect/` was hidden behind the global fail-closed guard. When GitHub was disabled or incomplete the client received `503` instead of the intended diagnostic `{configured:false}` state. The read-only connection-status endpoint is now available while all GitHub data/mutation endpoints remain guarded.
- Commercial launch can no longer pass with `GITHUB_REQUIRED_FOR_LAUNCH=true` while GitHub App configuration is disabled/incomplete. The launch script blocks early.
- GitHub OAuth cancellation now returns the user to `/app/projects` with an actionable state instead of exposing a raw API 400 page.
- Organization installations now use explicit user-scoped OAuth verification. Existing organization rows remain fail-closed until reconnection; verified organization installations can be used.
- If one installation is broken/requires reconnection, repository discovery uses partial-failure handling so healthy installations remain usable.
- Projects UI now explains server-not-configured, login-needed, connected, cancelled and partial-failure states rather than collapsing them into a generic failure.

### P0/P1 — Agent/Dev runtime stability

- Agent runtime PostgreSQL row locking was hardened: `select_for_update(of=("self",))` locks only `AgentRun`, avoiding `FOR UPDATE` over nullable outer-joined relations (`agent`, `team`, `project`). This removes a production-only class of PostgreSQL failures at run start.
- Regression coverage was added for the PostgreSQL locking behavior.
- Dev Studio no longer hides normal owner projects merely because GitHub is not yet connected. The start flow is now `Project -> GitHub -> Task` with one next action.
- Dev Studio technical readiness details remain available under disclosure but do not dominate the primary workflow.

### P1 — client UI/UX

- Mobile client navigation was reduced from a long horizontal strip of unlabeled icons to four primary product destinations plus an explicit `More` sheet for secondary sections.
- Dev Studio primary copy and states were simplified around the user's task rather than internal agent/team entities.

### P1 — Agent Studio correctness

- The advanced source selector previously did not reliably persist the selected sources as the runtime tool policy. It now explicitly patches `tool_policy`.
- `Files` previously could be selected without binding the new agent to a project, while runtime file retrieval requires `agent.project_id`. The wizard now requires/selects an owner project for project files.
- The unsupported `My site` source was removed from the simple creation wizard until a real connection/runtime path exists.
- The apparent schedule selector was removed from initial creation because it only wrote prose into instructions and did not create `AgentSchedule`. Scheduling is now described as a separate post-creation action instead of a fake persisted setting.
- Project choices in this flow are restricted to owner projects to match backend authorization and avoid post-submit permission errors.

## Security invariants preserved

- GitHub mutation/read paths remain behind `github_guard`.
- Sensitive repository paths and likely secrets remain blocked by default.
- GitHub organization access requires successful user-scoped verification; old rows are not silently trusted.
- Repository writes still require explicit write enablement and explicit mutation confirmation.
- Agent runtime billing reservation/settlement and provider spend accounting were not bypassed by the UX changes.
- No change marks Sprint 74 or 75 complete without runtime evidence.

## Remaining launch-blocking verification

These are not code-complete claims until run on the production host:

1. Pull current `main` and run `scripts/update.sh`.
2. Full release gate must pass, including PostgreSQL backend suite, migration drift, audits, frontend production build/lint/runtime smoke.
3. GitHub production configuration must have all required App fields, correct Setup URL/OAuth callback URL and appropriate `contents` permissions.
4. Test one personal repository and one organization repository end-to-end: connect -> list -> bind -> health -> write-ready -> Dev Studio task.
5. Run Agent Runtime/Dev Runtime drill with a real worker, sandbox and provider.
6. Run client-cabinet commercial E2E and paid provider/billing E2E.
7. Run release-candidate evidence script and retain generated logs/checksums.

## Next audit targets (P1 before broad redesign)

- Agent detail: simplify activation/readiness/schedule controls and ensure every visible control maps to persisted runtime behavior.
- Run detail: reduce internal step terminology; make approval, failure recovery and final artifact the dominant actions.
- Chat: verify external research progress, retry/cancel/recovery and billing confirmation UX under real streaming failures.
- Files/RAG: user-facing processing failure/retry states and project storage limits.
- Images: provider failure/refund/retry states and mobile result handling.
- Wallet/billing: payment return, reconciliation lag, receipt/refund visibility and duplicate-submit protection in the client UI.
- Accessibility: keyboard/focus handling for mobile `More` sheet and modal/drawer flows.

## Runtime status

Code audit/fixes: IN PROGRESS / patched in `main`.
Production runtime evidence after these patches: NOT YET PROVIDED.
Sprint 74: READY TO RUN, not DONE.
Sprint 75: READY TO RUN, not DONE.
