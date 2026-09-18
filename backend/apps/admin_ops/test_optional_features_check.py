import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_optional_features_gate_requires_web_search_when_other_features_disabled(settings, monkeypatch):
    settings.COMPARE_ENABLED = False
    settings.IMAGES_ENABLED = False
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)

    with pytest.raises(CommandError, match="WEB_SEARCH_BASE_URL"):
        call_command("optional_features_check")

    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "https://search.example.test/api")
    call_command("optional_features_check")
