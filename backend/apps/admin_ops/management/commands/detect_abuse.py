from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_ops.models import SecurityEvent
from apps.b2b_api.models import APIUsage
from apps.chat.models import Generation


class Command(BaseCommand):
    help = "Detect suspicious usage patterns and open deduplicated security events"

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=1)
        parser.add_argument("--generation-threshold", type=int, default=120)
        parser.add_argument("--failed-api-threshold", type=int, default=50)

    def handle(self, *args, **options):
        actor = User.objects.filter(role=User.Role.PLATFORM_ADMIN, status=User.Status.ACTIVE).first()
        if actor is None:
            self.stdout.write(self.style.WARNING("No platform admin; abuse scan cannot create events"))
            return
        since = timezone.now() - timedelta(hours=max(1, options["hours"]))
        created = 0

        heavy = (
            Generation.objects.filter(created_at__gte=since)
            .values("user_message__conversation__owner_id")
            .annotate(requests=Count("id"), cost=Sum("actual_cost_rub"))
            .filter(requests__gte=options["generation_threshold"])
        )
        for row in heavy:
            user_id = row["user_message__conversation__owner_id"]
            created += self._open(
                actor,
                category="usage_velocity",
                user_id=user_id,
                summary="Unusual chat generation velocity",
                details={"hours": options["hours"], "requests": row["requests"], "cost_rub": str(row["cost"] or 0)},
            )

        failed_api = (
            APIUsage.objects.filter(created_at__gte=since, state=APIUsage.State.FAILED)
            .values("organization_id", "api_key_id")
            .annotate(failures=Count("id"))
            .filter(failures__gte=options["failed_api_threshold"])
        )
        for row in failed_api:
            created += self._open(
                actor,
                category="api_failure_velocity",
                user_id=None,
                summary="Unusual B2B API failure velocity",
                details={
                    "hours": options["hours"],
                    "organization_id": str(row["organization_id"]),
                    "api_key_id": str(row["api_key_id"]),
                    "failures": row["failures"],
                },
            )

        self.stdout.write(self.style.SUCCESS(f"Abuse scan complete; opened={created}"))

    def _open(self, actor, *, category, user_id, summary, details):
        window = timezone.now().strftime("%Y%m%d%H")
        fingerprint = f"{category}:{user_id or details.get('api_key_id')}:{window}"
        exists = SecurityEvent.objects.filter(
            category=category,
            status__in=[SecurityEvent.Status.OPEN, SecurityEvent.Status.INVESTIGATING],
            details__fingerprint=fingerprint,
        ).exists()
        if exists:
            return 0
        details = {**details, "fingerprint": fingerprint}
        SecurityEvent.objects.create(
            category=category,
            severity=SecurityEvent.Severity.WARNING,
            user_id=user_id,
            summary=summary,
            details=details,
            created_by=actor,
        )
        return 1
