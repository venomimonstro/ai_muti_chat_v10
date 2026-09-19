from decimal import Decimal, ROUND_UP

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.ai_registry.models import AIModel
from apps.b2b_api.models import APIUsage
from apps.billing.models import RequestCost
from apps.chat.models import CompareVariant
from apps.image_studio.models import ImageGeneration

from .models import ProviderFundingAccount, ProviderSpendReservation
from .services import release_provider_spend, reserve_provider_spend, settle_provider_spend

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


def _provider_has_procurement(provider):
    return ProviderFundingAccount.objects.filter(provider=provider, active=True).exists()


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
    if not _provider_has_procurement(provider):
        return None
    fx = _fx(snapshot)
    if fx is None:
        return None
    native = (_decimal(expected_rub) / fx).quantize(STEP, rounding=ROUND_UP)
    if native <= ZERO:
        return None
    return reserve_provider_spend(provider=provider, amount_native=native, source_key=source_key)


def _settle(*, reservation, provider_cost_rub, customer_charge_rub, snapshot, source_type, source_id, model_slug, provider_request_id="", input_tokens=0, output_tokens=0):
    if reservation is None:
        return None
    fx = _fx(snapshot)
    if fx is None:
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
    if model is None or not _provider_has_procurement(model.provider):
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
    if not _provider_has_procurement(provider):
        return
    key = f"b2b:{instance.id}"
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
    if not _provider_has_procurement(provider):
        return
    key = f"image:{instance.id}"
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
    if not _provider_has_procurement(provider):
        return
    key = f"compare:{instance.id}"
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
