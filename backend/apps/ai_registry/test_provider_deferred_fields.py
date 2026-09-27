import pytest

from .models import Provider


@pytest.mark.django_db
def test_provider_only_queryset_does_not_recurse_on_deferred_credentials():
    provider = Provider.objects.create(
        slug="deferred-provider",
        name="Deferred provider",
        enabled=False,
        credential_env="DEFERRED_PROVIDER_API_KEY",
    )

    loaded = Provider.objects.only(
        "id",
        "slug",
        "enabled",
        "health_state",
    ).get(pk=provider.pk)

    assert loaded.id == provider.id
    assert loaded.slug == "deferred-provider"
    assert "credential_env" in loaded.get_deferred_fields()
    assert "credential_secret" in loaded.get_deferred_fields()
