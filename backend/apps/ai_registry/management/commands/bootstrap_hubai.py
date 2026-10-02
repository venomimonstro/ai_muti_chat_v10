import os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ai_registry.adapters import DeepSeekChatAdapter
from apps.ai_registry.models import Provider, ProviderApiKey


class Command(BaseCommand):
    help = (
        "Bootstrap HubAI from HUBAI_API_KEY, validate the credential, and store it "
        "encrypted in the provider key pool. Does not enable customer traffic."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--label",
            default="HubAI primary",
            help="Provider key label stored in the encrypted key pool",
        )

    def handle(self, *_args, **options):
        secret = os.getenv("HUBAI_API_KEY", "").strip()
        if not secret:
            raise CommandError("HUBAI_API_KEY is not configured")

        base_url = (
            os.getenv("HUBAI_API_BASE_URL", "https://hubai.loe.gg/v1").strip()
            or "https://hubai.loe.gg/v1"
        )
        provider = Provider.objects.filter(slug="hubai").first()
        if provider is None:
            raise CommandError(
                "HubAI provider is not registered; run Django migrations first"
            )

        provider.api_base_url = base_url
        provider.credential_env = "HUBAI_API_KEY"
        provider.health_state = Provider.HealthState.UNKNOWN
        provider.save(
            update_fields=[
                "api_base_url",
                "credential_env",
                "health_state",
            ]
        )

        label = str(options["label"] or "HubAI primary").strip()[:120]
        key = provider.api_keys.filter(label=label).first()
        if key is None:
            key = ProviderApiKey(
                provider=provider,
                label=label,
                enabled=True,
                priority=10,
            )
        key.set_secret(secret)
        key.enabled = True
        key.health_state = ProviderApiKey.HealthState.UNKNOWN
        key.last_error_code = ""
        key.save()

        adapter = DeepSeekChatAdapter(api_key=secret, base_url=base_url)
        health = adapter.health_check()
        now = timezone.now()
        key.last_checked_at = now
        key.last_latency_ms = health.latency_ms

        if not health.healthy:
            key.health_state = ProviderApiKey.HealthState.DEGRADED
            key.last_error_code = str(health.error_code or "hubai_health_failed")[:80]
            key.save(
                update_fields=[
                    "enabled",
                    "health_state",
                    "last_error_code",
                    "last_latency_ms",
                    "last_checked_at",
                ]
            )
            provider.health_state = Provider.HealthState.DEGRADED
            provider.last_checked_at = now
            provider.last_latency_ms = health.latency_ms
            provider.save(
                update_fields=[
                    "health_state",
                    "last_checked_at",
                    "last_latency_ms",
                ]
            )
            raise CommandError(
                f"HubAI credential probe failed: {key.last_error_code}"
            )

        key.health_state = ProviderApiKey.HealthState.HEALTHY
        key.last_error_code = ""
        key.save(
            update_fields=[
                "enabled",
                "health_state",
                "last_error_code",
                "last_latency_ms",
                "last_checked_at",
            ]
        )
        provider.health_state = Provider.HealthState.HEALTHY
        provider.last_checked_at = now
        provider.last_latency_ms = health.latency_ms
        provider.save(
            update_fields=[
                "health_state",
                "last_checked_at",
                "last_latency_ms",
            ]
        )

        self.stdout.write(
            self.style.SUCCESS(
                "HubAI credential is HEALTHY and stored encrypted. "
                "Provider/models remain disabled until pricing and funding are configured."
            )
        )
