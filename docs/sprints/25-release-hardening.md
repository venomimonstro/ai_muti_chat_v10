# Sprint 25 — Release Hardening

Sprint 25 completes the engineering roadmap with production security controls, status and
incident communication, bounded load tooling, backup/restore evidence, deployment topology,
runbooks and an executable launch gate.

## Commands

```bash
# Structural checks before a release
python manage.py prelaunch_check

# Production gate: security settings + legal/drill evidence
python manage.py prelaunch_check --strict

# Non-billable readiness load smoke
python scripts/load_smoke.py --base-url https://api.example.ru \
  --requests 1000 --concurrency 50 --max-p95-ms 750

# Billable B2B contract smoke requires explicit consent
python scripts/load_smoke.py --base-url https://api.example.ru \
  --requests 20 --concurrency 2 --api-key "$API_KEY" --model model-slug --allow-billable
```

The strict gate deliberately fails on missing MFA, unsafe cookies/HTTPS, shared secrets,
disabled live-payment fiscalization, incomplete compliance sign-offs, stale restore evidence or
missing rollback evidence. These are launch blockers, not warnings.

Operational documents:

- `docs/operations/prelaunch-edge-matrix.md`
- `docs/operations/incident-runbook.md`
- `docs/operations/support-runbook.md`
- `docs/operations/backup-restore-drill.md`
- `docs/operations/fiscal-legal-signoff.md`
- `docs/operations/final-launch-checklist.md`
