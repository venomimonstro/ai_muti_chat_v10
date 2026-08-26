# Backup and restore drill

Run at least monthly and before a high-risk release. Backups must live in a private account or
bucket isolated from application credentials and production write access.

```bash
export DATABASE_URL='postgresql://.../production'
bash scripts/backup_postgres.sh /srv/private-backups/aiworkspace-2026-08-26.dump
```

Store the printed SHA-256 and private storage reference in Admin Ops. On an isolated host, create
an empty database whose name ends in `_restore_drill`, then run:

```bash
export RESTORE_DRILL_DATABASE_URL='postgresql://.../aiworkspace_restore_drill'
bash scripts/restore_drill.sh /srv/private-backups/aiworkspace-2026-08-26.dump
```

The suffix guard prevents accidental restore into the production database. After the script,
verify counts for users, wallets, ledger, payments, conversations and files; run a synthetic chat
with an Echo provider; confirm no outbound payment/provider credentials are enabled. Record RPO,
RTO, checksum, operator and result through the backup lifecycle:

`requested → running → succeeded → verified → restored`.

A backup is not considered valid until a real isolated restore reaches `restored`.
