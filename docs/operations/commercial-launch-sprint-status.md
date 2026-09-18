# Commercial launch status — Sprints 47–66

Updated: 2026-09-18

| Sprint | Status | Notes |
|---|---|---|
| 47 Public Commercial Website | CODE COMPLETE | Public `/`, pricing, FAQ, legal navigation, product/API messaging. |
| 48 Auth & Registration UX | CODE COMPLETE | Separate login/register, password recovery, email verification, legal acceptance. |
| 49 Client Workspace Professional UX | CODE COMPLETE | Modular chat, Markdown/code/tables, edit/regenerate, stop, routing/cost metadata. |
| 50 Frontend Architecture Cleanup | CODE COMPLETE | Modular workspace introduced; legacy workspace retained at `/app/legacy` for rollback/fallback. |
| 51 Customer Account Cabinet | CODE COMPLETE | Account, sessions, password/MFA surfaces, export and account deletion. |
| 52 Wallet & Payment UX | CODE COMPLETE | Wallet, paid/promo/reserved balances, top-up, payments and return page. |
| 53 Admin Console Foundation | CODE COMPLETE | `/admin-console` and navigation over Admin Ops API. |
| 54 Admin Executive Dashboard | CODE COMPLETE | Business/operations overview using existing executive metrics. |
| 55 Admin Users & Support | CODE COMPLETE | Users, detail/actions and support queue. |
| 56 Admin Providers, Models & Pricing | CODE COMPLETE | Provider health/control and pricing visibility. |
| 57 Admin Finance & Payments | CODE COMPLETE | Finance/payment/ledger views. |
| 58 Admin Operations & Security | CODE COMPLETE | Security, releases, backups, audit, feature/compliance operations. |
| 59 Analytics & Commercial Funnel | CODE COMPLETE | ProductEvent ingestion, funnel dashboard and frontend event tracking. |
| 60 SEO & Commercial Content | CODE COMPLETE | Sitemap, robots, API landing and use-case pages. |
| 61 Mobile & Cross-Browser QA | AUTOMATED CONTRACT READY | Responsive CSS implemented; final physical browser/device matrix must be executed on deployed build. |
| 62 End-to-End Commercial QA | AUTOMATED CONTRACT READY | `scripts/commercial_e2e_smoke.sh`; real deployed run required. |
| 63 Production Configuration | CODE COMPLETE / CONFIG REQUIRED | Runtime SMTP/public URL/legal settings wired. Real credentials and seller data required. |
| 64 Production Drills | GATED | Automated load/chaos/E2E evidence plus mandatory reviewed provider/payment/restore/rollback drills. |
| 65 Legal & Provider Sign-Off | GATED | Dynamic provider sign-offs and published legal-page verification. Human legal/provider approvals remain mandatory. |
| 66 Final Commercial Launch Gate | READY TO RUN | `scripts/commercial_launch_check.sh` blocks until release, E2E, finance, drills, legal and compliance all pass. |

## Commands before launch

```bash
bash scripts/release_check.sh
bash scripts/production_drills.sh --confirm-production-drills
bash scripts/commercial_launch_check.sh
```

Manual reviewed drill evidence is recorded with:

```bash
bash scripts/record_manual_drill.sh provider_outage <evidence> [sha256]
bash scripts/record_manual_drill.sh duplicate_webhook <evidence> [sha256]
bash scripts/record_manual_drill.sh payment_failure <evidence> [sha256]
bash scripts/record_manual_drill.sh refund <evidence> [sha256]
```

Restore and rollback evidence must be recorded via `record_operational_drill` after the real isolated drills.

## Launch rule

Do not enable public paid acquisition until both release and commercial launch gates return PASS on the actual production checkout. Code-complete status is not equivalent to production evidence.
