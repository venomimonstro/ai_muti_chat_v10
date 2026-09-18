from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_ops.models import BackupRecord, ReleaseRecord


class Command(BaseCommand):
    help = "Record reviewed restore/rollback drill evidence for strict commercial prelaunch"

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=("restore", "rollback"))
        parser.add_argument("--evidence", required=True)
        parser.add_argument("--actor", default="")
        parser.add_argument("--commit-sha", default="")
        parser.add_argument("--size-bytes", type=int, default=0)
        parser.add_argument("--checksum", default="")

    def _actor(self, username):
        queryset = (
            User.objects.filter(status=User.Status.ACTIVE)
            .filter(Q(is_staff=True) | Q(role=User.Role.PLATFORM_ADMIN))
            .distinct()
        )
        if username:
            user = queryset.filter(username=username).first()
            if not user:
                raise CommandError(
                    "Requested evidence actor is not an active platform administrator"
                )
            return user
        user = queryset.order_by("date_joined", "id").first()
        if not user:
            raise CommandError(
                "No active platform administrator exists to own drill evidence"
            )
        return user

    def handle(self, *args, **options):
        evidence = options["evidence"].strip()
        if not evidence or len(evidence) > 400:
            raise CommandError("--evidence must contain a 1..400 character reference")
        actor = self._actor(options["actor"].strip())
        now = timezone.now()

        if options["kind"] == "restore":
            checksum = options["checksum"].strip().lower()
            invalid_checksum = checksum and (
                len(checksum) != 64
                or any(ch not in "0123456789abcdef" for ch in checksum)
            )
            if invalid_checksum:
                raise CommandError("--checksum must be a SHA256 hex digest")
            if options["size_bytes"] < 0:
                raise CommandError("--size-bytes cannot be negative")
            record = BackupRecord.objects.create(
                kind=BackupRecord.Kind.FULL,
                status=BackupRecord.Status.RESTORED,
                storage_reference=evidence,
                size_bytes=options["size_bytes"] or None,
                checksum_sha256=checksum,
                notes="Isolated restore drill completed and reviewed",
                requested_by=actor,
                started_at=now,
                completed_at=now,
                verified_at=now,
                restored_at=now,
            )
            self.stdout.write(
                self.style.SUCCESS(f"Recorded restore drill: {record.id}")
            )
            return

        commit_sha = options["commit_sha"].strip()
        if not commit_sha or len(commit_sha) > 64:
            raise CommandError("Rollback evidence requires --commit-sha")
        version = f"rollback-drill-{now.strftime('%Y%m%dT%H%M%SZ')}"
        record = ReleaseRecord.objects.create(
            version=version,
            commit_sha=commit_sha,
            environment="production",
            state=ReleaseRecord.State.ROLLED_BACK,
            rollout_percent=0,
            health_snapshot={"drill": True, "evidence": evidence},
            notes="Rollback drill completed and reviewed",
            created_by=actor,
        )
        self.stdout.write(self.style.SUCCESS(f"Recorded rollback drill: {record.id}"))
