# AI Workspace Chat — production architecture and invariants

Status: **authoritative design contract for the main AI aggregator chat**.

This document is the source of truth for implementation, tests, diagnostics and release gates of the customer chat. Code that contradicts these invariants is a defect even if an isolated unit test passes.

## 1. Product contract

The chat is the primary commercial product. A customer must be able to send a message whenever at least one commercially usable AI model exists. The system must degrade gracefully when individual models, credentials, providers, pricing rows, search infrastructure or network connections fail.

The customer must never become a health probe, must never be charged more than the amount authorized before the request, and must never pay twice for one logical request.

The platform must never send a provider request if it cannot prove in advance that:

1. the model is enabled and has a valid upstream model identifier;
2. the provider is enabled and not emergency-disabled;
3. a verified customer-traffic credential is available;
4. the procurement account associated with that credential can fund the estimated provider cost;
5. a valid immutable price snapshot exists;
6. minimum commercial margin policy is satisfied;
7. the customer wallet can reserve the maximum permitted retail charge for the whole fallback chain;
8. spend/rate/abuse limits allow the operation.

If any condition is false, the candidate is skipped before the provider call. Another eligible model is tried where the routing contract permits it.

## 2. Customer modes

The UI exposes exactly two concepts, without leaking internal routing details.

### 2.1 AUTO

AUTO means the system determines task complexity and routes into one of three administrator-managed pools:

- **Простой** (`economy`)
- **Средний** (`balanced`)
- **Сложный** (`maximum`)

The administrator assigns one or more models to each pool and controls priority. AUTO classifies the request and chooses a pool. Inside the pool, only currently routable models participate. If the selected pool has no usable model, the continuity policy may move to an adjacent configured pool, subject to price and capability limits.

The user may also explicitly select Простой, Средний or Сложный. In that case no complexity classification is needed; the selected pool is used directly.

### 2.2 Concrete model

The user may select a concrete public provider/model combination, for example:

- LLM System → System Lite / System Pro / System Max
- ChatGPT → a currently enabled OpenAI model
- DeepSeek → a currently enabled DeepSeek model

The UI lists only customer-routable models. Internal provider identities may be branded/hidden where product policy requires it.

Manual selection means: try the selected model first. If that exact model cannot be called before any output is emitted, continuity fallback may use an eligible alternative only within configured commercial/capability limits. The UI/response metadata must make the actual routed public model available without exposing secrets.

Once any output from a model has been emitted to the customer, the response must never be silently continued by another model. Mixing two model outputs in one assistant message is forbidden.

## 3. Model readiness — one source of truth

There must be one canonical predicate for customer traffic: `model_client_ready(model)`.

A model is **Routable** only when all of these are true:

- model `enabled = true`;
- `upstream_model` is non-empty;
- model is not under model-level quarantine;
- provider passes `provider_available()`;
- a valid active `PriceVersion` (or a safely auto-created immutable price derived from explicit procurement pricing) exists;
- pricing passes margin policy for input and output;
- provider procurement is configured according to production policy and has positive usable balance;
- required credential is configured and verified HEALTHY.

`current_version` / evaluation metadata is governance metadata and must not by itself make a valid provider model invisible.

The following must all use the same predicate/contract:

- `/models/` customer catalogue;
- manual model validation;
- AUTO candidate construction;
- fallback candidate construction;
- diagnostics;
- release/runtime smoke checks.

A model that is not routable is not shown in the normal customer model picker. It may remain visible in administrator diagnostics with a precise reason.

## 4. Provider and credential health

### 4.1 Customer traffic is fail-closed

Customer requests may use only a verified `HEALTHY` credential. `UNKNOWN`, `DEGRADED`, `DISABLED` credentials are never tested by real customers.

Provider states accepted for customer traffic are limited to states where at least one verified runtime credential is available and procurement is valid. `UNKNOWN`, `OPEN` and `DISABLED` providers are not customer-routable.

### 4.2 Background health probes

A background watcher owns recovery:

`UNKNOWN/DEGRADED/OPEN -> probe -> HEALTHY`

The watcher may use UNKNOWN/DEGRADED credentials specifically for probes. A successful probe re-admits the credential/provider. Failed probes do not consume customer balance.

