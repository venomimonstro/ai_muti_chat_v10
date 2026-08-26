# Incident response runbook

## Severity and ownership

- **SEV-1:** money integrity, data exposure, authentication bypass or full outage. Stop rollout,
  invoke provider kill switches where relevant, publish a critical status incident and preserve
  logs. Owner: platform administrator.
- **SEV-2:** major degradation, repeated provider failures or payment reconciliation drift.
  Freeze related changes, route traffic away from the component and publish an update.
- **SEV-3:** localized failure with a safe workaround. Track in Admin Ops and normal support SLA.

## First 15 minutes

1. Create a SecurityEvent or provider incident and record the correlation IDs.
2. If a release is involved, move it to `rolled_back`; do not edit historical release evidence.
3. For suspected abuse, use `contain` to atomically block the user and revoke funded-org keys.
4. For provider failure, use the provider emergency kill switch and verify fallback health.
5. For money incidents, stop the affected payment/generation path and run reconciliation. Never
   correct the wallet directly; use an explicit ledger adjustment after review.
6. Publish a sanitized StatusIncident. Do not publish emails, prompts, provider payloads or keys.

## Diagnosis and recovery

Use Admin Ops Request Inspector, Finance, Incidents and Audit. Compare wallet cached values with
the immutable ledger, provider usage with RequestCost snapshots and YooKassa webhook data with a
fresh provider GET. Recovery requires a synthetic journey, readiness check and evidence that the
error rate returned to baseline.

## Closure

Resolve the public incident, preserve timestamps and write a short postmortem: impact, timeline,
root cause, detection gap, corrective actions and owner. Rotate any credential that might have
been exposed and verify the secret scan before the next release.
