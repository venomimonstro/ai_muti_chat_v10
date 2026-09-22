from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.pricing import active_price, quote, require_margin

from .openrouter_pricing import sync_openrouter_prices
from .services import audit
from .views import AdminAPIView


class ProviderClientActivationView(AdminAPIView):
    """Make selected provider models actually usable by client chat.

    Activation is fail-closed: a model is exposed to clients only when the
    provider has a working credential, the exact upstream model/version exists,
    and both input/output pricing pass the configured margin floor.
    """

    @transaction.atomic
    def post(self, request, provider_slug):
        provider = Provider.objects.select_for_update().filter(slug=provider_slug).first()
        if provider is None:
            return Response({"detail": "Провайдер не найден"}, status=404)

        raw_ids = request.data.get("model_ids") or []
        if not isinstance(raw_ids, list) or not raw_ids:
            return Response({"detail": "Выберите хотя бы одну модель"}, status=400)
        model_ids = [str(value).strip() for value in raw_ids if str(value).strip()][:50]

        healthy_pool_key = ProviderApiKey.objects.filter(
            provider=provider,
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        ).exists()
        credential_ready = healthy_pool_key or (
            provider.credential_configured() and provider.health_state == Provider.HealthState.HEALTHY
        )
        if not credential_ready:
            return Response(
                {
                    "detail": "Нет рабочего API-ключа. Сначала перепроверьте ключ провайдера.",
                    "code": "provider_credential_not_ready",
                },
                status=409,
            )
        if provider.emergency_disabled:
            return Response(
                {"detail": "Провайдер аварийно отключён", "code": "provider_emergency_disabled"},
                status=409,
            )

        pricing_sync = {"verified": [], "rejected": [], "skipped": True}
        if provider.slug == "openrouter":
            try:
                pricing_sync = sync_openrouter_prices(provider, model_ids)
            except Exception as exc:
                pricing_sync = {
                    "verified": [],
                    "rejected": [
                        {
                            "model": "*",
                            "detail": f"Не удалось синхронизировать цены OpenRouter: {exc}",
                        }
                    ],
                    "skipped": False,
                }

        pricing_rejections = {
            str(item.get("model") or ""): str(item.get("detail") or "")
            for item in pricing_sync.get("rejected", [])
        }
        global_pricing_error = pricing_rejections.get("*", "")

        models = list(
            AIModel.objects.select_for_update().filter(
                provider=provider,
                upstream_model__in=model_ids,
            )
        )
        by_upstream = {item.upstream_model: item for item in models}
        activated = []
        blocked = []

        for upstream in model_ids:
            model = by_upstream.get(upstream)
            reasons = []
            if global_pricing_error:
                reasons.append(global_pricing_error)
            if pricing_rejections.get(upstream):
                reasons.append(pricing_rejections[upstream])
            if model is None:
                reasons.append("Модель ещё не сохранена в каталоге")
            else:
                if not model.upstream_model.strip():
                    reasons.append("Не указан upstream model ID")
                if not model.current_version_id:
                    reasons.append("Нет активной версии модели")
                try:
                    price = active_price(model.slug)
                    require_margin(
                        quote(
                            price,
                            1_000_000,
                            0,
                            provider_slug=provider.slug,
                            model_slug=model.slug,
                        )
                    )
                    require_margin(
                        quote(
                            price,
                            0,
                            1_000_000,
                            provider_slug=provider.slug,
                            model_slug=model.slug,
                        )
                    )
                except ValidationError as exc:
                    reasons.extend(exc.messages or ["Цена модели не настроена или маржа ниже допустимой"])
                except Exception:
                    reasons.append("Цена модели не настроена или маржа ниже допустимой")

            if reasons:
                blocked.append({"model": upstream, "reasons": list(dict.fromkeys(reasons))})
                continue

            if not model.enabled:
                model.enabled = True
                model.save(update_fields=["enabled"])
            activated.append(
                {
                    "id": str(model.id),
                    "slug": model.slug,
                    "upstream_model": model.upstream_model,
                }
            )

        if activated:
            update_fields = []
            if not provider.enabled:
                provider.enabled = True
                update_fields.append("enabled")
            # A verified healthy pool key is stronger evidence than a stale
            # provider-level circuit state left over from an earlier failure.
            # Reset the circuit when exposing verified models to clients so
            # AUTO Router does not immediately reject the provider as unavailable.
            if healthy_pool_key:
                provider.health_state = Provider.HealthState.HEALTHY
                provider.consecutive_failures = 0
                provider.circuit_opened_until = None
                provider.last_checked_at = timezone.now()
                update_fields.extend([
                    "health_state",
                    "consecutive_failures",
                    "circuit_opened_until",
                    "last_checked_at",
                ])
            if update_fields:
                provider.save(update_fields=list(dict.fromkeys(update_fields)))

        audit(
            request,
            "provider.client_models.activated",
            "provider",
            provider.id,
            {
                "activated": [item["upstream_model"] for item in activated],
                "blocked": blocked,
                "pricing_sync": pricing_sync,
            },
        )
        return Response(
            {
                "provider": provider.slug,
                "provider_enabled": provider.enabled,
                "provider_health": provider.health_state,
                "activated": activated,
                "blocked": blocked,
                "pricing_sync": pricing_sync,
            }
        )