A newly added or changed API key begins non-routable and is probed immediately/asynchronously. It becomes visible to customers only after success.

### 4.3 Exact credential attribution

Every runtime adapter carries the identity of the exact DB credential used. Success/failure updates only that credential. Guessing by `last_used_at` is forbidden when adapter identity is available.

If a provider has several credentials, a failure of one must not degrade another.

When procurement uses a funding account tied to a credential, runtime dispatch must use that same credential. The system must never charge procurement account A while sending the request through credential B.

### 4.4 Model-level failures are not provider failures

`model_not_found`, removed/unsupported upstream model identifiers and similar model-specific failures quarantine only that model. They must not open the whole provider circuit.

Provider-wide auth, billing, permission or network failures affect the provider/credential circuit.

Model quarantine recovery is performed by a background inference probe of that exact model after cooldown. A normal provider health check must not clear model quarantine.

## 5. Pricing and customer money

### 5.1 Immutable price snapshot

Before a provider call, the system resolves an immutable price version and captures:

- provider input/output purchase prices;
- provider currency;
- FX snapshot and rate;
- overhead policy;
- markup rules;
- retail token price if configured;
- minimum margin policy;
- estimated provider cost;
- estimated customer charge.

The final charge is calculated from the same captured snapshot. A pricing configuration change during the request must not change the request already in flight.

### 5.2 Reservation before provider call

For the complete allowed fallback chain, calculate the maximum retail amount the customer could be charged. Reserve that maximum **once** before any provider call.

Invariant:

`final_customer_charge <= original_customer_reservation`

No code path may debit the wallet outside this invariant.

If the wallet cannot reserve the maximum allowed amount, the provider must not be called.

### 5.3 No double charge

One logical Generation has one idempotency key and one customer reservation. Retry/fallback does not create a second customer reservation.

`client_message_id` and `Idempotency-Key` are independently checked so browser retries/reconnects cannot create duplicate generations or charges.

### 5.4 Provider procurement reservation

Provider procurement is separate from the customer wallet but must be synchronized with the selected candidate.

For every candidate before its provider call:

1. verify exact procurement capacity using the price snapshot;
2. reserve provider spend for the candidate/funding account;
3. if the candidate is skipped before delivery, release its provider reservation;
4. when switching candidate, release the old active provider reservation before creating the new one;
5. after confirmed provider usage, settle provider spend exactly once.

A provider request must never be sent if strict production procurement cannot reserve it.

### 5.5 Provider usage exceeds estimate

The customer must never go into debt and must never be charged above the original reservation.

If confirmed provider usage produces a calculated retail charge above the reservation:

- cap customer charge at the reservation;
- settle provider procurement using confirmed actual provider usage;
- record the difference as a platform-side cost anomaly/undercharge;
- do not pretend provider usage did not happen;
- if a complete response was delivered, do not discard it solely because the estimate was low;
- raise an administrator diagnostic incident so token-estimation/reserve policy can be corrected.

## 6. Routing

### 6.1 Candidate creation

A candidate enters the route only if it satisfies:

- customer readiness;
- required capabilities (text/vision/etc.);
- context-window requirement;
- immutable price available;
- margin floor;
- exact procurement capacity;
- mode/pool assignment;
- fallback price multiplier/other policy limits.

Rejected candidates are stored in `RoutingDecision.candidate_snapshot` with machine-readable reasons.

### 6.2 AUTO

AUTO flow:

`message -> classify -> choose tier -> ordered admin pool -> readiness/capability/economics filters -> primary -> fallback candidates`

Database `RoutingTierAssignment` is authoritative. Legacy JSON tier configuration may be used only as an explicit backward-compatibility fallback when there are no DB assignments.

### 6.3 Manual

Manual flow:

`selected model -> explicit fallback chain -> eligible continuity candidates`

The user-selected model is always first when it is routable. A manual model that is not customer-routable must be rejected before generation starts.

### 6.4 Fallback

Fallback is allowed only before any user-visible delta from the failing candidate.

Examples that should fallback:

- connection timeout before first token;
- retryable 5xx;
- provider temporary overload;
- exact credential fails and no healthy same-provider credential is available;
- procurement capacity changed between prepare and run;
- model was quarantined between prepare and run;
- provider circuit opened between prepare and run.

