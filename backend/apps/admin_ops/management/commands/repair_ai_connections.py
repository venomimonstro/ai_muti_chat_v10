import httpx
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ai_registry.adapters import adapter_for
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey, RoutingPolicyVersion
from apps.ai_registry.router import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS
from apps.billing.pricing import active_price


ALIASES = {
    "lite": {"GigaChat", "GigaChat-2"},
    "pro": {"GigaChat-Pro", "GigaChat-2-Pro"},
    "max": {"GigaChat-Max", "GigaChat-2-Max"},
}
SLUGS = {
    "lite": "gigachat-2-lite",
    "pro": "gigachat-2-pro",
    "max": "gigachat-2-max",
}


class Command(BaseCommand):
    help = "Live-repair configured AI provider/model/AUTO routing chain using models actually available to the key"

    @transaction.atomic
    def handle(self, *args, **options):
        provider = Provider.objects.filter(slug="gigachat").first()
        if provider is None:
            raise CommandError("GigaChat provider is not registered")
        key = provider.api_keys.filter(enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY).order_by("priority", "created_at").first()
        if key is None:
            raise CommandError("GigaChat has no healthy enabled authorization key")

        probe = AIModel.objects.filter(provider=provider).exclude(upstream_model="").select_related("provider").first()
        if probe is None:
            raise CommandError("GigaChat has no registered models")
        adapter = adapter_for(probe)
        try:
            response = httpx.get(
                f"{adapter.base_url}/models",
                headers=adapter._headers(),
                timeout=20,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise CommandError(f"GigaChat /models live check failed: {type(exc).__name__}: {exc}") from exc

        returned = {
            str(item.get("id") or item.get("name") or "").strip()
            for item in (payload.get("data") or payload.get("models") or [])
            if isinstance(item, dict)
        }
        returned.discard("")
        self.stdout.write("GigaChat API models: " + (", ".join(sorted(returned)) or "<empty>"))
        if not returned:
            raise CommandError("GigaChat returned an empty model list")

        available_families = {
            family for family, aliases in ALIASES.items() if aliases & returned
        }
        if not available_families:
            raise CommandError("No supported Lite/Pro/Max GigaChat model is available to this key")

        Provider.objects.filter(pk=provider.pk).update(enabled=True, emergency_disabled=False)
        provider.refresh_from_db()

        ready = {}
        for family, slug in SLUGS.items():
            model = AIModel.objects.filter(provider=provider, slug=slug).first()
            available = family in available_families and model is not None
            if available:
                try:
                    active_price(slug)
                except Exception as exc:
                    raise CommandError(f"{slug} is available but has no active commercial price: {exc}") from exc
                AIModel.objects.filter(pk=model.pk).update(enabled=True)
                ready[family] = model
                self.stdout.write(self.style.SUCCESS(f"[READY] {family}: {model.upstream_model}"))
            elif model is not None:
                AIModel.objects.filter(pk=model.pk).update(enabled=False)
                self.stdout.write(f"[OFF] {family}: not returned by this key")

        # Always choose only models that the provider itself returned. If a tier's
        # preferred family is missing, use the nearest available family so AUTO is
        # functional instead of exposing a dead selector.
        weak = ready.get("lite") or ready.get("pro") or ready.get("max")
        medium = ready.get("pro") or ready.get("max") or ready.get("lite")
        high = ready.get("max") or ready.get("pro") or ready.get("lite")
        if not (weak and medium and high):
            raise CommandError("Could not build all three AUTO tiers")

        policy = RoutingPolicyVersion.objects.filter(active=True).first()
        if policy is None:
            policy = RoutingPolicyVersion.objects.create(
                version="admin-routing-v1",
                active=True,
                mode_weights=DEFAULT_WEIGHTS,
                thresholds=DEFAULT_THRESHOLDS,
            )
        thresholds = dict(policy.thresholds or {})
        thresholds["tier_models"] = {
            "economy": weak.slug,
            "balanced": medium.slug,
            "maximum": high.slug,
        }
        RoutingPolicyVersion.objects.filter(pk=policy.pk).update(thresholds=thresholds)
        self.stdout.write(self.style.SUCCESS(
            f"AUTO repaired: weak={weak.slug}, medium={medium.slug}, high={high.slug}"
        ))
