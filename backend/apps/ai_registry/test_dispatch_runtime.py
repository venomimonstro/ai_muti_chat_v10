import pytest

from . import adapters, dispatch, reliability
from .gigachat_adapter import GigaChatAPIAdapter
from .models import AIModel, Provider, ProviderApiKey


def _provider(slug: str):
    return Provider.objects.create(
        slug=slug,
        name=slug,
        enabled=True,
        adapter_type=Provider.AdapterType.ECHO,
    )


@pytest.mark.django_db
def test_reliability_uses_fail_safe_dispatcher_after_app_startup():
    assert adapters.adapter_for is dispatch.adapter_for
    assert reliability.adapter_for is dispatch.adapter_for


@pytest.mark.django_db
def test_legacy_gigachat_echo_row_never_dispatches_to_echo():
    provider = _provider("gigachat")
    key = ProviderApiKey(provider=provider, label="test")
    key.set_secret("credential")
    key.save()
    model = AIModel.objects.create(
        provider=provider,
        slug="gigachat-runtime-test",
        display_name="System Pro",
        upstream_model="GigaChat-2-Pro",
        enabled=True,
    )

    adapter = dispatch.adapter_for(model)
    assert isinstance(adapter, GigaChatAPIAdapter)
