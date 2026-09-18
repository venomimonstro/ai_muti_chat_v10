from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response

from apps.ai_registry.models import AIModel
from apps.billing.models import PriceVersion

from .services import audit
from .views import PricingControlView


def _decimal(value, label, *, minimum=None):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}: укажите число") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{label}: значение должно быть не меньше {minimum}")
    return parsed


class PricingManagementView(PricingControlView):
    @transaction.atomic
    def post(self, request):
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
            markup = _decimal(
                request.data.get("markup_percent", "100"),
                "Наценка",
                minimum=Decimal("0"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        effective_raw = str(request.data.get("effective_from", "")).strip()
        effective_from = parse_datetime(effective_raw) if effective_raw else timezone.now()
        if effective_raw and effective_from is None:
            return Response({"detail": "Некорректная дата начала действия цены"}, status=400)
        if effective_from and timezone.is_naive(effective_from):
            effective_from = timezone.make_aware(effective_from)
        version = PriceVersion.objects.create(
            model_slug=model.slug,
            input_rub_per_million=input_cost,
            output_rub_per_million=output_cost,
            provider_currency="RUB",
            markup_percent=markup,
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
                "markup_percent": str(markup),
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
