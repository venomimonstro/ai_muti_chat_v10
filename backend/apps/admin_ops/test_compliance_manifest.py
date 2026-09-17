import pytest

from apps.ai_registry.models import Provider

from .compliance_manifest import (
    bootstrap_compliance_manifest,
    provider_terms_key,
    required_compliance_keys,
)
from .models import ComplianceSignoff


@pytest.mark.django_db
def test_enabled_provider_gets_individual_terms_signoff():
    provider = Provider.objects.create(
        slug="commercial-test",
        name="Commercial Test",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        priority=10,
    )
    created = bootstrap_compliance_manifest()
    key = provider_terms_key(provider.slug)
    assert created >= 1
    assert key in required_compliance_keys()
    assert ComplianceSignoff.objects.filter(key=key).exists()


@pytest.mark.django_db
def test_disabled_provider_does_not_block_terms_gate():
    provider = Provider.objects.create(
        slug="disabled-test",
        name="Disabled Test",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=False,
        priority=10,
    )
    bootstrap_compliance_manifest()
    assert provider_terms_key(provider.slug) not in required_compliance_keys()
