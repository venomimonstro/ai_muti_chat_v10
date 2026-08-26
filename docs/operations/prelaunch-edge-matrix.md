# Pre-launch edge-case matrix

This matrix is the mandatory regression scope before every production release. Automated rows
must pass before release; manual rows require evidence in the Admin Ops sign-off or drill record.

| Area | Edge case | Expected result | Evidence |
| --- | --- | --- | --- |
| Auth | Anonymous object access | `401/403`, no object metadata | ACL API tests |
| Auth | Cross-user/project UUID | `404`, no BOLA disclosure | Projects/files/chat tests |
| Auth | Blocked account | Login and platform-admin access denied | Account/Admin Ops tests |
| Sessions | Password change | Current session retained, old password invalid | Account tests |
| Sessions | Logout all | Every active server session removed | Account tests |
| CSRF | Session mutation without token | Rejected by Django CSRF middleware | Django security checks |
| Browser | XSS payload in content | React escaping plus restrictive CSP | Frontend build/CSP test |
| Files | MIME spoof | Rejected before persistence | Files tests |
| Files | Oversized upload | Rejected by request and file caps | Files tests |
| Files | ZIP traversal/bomb | Rejected by archive controls | Files tests |
| Files | XML entity/DOCTYPE | Extraction stopped as unsafe | Files tests |
| Files | Cross-tenant vector search | ACL applied before ranking | RAG/search tests |
| Chat | Provider fails before output | Prompt retained, reserve released | Streaming tests |
| Chat | Provider fails mid-stream | Partial response retained, no hidden loss | Streaming tests |
| Chat | Retry/fallback | No duplicate message or debit | Reliability tests |
| Chat | Usage exceeds reserve | Hard stop, no overcharge | Cost protection tests |
| Routing | Provider circuit open | Candidate excluded/fallback used | Router tests |
| Routing | Price changes in flight | Settlement uses immutable snapshot | Billing tests |
| Compare | One branch fails | Other results remain; hard reserve settles safely | Compare tests |
| Images | Invalid binary/oversize | Result rejected, reservation released | Image tests |
| Payments | Duplicate create | Same payment and provider call | YooKassa tests |
| Payments | Forged webhook | No credit without provider recheck | YooKassa tests |
| Payments | Duplicate webhook | One event and one credit | YooKassa tests |
| Payments | Reordered cancellation | Success cannot be downgraded | YooKassa tests |
| Refunds | Duplicate refund | One provider call and one paid debit | YooKassa tests |
| Ledger | Parallel reserves | Available balance never negative | PostgreSQL torture test |
| Ledger | Cached balance mismatch | Alert/manual review, no silent rewrite | Reconciliation tests |
| B2B | Reused idempotency key/new body | `409`, no second charge | B2B tests |
| B2B | RPM/concurrency/budget/IP | Hard limit before provider request | B2B tests |
| Admin | Non-admin control-plane request | `403` | Admin Ops tests |
| Admin | Mass provider kill switch | Only explicit IDs changed and audited | Admin Ops tests |
| Release | Draft jumps to stable | `409` | Release state-machine tests |
| Release | Canary rollback | 0% and immutable audit event | Release tests/drill record |
| Backup | Restore before verify | `409` | Backup state-machine tests |
| Backup | Wrong restore target | Script refuses DB without `_restore_drill` suffix | Restore runbook |
| Status | Public incident | Sanitized message visible, internals hidden | Status tests |
| Secrets | Committed high-confidence token/key | Release check fails | `scripts/security_scan.sh` |
| Load | Readiness under bounded concurrency | Error rate and p95 below thresholds | Load report artifact |
