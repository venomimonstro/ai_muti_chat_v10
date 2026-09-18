from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import MarkupRuleVersion, PriceVersion

from .services import audit
from .views import PricingControlView


def _decimal(value, label, *, minimum=None, allow_blank=False):
    if allow_blank and (value is None or str(value).strip() == ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}: укажите число") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{label}: значение должно быть не меньше {minimum}")
    return parsed


def _effective_datetime(value):
    raw = str(value or "").strip()
    result = parse_datetime(raw) if raw else timezone.now()
    if raw and result is None:
        raise ValueError("Некорректная дата начала действия")
    if result and timezone.is_naive(result):
        result = timezone.make_aware(result)
    return result


def _validate_markup_scope(scope_type, scope_key):
    allowed = {
        MarkupRuleVersion.Scope.GLOBAL,
        MarkupRuleVersion.Scope.PROVIDER,
        MarkupRuleVersion.Scope.MODEL,
        MarkupRuleVersion.Scope.OPERATION,
    }
    if scope_type not in allowed:
        raise ValueError("Для панели доступны области: глобально, провайдер, модель или операция")
    if scope_type == MarkupRuleVersion.Scope.GLOBAL:
        if scope_key:
            raise ValueError("Для глобального правила ключ не нужен")
        return ""
    if not scope_key:
        raise ValueError("Для выбранной области укажите ключ")
    if scope_type == MarkupRuleVersion.Scope.PROVIDER and not Provider.objects.filter(slug=scope_key).exists():
        raise ValueError("Провайдер не найден")
    if scope_type == MarkupRuleVersion.Scope.MODEL and not AIModel.objects.filter(slug=scope_key).exists():
        raise ValueError("Модель не найдена")
    if scope_type == MarkupRuleVersion.Scope.OPERATION and scope_key not in {
        "chat",
        "compare",
        "compare_synthesis",
        "image",
        "public_api",
    }:
        raise ValueError("Неизвестный тип операции")
    return scope_key


class PricingManagementView(PricingControlView):
    def get(self, request):
        response = super().get(request)
        by_id = {str(item.id): item for item in PriceVersion.objects.filter(active=True)}
        for row in response.data.get("active_prices", []):
            price = by_id.get(str(row["id"]))
            if price is None:
                continue
            row["input_rub_per_million"] = str(price.input_rub_per_million)
            row["output_rub_per_million"] = str(price.output_rub_per_million)
        return response

    @transaction.atomic
    def post(self, request):
        if request.data.get("kind") == "markup_rule":
            return self._create_markup_rule(request)
        return self._create_price(request)

    def _create_price(self, request):
        model_slug = str(request.data.get("model", "")).strip()
        model = AIModel.objects.filter(slug=model_slug).select_related("provider").first()
        if model is None:
            return Response({"detail": "Модель не найдена"}, status=404)
        try:
            input_cost = _decimal(
                request.data.get("input_rub_per_million"),
                "Себестоимость входных токенов",
                minimum=Decimal("0.0001"),
            )
            output_cost = _decimal(
                request.data.get("output_rub_per_million"),
                "Себестоимость выходных токенов",
                minimum=Decimal("0.0001"),
            )
            base_markup = _decimal(
                request.data.get("markup_percent", "100"),
                "Базовая наценка",
                minimum=Decimal("0"),
            )
            effective_from = _effective_datetime(request.data.get("effective_from"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        version = PriceVersion.objects.create(
            model_slug=model.slug,
            input_rub_per_million=input_cost,
            output_rub_per_million=output_cost,
            provider_currency="RUB",
            markup_percent=base_markup,
            active=True,
            effective_from=effective_from,
        )
        audit(
            request,
            "pricing.version_created",
            "price_version",
            version.id,
            {
                "model": model.slug,
                "provider": model.provider.slug,
                "input_rub_per_million": str(input_cost),
                "output_rub_per_million": str(output_cost),
                "base_markup_percent": str(base_markup),
                "effective_from": effective_from.isoformat(),
            },
        )
        return Response(
            {
                "id": version.id,
                "model": version.model_slug,
                "input_rub_per_million": version.input_rub_per_million,
                "output_rub_per_million": version.output_rub_per_million,
                "markup_percent": version.markup_percent,
                "effective_from": version.effective_from,
            },
            status=201,
        )

    def _create_markup_rule(self, request):
        scope_type = str(request.data.get("scope_type", "")).strip()
        scope_key = str(request.data.get("scope_key", "")).strip()
        try:
            scope_key = _validate_markup_scope(scope_type, scope_key)
            markup = _decimal(
                request.data.get("markup_percent"),
                "Наценка",
                minimum=Decimal("0"),
                allow_blank=True,
            )
            multiplier = _decimal(
                request.data.get("price_multiplier", "1"),
                "Множитель цены",
                minimum=Decimal("0.0001"),
            )
            effective_from = _effective_datetime(request.data.get("effective_from"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        if markup is None and multiplier == Decimal("1"):
            return Response(
                {"detail": "Правило должно менять наценку или множитель цены"}, status=400
            )
        rule = MarkupRuleVersion.objects.create(
            scope_type=scope_type,
            scope_key=scope_key,
            markup_percent=markup,
            price_multiplier=multiplier,
            active=True,
            effective_from=effective_from,
            reason=str(request.data.get("reason", "")).strip()[:300],
        )
        audit(
            request,
            "pricing.markup_rule_created",
            "markup_rule_version",
            rule.id,
            {
                "scope_type": rule.scope_type,
                "scope_key": rule.scope_key,
                "markup_percent": str(rule.markup_percent) if rule.markup_percent is not None else None,
                "price_multiplier": str(rule.price_multiplier),
                "effective_from": rule.effective_from.isoformat(),
            },
        )
        return Response(
            {
                "id": rule.id,
                "scope_type": rule.scope_type,
                "scope_key": rule.scope_key,
                "markup_percent": rule.markup_percent,
                "price_multiplier": rule.price_multiplier,
                "effective_from": rule.effective_from,
            },
            status=201,
        )
