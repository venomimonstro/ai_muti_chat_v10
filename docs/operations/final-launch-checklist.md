# Final launch checklist

Run `python manage.py prelaunch_check --strict` with production settings. A green CI build is
necessary but does not replace the manual evidence below.

## Automated technical gates

- [x] Backend lint, migrations and full tests in CI.
- [x] Frontend lint and production build in CI.
- [x] High-confidence committed-secret scan.
- [x] BOLA/BFLA coverage for user, project, file, B2B and admin resources.
- [x] CSRF middleware, browser security headers and secure production cookie controls.
- [x] SSRF-safe server adapters with fixed configured provider origins.
- [x] File size, MIME, archive, XML and extraction sandbox controls.
- [x] Rate, token, concurrency, budget and reserve cost limits.
- [x] Provider contract, fallback, circuit breaker and synthetic journey tooling.
- [x] Immutable price/FX/markup snapshots and Margin Guard.
- [x] Parallel reserve, webhook order, duplicate refund and reconciliation torture tests.
- [x] Canary/rolling/rollback state machine and feature flags.
- [x] Public status page and audited incident publication.
- [x] Backup/restore scripts with isolated target guard and evidence lifecycle.
- [x] Bounded load smoke runner with error-rate and p95 gates.

## Required deployment evidence

- [ ] Staging deployment uses production topology and isolated credentials.
- [ ] `scripts/load_smoke.py` report meets agreed traffic/p95 thresholds.
- [ ] Canary journey and rollback drill recorded in Admin Ops.
- [ ] Real backup restored within 30 days; RPO/RTO and checksum recorded.
- [ ] Alerts reach the on-call owner and deduplicate repeated provider/cost incidents.
- [ ] Public pricing, examples, FAQ and support entry point reviewed.
- [ ] Mobile onboarding reaches first successful answer in under 60 seconds.

## Launch-blocking human sign-off

- [ ] Production admin MFA enforced and evidenced.
- [ ] Actual YooKassa fee version matches the signed contract.
- [ ] Entity/tax and 54-FZ fiscalization reviewed by an authorized specialist.
- [ ] Payment/refund receipt flow tested on the production YooKassa account.
- [ ] Privacy/data retention and AI-provider data flows approved.
- [ ] AI-provider commercial terms approved for resale/multi-model usage.
- [ ] Offer, privacy policy and refund/chargeback rules published.

Do not convert unchecked legal or operational evidence into a release approval. Record each
sign-off with a link or document identifier, then rerun the strict prelaunch command.
