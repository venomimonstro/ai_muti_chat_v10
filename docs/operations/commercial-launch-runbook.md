# Commercial launch runbook

## 1. Configure production

- Fill provider API keys, exact model IDs and reviewed prices.
- Run `python manage.py bootstrap_catalog` after enabling/changing providers so provider-specific compliance items exist.
- Configure SMTP, YooKassa, fiscalization, acquiring fee version and HTTPS return URL.
- Configure offsite backup target.
- Set `ADMIN_MFA_ENFORCED=true` only after every platform administrator has enrolled TOTP and stored recovery codes securely.

## 2. Evidence gates

In Admin Ops approve compliance signoffs only with concrete evidence references. Required items include seller/tax setup, fiscalization, receipts/refunds, privacy/data flow, public legal documents, global provider review, and a dedicated `provider-terms-<slug>` signoff for every enabled provider.

A signoff is not a substitute for legal/accounting review; it records that the review happened.

## 3. Operational drills

Before commercial launch record:

- a successful full DB + media restore drill no older than 30 days;
- at least one application rollback drill;
- a successful payment reconciliation;
- provider health checks;
- chaos/invariant checks with no double charges or stuck reservations.

## 4. Release gate

Run the local release gate before deployment:

```bash
bash scripts/release_check.sh
```

Then deploy through `scripts/update.sh`; normal deployment rejects destructive migrations and performs DB/media backup before release.

## 5. Final production gate

Run:

```bash
bash scripts/commercial_launch_check.sh
```

This creates `launch-evidence/commercial-launch-<UTC>.json` plus a SHA256 checksum. Commercial traffic stays blocked until every nested gate passes.

The unified audit covers Django deploy checks, configured/healthy AI providers, positive prices, payment production readiness, financial invariants, compliance signoffs, administrator MFA, restore evidence and rollback evidence.

## 6. After launch

Monitor Admin Ops metrics/growth endpoints, provider health, gross margin anomalies, payment reconciliation, disk/media storage and security events. Do not increase traffic while critical alerts or unresolved financial anomalies exist.