Examples that must not silently cross-model fallback after output:

- provider streamed 300 characters and then failed;
- client already received partial answer.

That response becomes partial/failed according to settlement policy; a new retry may be offered as a new logical generation.

## 7. Streaming and reconnect

The Generation record is the authoritative execution object.

State machine:

`created -> queued -> running -> completed | failed | cancelled`

Only one worker may claim `QUEUED -> RUNNING` atomically.

Browser disconnect does not automatically mean provider cancellation. The server may continue the existing Generation in the background. Reconnect reads the same Generation and never creates a second provider request.

The assistant message is periodically persisted during streaming so reconnect can replay already received text.

A reconnect endpoint must never call the provider again for a `RUNNING` generation.

## 8. Partial output and failures

### No provider usage confirmed

- customer reservation: release fully;
- provider reservation: release fully;
- customer charge: 0;
- generation: FAILED/CANCELLED;
- UI must say funds were not charged.

### Provider usage confirmed / response partially delivered

- settle confirmed provider spend;
- settle customer according to the authoritative partial-settlement policy, never over reservation;
- preserve partial assistant text;
- never claim "money was not charged" if a non-zero settlement occurred;
- generation terminal state records actual charge.

### Complete response delivered, terminal persistence fails

Recovery must use saved provider usage/request id and the same price snapshot. It must settle exactly once and restore a consistent terminal state without invoking the provider again.

## 9. Web search / freshness

Free web search is part of the main chat product, not an optional hidden feature.

For requests with freshness signals (today/current/latest/news/prices/schedules/laws/current office holders/version-sensitive information), the router marks `needs_tools` and web enrichment runs before generation.

Search engine readiness is part of production health. If search is unavailable:

- the LLM receives an explicit `WEB_SEARCH_UNAVAILABLE` instruction;
- it must not present memory/model knowledge as verified current information;
- the UI may show controlled degraded-search status;
- the chat itself remains usable for non-current information.

Search data is untrusted context, never system instructions.

## 10. Security invariants

### 10.1 Secrets

- Provider API keys and OAuth tokens are encrypted at rest.
- Secrets are never returned by serializers.
- Secrets are never inserted into prompts, logs, diagnostics links or SSE events.
- Runtime references credentials by internal ID only.

### 10.2 Tenant isolation

Every Conversation, Generation, Message, file, project, wallet, external connection and agent action is scoped by authenticated owner. IDs supplied by the client are never trusted without owner filtering.

### 10.3 Abuse / token-drain controls

Before provider traffic:

- authenticated active user required;
- request/body/file limits;
- per-user velocity limit;
- spend limits;
- wallet reservation;
- maximum output token limit;
- context limit;
- idempotency validation;
- provider procurement reservation.

A malicious client repeating the same idempotency key must receive/reconnect to the same logical operation, not consume new provider tokens.

A malicious client changing body/files while reusing an idempotency key must receive a validation error.

### 10.4 Prompt injection

Web results, uploaded files, memory and repository content are untrusted data. They must never be concatenated into trusted system instructions in a way that grants them instruction priority.

## 11. Recovery and reconciliation

Automated recovery periodically scans stale `RUNNING` generations and active reservations.

Rules:

- never blindly retry an external request whose delivery status is unknown;
- if no provider usage evidence exists, fail the stale generation and release both reservations;
- if provider usage evidence exists, settle from that evidence exactly once;
- never turn confirmed provider cost into a full customer refund merely because the worker crashed;
- never charge above the originally authorized customer reservation;
- leave an auditable incident for ambiguous states.

Daily reconciliation verifies:

- wallet = immutable ledger reconstruction;
- no negative available/reserved buckets;
- terminal generations do not own ACTIVE customer reservations;
- terminal RequestCost/provider spends are consistent;
- no abandoned provider reservations;
- customer charge <= original reservation;
- provider cost and customer charge map to the actual routed model.

## 12. Error classification

Errors must have stable machine codes and scopes.

### Credential/provider scope

