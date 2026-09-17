from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ai_registry.models import RoutingPolicyVersion


class Command(BaseCommand):
    help = "Install and activate the commercial AUTO Router v2 policy"

    @transaction.atomic
    def handle(self, *args, **options):
        RoutingPolicyVersion.objects.filter(active=True).update(active=False)
        policy, created = RoutingPolicyVersion.objects.get_or_create(
            version="commercial-router-v2",
            defaults={
                "active": True,
                "mode_weights": {
                    "economy": {"quality": 0.30, "cost": 0.50, "latency": 0.15, "health": 0.05},
                    "balanced": {"quality": 0.55, "cost": 0.25, "latency": 0.15, "health": 0.05},
                    "maximum": {"quality": 0.80, "cost": 0.05, "latency": 0.10, "health": 0.05},
                },
                "thresholds": {
                    "default_quality": 0.55,
                    "economy_min_quality": 0.60,
                    "fallback_price_multiplier": 1.50,
                    "unknown_latency_ms": 1500,
                    "classification_version": "rules-v2",
                    "token_estimator": "calibrated-v1",
                },
            },
        )
        if not created and not policy.active:
            policy.active = True
            policy.save(update_fields=["active"])
        self.stdout.write(self.style.SUCCESS(f"AUTO Router v2 active: {policy.version}"))
