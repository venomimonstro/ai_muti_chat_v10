import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.ai_registry.models import Provider

from .signals import _require_procurement


@pytest.mark.django_db
@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
def test_legacy_special_external_provider_cannot_use_echo_procurement_bypass():
    provider = Provider.objects.create(
        slug="gigachat",
        name="Legacy GigaChat",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )

    with pytest.raises(ValidationError):
        _require_procurement(provider)


@pytest.mark.django_db
@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
def test_real_internal_echo_provider_remains_exempt_from_procurement():
    provider = Provider.objects.create(
        slug="test-echo-provider",
        name="Test Echo Provider",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )

    assert _require_procurement(provider) is False