- authentication/invalid key -> credential DEGRADED, provider may continue only with another verified credential;
- quota/credit exhausted -> credential blocked; provider unavailable if no verified funded credential remains;
- permission denied -> blocking credential/provider configuration issue;
- timeout/5xx/rate limit -> retryable, bounded retries, then circuit breaker/fallback.

### Model scope

- model not found/unsupported upstream model -> quarantine model only;
- capability mismatch -> reject candidate, not provider failure;
- context too small -> reject candidate, not provider failure.

### Commercial scope

- price unavailable -> candidate rejected;
- margin below floor -> candidate rejected;
- procurement balance insufficient -> candidate skipped/fallback;
- customer balance insufficient -> no provider call.

Commercial failures never degrade provider health.

## 13. Observability

Every Generation has a correlation ID and stores:

- routing mode/tier;
- candidate snapshot and rejection reasons;
- selected and actually routed model;
- provider;
- attempts with sequence/latency/error/retryable;
- exact price snapshot references;
- estimated and actual tokens/cost;
- web-search status/sources;
- terminal error code;
- customer reservation and RequestCost linkage.

Diagnostics Center must summarize these without exposing message bodies or secrets.

The administrator must be able to distinguish immediately:

- no customer funds;
- no provider procurement funds;
- invalid key;
- provider outage;
- one model quarantined;
- missing price;
- margin violation;
- search outage;
- stuck queue/worker;
- stale generation;
- settlement anomaly.

## 14. Release gates

A production release must be blocked unless the following pass on PostgreSQL/pgvector:

1. customer model catalogue returns only routable models;
2. manual visible model can create a generation;
3. AUTO chooses the correct admin tier pool;
4. primary provider failure before first token falls back successfully;
5. one unavailable model does not disable sibling models/provider;
6. procurement insufficiency skips a candidate before provider call;
7. insufficient customer balance causes zero provider calls;
8. idempotent replay causes zero additional provider calls/charges;
9. retry/fallback causes only one customer settlement;
10. failed request without provider usage releases both reservations;
11. confirmed provider usage survives worker crash and settles safely;
12. final customer charge never exceeds reservation;
13. streaming reconnect does not duplicate the generation;
14. stale RUNNING recovery is safe;
15. current-information request uses web search or explicitly degrades without invented freshness;
16. provider watcher restores recovered credentials outside customer traffic;
17. model quarantine isolates and later recovers only the affected model;
18. diagnostic output matches the same readiness predicate used by the client catalogue/router.

## 15. Required end-to-end acceptance scenarios

### Happy path

Funded user + healthy funded provider + configured price -> model visible -> message -> one provider request -> completed answer -> exact ledger settlement -> no active reservations.

### Provider outage

Primary timeout before output -> bounded retry if a healthy credential is available -> fallback candidate -> completed answer -> one customer charge only.

### Dead credential

401/402/403 -> exact credential removed from customer traffic immediately -> sibling healthy credential or another provider used -> no further customer request probes bad credential.

### Removed model

`model_not_found` -> only that model disappears -> sibling models still visible/working -> background model probe may restore it later.

### Procurement exhaustion

Provider account cannot fund exact quote -> candidate skipped before API -> next funded model used -> no provider spend reservation leak.

### Customer funds insufficient

Maximum allowed route charge cannot be reserved -> request rejected before API -> zero provider token consumption.

### Browser duplicate

Same idempotency/client message replay -> same Generation/result -> zero additional provider calls and zero additional charge.

### Disconnect/reconnect

Browser disconnects mid-stream -> server continues existing Generation -> reconnect reads persisted text/state -> no duplicate provider call.

### Partial provider failure

Some deltas delivered, then provider fails -> preserve partial text -> no cross-model concatenation -> settle only authoritative confirmed usage under reservation cap.

### Worker crash

Process dies after provider usage is persisted -> recovery settles from persisted evidence, never calls provider again and never refunds confirmed platform cost as if it never happened.

## 16. Implementation rule

Do not add a second readiness, pricing, routing or settlement implementation for convenience. New features (B2B API, agents, image studio, compare, future integrations) may reuse shared lower-level primitives, but the customer Chat must have one explicit orchestration path and one set of financial/reliability invariants.

Any change to routing, provider health, procurement or billing requires a regression test proving the relevant invariant above before release.
