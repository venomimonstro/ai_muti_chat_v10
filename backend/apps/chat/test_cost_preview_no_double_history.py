from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from apps.chat import cost_preview


def test_chat_cost_preview_does_not_add_recent_history_twice():
    provider = SimpleNamespace(slug="provider-a")
    model = SimpleNamespace(
        slug="model-a",
        display_name="Model A",
        upstream_model="model-a",
        provider=provider,
        max_output_tokens=1024,
        context_window=32768,
        capabilities=["text", "streaming"],
    )
    route = SimpleNamespace(
        ordered_models=[model],
        estimated_input_tokens=100,
    )
    conversation = SimpleNamespace(
        project_id=None,
        memory_enabled=False,
        routing_mode="balanced",
    )
    quoted_inputs = []

    def fake_quote(_price, input_tokens, _output_tokens, **_kwargs):
        quoted_inputs.append(input_tokens)
        return SimpleNamespace(user_charge_rub=Decimal("1.0000"))

    wallet = SimpleNamespace()
    guard = {
        "single_request_limit_rub": None,
        "single_request_balance_percent": Decimal("100"),
        "burst_limit_rub": None,
        "burst_window_minutes": 10,
        "daily_system_limit_rub": None,
    }

    with (
        patch.object(cost_preview, "resolve_chat_attachments", return_value=([], [])),
        patch.object(cost_preview, "select_route", return_value=route),
        patch.object(cost_preview, "active_price", return_value=object()),
        patch.object(cost_preview, "quote", side_effect=fake_quote),
        patch.object(cost_preview, "require_margin", side_effect=lambda value: value),
        patch.object(cost_preview, "quote_has_procurement_capacity", return_value=True),
        patch.object(cost_preview.Wallet.objects, "get_or_create", return_value=(wallet, False)),
        patch.object(cost_preview, "spend_guard_snapshot", return_value=guard),
        patch.object(
            cost_preview,
            "_existing_history_tokens",
            side_effect=AssertionError("recent history must not be added twice"),
        ),
    ):
        result = cost_preview.chat_cost_preview(
            user=object(),
            conversation=conversation,
            content="hello",
            file_ids=[],
        )

    # 100 router-estimated tokens + 1200 old-message budget + 1200 summary budget
    # + 512 system/context envelope. Recent history is already inside the 100.
    assert quoted_inputs == [3012]
    assert result["estimated_max_rub"] == Decimal("1.0000")
