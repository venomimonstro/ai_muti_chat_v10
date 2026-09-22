import logging
from decimal import Decimal, ROUND_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.ai_registry.models import AIModel
from apps.b2b_api.models import APIUsage
from apps.billing.models import RequestCost
from apps.chat.models import CompareVariant
from apps.image_studio.models import ImageGeneration

from .models import ProviderFundingAccount, ProviderSpend, ProviderSpendReservation
from .services import release_provider_spend, reserve_provider_spend, settle_provider_spend

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
STEP = Decimal("0.000001")


def _decimal(value, default="0"):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(default)


def _fx(snapshot):
    value = _decimal((snapshot or {}).get("fx_rate"), "0")
    return value if value > ZERO else None


def _commercial_fail_closed():
    """Whether the optional owner-side provider funding ledger is mandatory.

    Client payments and provider procurement are separate concerns. A live YooKassa
    checkout must not by itself require a manually maintained ProviderFundingAccount:
    the client chat is already protected by model pricing, margin checks and the
    customer wallet reservation/settlement path. Deployments that actively maintain
    provider funding balances can opt into strict fail-closed behaviour explicitly.
    """
    return bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False))


def _provider_has_procurement(provider):
    return ProviderFundingAccount.objects.filter(provider=provider, active=True).exists()


def _require_procurement(provider):
    configured = _provider_has_procurement(provider)
    if configured:
        return True
    adapter_type = str(getattr(provider, "adapter_type", ""))
    if adapter_type == "echo":
        return False
    if _commercial_fail_closed():
        raise ValidationError(
            f"Коммерческий запрос заблокирован: для провайдера {provider.slug} не настроен закупочный контур"
        )
    logger.warning(
        "Provider funding ledger is not configured; continuing with customer pricing/settlement only provider=%s",
        provider.slug,
    )
    return False


def _release_other(prefix, keep_key=None):
    queryset = ProviderSpendReservation.objects.filter(
        source_key__startswith=prefix,
        state=ProviderSpendReservation.State.ACTIVE,
    )
    if keep_key:
        queryset = queryset.exclude(source_key=keep_key)
    for reservation_id in queryset.values_list("id", flat=True):
        release_provider_spend(reservation_id)


def _ensure(*, provider, expected_rub, snapshot, source_key):
    if not _require_procurement(provider):
        return None
    fx = _fx(snapshot)
    if fx is None:
        if _commercial_fail_closed():
            raise ValidationError(
                f"Коммерческий запрос заблокирован: отсутствует FX snapshot для {provider.slug}"
            )
        return None
    native = (_decimal(expected_rub) / fx).quantize(STEP, rounding=ROUND_UP)
    if native <= ZERO:
        if _commercial_fail_closed() and _decimal(expected_rub) > ZERO:
            raise ValidationError("Не удалось рассчитать закупочный резерв провайдера")
        return None
    try:
        return reserve_provider_spend(provider=provider, amount_native=native, source_key=source_key)
    except ValidationError as exc:
        if _commercial_fail_closed():
            raise
        logger.warning(
            "Optional provider procurement reservation unavailable; continuing client request provider=%s source=%s reason=%s",
            provider.slug,
            source_key,
            exc,
        )
        return None


def _existing_spend(source_type, source_id, customer_charge=None):
    """Return an already-settled provider spend and safely refresh revenue only.

    Django post_save signals fire on unrelated later updates too (for example a
    reconciliation_status update). A completed operation must therefore never be
    settled twice and must not fail merely because its reservation is already closed.
    """
    spend = ProviderSpend.objects.filter(
        source_type=source_type,
        source_id=str(source_id),
    ).first()
    if spend is not None and customer_charge is not None:
        charge = _decimal(customer_charge)
        if charge >= ZERO and spend.customer_charge_rub != charge:
            ProviderSpend.objects.filter(pk=spend.pk).update(customer_charge_rub=charge)
            spend.customer_charge_rub = charge
    return spend


