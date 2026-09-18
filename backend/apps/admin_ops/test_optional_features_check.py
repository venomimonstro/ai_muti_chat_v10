import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_optional_features_gate_requires_web_search_when_other_features_disabled(settings, monkeypatch):
    settings.COMPARE_ENABLED = False
    settings.IMAGES_ENABLED = False
    settings.B2B_API_ENABLED = True
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)

    with pytest.raises(CommandError, match="WEB_SEARCH_BASE_URL"):
        call_command("optional_features_check", "--skip-live-web-probe")

    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "https://search.example.test/api")
    call_command("optional_features_check", "--skip-live-web-probe")


@pytest.mark.django_db
def test_optional_features_gate_blocks_disabled_public_b2b_api(settings, monkeypatch):
    settings.COMPARE_ENABLED = False
    settings.IMAGES_ENABLED = False
    settings.B2B_API_ENABLED = False
    monkeypatch.setenv("WEB_SEARCH_BASE_URL", "https://search.example.test/api")

    with pytest.raises(CommandError, match="B2B OpenAI-compatible API выключен"):
        call_command("optional_features_check", "--skip-live-web-probe")
