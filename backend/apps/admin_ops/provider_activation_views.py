from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.pricing import active_price, quote, require_margin
from apps.procurement.services import account_available_native, credential_is_configured, default_account

from .openrouter_pricing import sync_openrouter_prices
from .services import audit
from .views import AdminAPIView


SPECIAL_EXTERNAL_PROVIDER_SLUGS = {"gigachat", "openrouter"}


def _is_test_echo(provider):
    return (
        provider.adapter_type == Provider.AdapterType.ECHO
        and provider.slug not in SPECIAL_EXTERNAL_PROVIDER_SLUGS
    )


def _procurement_blocker(provider):
    if _is_test_echo(provider) or not bool(
        getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False)
    ):
        return ""
    account = default_account(provider)
    if account is None:
        return "Не записан закупочный баланс API для этого провайдера"
    if not credential_is_configured(account):
        return "Закупочный аккаунт не связан с рабочим API-ключом"
    if account_available_native(account) <= 0:
        return "Закупочный баланс API исчерпан"
    return ""


def _schedule_provider_verification():
    """Best-effort immediate wake-up; periodic heartbeat remains authoritative."""
    try:
        from .tasks import provider_health_watch_task

        provider_health_watch_task.delay()
    except Exception:
        # A broker outage must not roll back an otherwise valid admin configuration.
        # The minute heartbeat will retry once the worker plane is healthy again.
        pass


class ProviderClientActivationView(AdminAPIView):
    """Make selected provider models eligible for verified customer activation.

    Activation is fail-closed: a model is configured only when the provider has a
    verified credential, an upstream model id, commercially safe pricing, and —
    in production — positive purchased provider capacity. This endpoint never
    clears an OPEN/DEGRADED/UNKNOWN circuit merely because one credential row says
    HEALTHY. A recovered provider must pass the background health + real inference
    proof before model_client_ready() exposes it to customer traffic.
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
        procurement_blocker = _procurement_blocker(provider)
        if procurement_blocker:
            return Response(
                {"detail": procurement_blocker, "code": "provider_procurement_not_ready"},
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

        verification_pending = False
        if activated:
            update_fields = []
            if not provider.enabled:
                provider.enabled = True
                update_fields.append("enabled")
            # Enabling a previously disabled channel starts a new verification cycle.
            # Preserve an existing HEALTHY state, but never promote OPEN/DEGRADED/
            # UNKNOWN solely from a database credential flag.
            if provider.health_state == Provider.HealthState.DISABLED:
                provider.health_state = Provider.HealthState.UNKNOWN
                provider.consecutive_failures = 0
                provider.circuit_opened_until = None
                update_fields.extend(
                    ["health_state", "consecutive_failures", "circuit_opened_until"]
                )
            if update_fields:
                provider.save(update_fields=list(dict.fromkeys(update_fields)))
            verification_pending = provider.health_state != Provider.HealthState.HEALTHY
            if verification_pending:
                transaction.on_commit(_schedule_provider_verification)

        audit(
            request,
            "provider.client_models.activated",
            "provider",
            provider.id,
            {
                "activated": [item["upstream_model"] for item in activated],
                "blocked": blocked,
                "pricing_sync": pricing_sync,
                "verification_pending": verification_pending,
            },
        )
        return Response(
            {
                "provider": provider.slug,
                "provider_enabled": provider.enabled,
                "provider_health": provider.health_state,
                "verification_pending": verification_pending,
                "activated": activated,
                "blocked": blocked,
                "pricing_sync": pricing_sync,
            }
        )
