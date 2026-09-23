from types import SimpleNamespace

from .cost_preview import _public_model
from .models import Conversation


def _model(provider_slug, slug="internal-renamed", display_name="Internal Renamed"):
    return SimpleNamespace(
        slug=slug,
        display_name=display_name,
        provider=SimpleNamespace(slug=provider_slug),
    )


def _conversation(mode):
    return SimpleNamespace(routing_mode=mode)


def test_internal_provider_is_hidden_even_after_model_slug_rename():
    model = _model("gigachat")
    assert _public_model(model, _conversation(Conversation.RoutingMode.ECONOMY)) == (
        "System Lite",
        "System Lite",
    )
    assert _public_model(model, _conversation(Conversation.RoutingMode.BALANCED)) == (
        "System Pro",
        "System Pro",
    )
    assert _public_model(model, _conversation(Conversation.RoutingMode.MAXIMUM)) == (
        "System Max",
        "System Max",
    )


def test_external_model_keeps_public_catalog_identity():
    model = _model("openrouter", slug="public-model", display_name="Public Model")
    assert _public_model(model, _conversation(Conversation.RoutingMode.BALANCED)) == (
        "public-model",
        "Public Model",
    )
