import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_ops.models import OperationalDrillEvidence


class Command(BaseCommand):
    help = "Record immutable commercial drill evidence"

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=OperationalDrillEvidence.Kind.values)
        parser.add_argument("--evidence", required=True)
        parser.add_argument("--actor", default="")
        parser.add_argument("--checksum", default="")
        parser.add_argument("--notes", default="")

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
                    "Requested actor is not an active platform administrator"
                )
            return user
        user = queryset.order_by("date_joined", "id").first()
        if not user:
            raise CommandError("No active platform administrator exists")
        return user

    def handle(self, *args, **options):
        evidence = options["evidence"].strip()
        if not evidence or len(evidence) > 400:
            raise CommandError("--evidence must contain 1..400 characters")
        checksum = options["checksum"].strip().lower()
        if checksum and (
            len(checksum) != 64
            or any(ch not in "0123456789abcdef" for ch in checksum)
        ):
            raise CommandError("--checksum must be a SHA256 hex digest")
        if not checksum:
            path = Path(evidence)
            if path.is_file():
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        item = OperationalDrillEvidence.objects.create(
            kind=options["kind"],
            evidence_reference=evidence,
            checksum_sha256=checksum,
            notes=options["notes"][:2000],
            passed_at=timezone.now(),
            recorded_by=self._actor(options["actor"].strip()),
        )
        self.stdout.write(
            self.style.SUCCESS(f"Recorded {item.kind} drill: {item.id}")
        )