def _settle(*, reservation, provider_cost_rub, customer_charge_rub, snapshot, source_type, source_id, model_slug, provider_request_id="", input_tokens=0, output_tokens=0):
    existing = _existing_spend(source_type, source_id, customer_charge_rub)
    if existing is not None:
        return existing
    if reservation is None:
        if _commercial_fail_closed() and _decimal(provider_cost_rub) > ZERO:
            raise ValidationError("Фактический расход провайдера не имеет закупочного резерва")
        return None
    fx = _fx(snapshot)
    if fx is None:
        if _commercial_fail_closed():
            raise ValidationError("Нельзя закрыть расход провайдера без FX snapshot")
        release_provider_spend(reservation.id)
        return None
    native = (_decimal(provider_cost_rub) / fx).quantize(STEP, rounding=ROUND_UP)
    return settle_provider_spend(
        reservation_id=reservation.id,
        actual_native=native,
        nominal_cost_rub=_decimal(provider_cost_rub),
        customer_charge_rub=_decimal(customer_charge_rub),
        source_type=source_type,
        source_id=source_id,
        model_slug=model_slug,
        provider_request_id=provider_request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@receiver(post_save, sender=RequestCost)
def request_cost_procurement(sender, instance, **kwargs):
    model = AIModel.objects.select_related("provider").filter(slug=instance.price_version.model_slug).first()
    if model is None:
        if _commercial_fail_closed():
            raise ValidationError("Цена ссылается на неизвестную AI-модель")
        return
    if not _require_procurement(model.provider):
        return
    prefix = f"chat:{instance.id}:"
    key = f"{prefix}{instance.price_version_id}"
    if instance.provider_cost_rub is None:
        _release_other(prefix, keep_key=key)
        _ensure(
            provider=model.provider,
            expected_rub=instance.expected_provider_cost_rub or instance.estimated_rub,
            snapshot=instance.pricing_snapshot,
            source_key=key,
        )
        return
    if _existing_spend("chat", instance.id, instance.charged_rub or ZERO) is not None:
        return
    reservation = ProviderSpendReservation.objects.filter(
        source_key=key,
        state=ProviderSpendReservation.State.ACTIVE,
    ).first()
    _settle(
        reservation=reservation,
        provider_cost_rub=instance.provider_cost_rub,
        customer_charge_rub=instance.charged_rub or ZERO,
        snapshot=instance.pricing_snapshot,
        source_type="chat",
        source_id=str(instance.id),
        model_slug=model.slug,
        input_tokens=instance.input_tokens,
        output_tokens=instance.output_tokens,
    )


@receiver(post_save, sender=APIUsage)
def api_usage_procurement(sender, instance, **kwargs):
    model = instance.model
    provider = model.provider
    if not _require_procurement(provider):
        return
    key = f"b2b:{instance.id}"
    if instance.state == APIUsage.State.COMPLETED and _existing_spend(
        "b2b", instance.id, instance.charged_rub
    ) is not None:
        return
    reservation = ProviderSpendReservation.objects.filter(source_key=key).first()
    if instance.state == APIUsage.State.RUNNING:
        if reservation is None:
            _ensure(
                provider=provider,
                expected_rub=instance.estimated_cost_rub,
                snapshot=instance.pricing_snapshot,
                source_key=key,
            )
        return
    if reservation is None or reservation.state != ProviderSpendReservation.State.ACTIVE:
        if _commercial_fail_closed() and instance.state == APIUsage.State.COMPLETED:
            raise ValidationError("B2B расход завершён без активного закупочного резерва")
        return
    if instance.state == APIUsage.State.COMPLETED:
        _settle(
            reservation=reservation,
            provider_cost_rub=instance.provider_cost_rub,
            customer_charge_rub=instance.charged_rub,
            snapshot=instance.pricing_snapshot,
            source_type="b2b",
            source_id=str(instance.id),
            model_slug=model.slug,
            provider_request_id=instance.provider_request_id,
            input_tokens=instance.prompt_tokens,
            output_tokens=instance.completion_tokens,
        )
    elif instance.state == APIUsage.State.FAILED:
        release_provider_spend(reservation.id)


@receiver(post_save, sender=ImageGeneration)
def image_generation_procurement(sender, instance, **kwargs):
    provider = instance.model.provider
    if not _require_procurement(provider):
        return
    key = f"image:{instance.id}"
    if instance.state == ImageGeneration.State.COMPLETED and _existing_spend(
        "image", instance.id, instance.actual_cost_rub or ZERO
    ) is not None:
        return
    reservation = ProviderSpendReservation.objects.filter(source_key=key).first()
    if instance.state == ImageGeneration.State.RUNNING:
        if reservation is None:
            _ensure(
                provider=provider,
                expected_rub=instance.estimated_cost_rub,
                snapshot=instance.price_snapshot,
                source_key=key,
            )
        return
    if reservation is None or reservation.state != ProviderSpendReservation.State.ACTIVE:
        if _commercial_fail_closed() and instance.state == ImageGeneration.State.COMPLETED:
            raise ValidationError("Image расход завершён без активного закупочного резерва")
        return
    if instance.state == ImageGeneration.State.COMPLETED:
        _settle(
            reservation=reservation,
            provider_cost_rub=instance.provider_cost_rub or ZERO,
            customer_charge_rub=instance.actual_cost_rub or ZERO,
            snapshot=instance.price_snapshot,
            source_type="image",
            source_id=str(instance.id),
            model_slug=instance.model.slug,
            provider_request_id=instance.provider_request_id,
        )
    elif instance.state == ImageGeneration.State.FAILED:
        release_provider_spend(reservation.id)


@receiver(post_save, sender=CompareVariant)
def compare_variant_procurement(sender, instance, **kwargs):
    provider = instance.model.provider
    if not _require_procurement(provider):
        return
    key = f"compare:{instance.id}"
    if instance.state == CompareVariant.State.COMPLETED and _existing_spend(
        "compare", instance.id, instance.actual_cost_rub
    ) is not None:
        return
    reservation = ProviderSpendReservation.objects.filter(source_key=key).first()
    if instance.state in {CompareVariant.State.QUEUED, CompareVariant.State.RUNNING}:
        if reservation is None:
            _ensure(
                provider=provider,
                expected_rub=instance.expected_max_rub,
                snapshot=instance.pricing_snapshot,
                source_key=key,
            )
        return
    if reservation is None or reservation.state != ProviderSpendReservation.State.ACTIVE:
        if _commercial_fail_closed() and instance.state == CompareVariant.State.COMPLETED:
            raise ValidationError("Compare расход завершён без активного закупочного резерва")
        return
    if instance.state == CompareVariant.State.COMPLETED:
        _settle(
            reservation=reservation,
            provider_cost_rub=instance.provider_cost_rub,
            customer_charge_rub=instance.actual_cost_rub,
            snapshot=instance.pricing_snapshot,
            source_type="compare",
            source_id=str(instance.id),
            model_slug=instance.model.slug,
            provider_request_id=instance.provider_request_id,
            input_tokens=instance.input_tokens,
            output_tokens=instance.output_tokens,
        )
    elif instance.state == CompareVariant.State.FAILED:
        release_provider_spend(reservation.id)
