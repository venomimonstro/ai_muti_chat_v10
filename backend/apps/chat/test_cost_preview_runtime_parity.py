from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from apps.chat import cost_preview


def _model(slug):
    return SimpleNamespace(
        slug=slug,
        display_name=slug,
        max_output_tokens=8192,
        context_window=32000,
        capabilities=[],
        provider=SimpleNamespace(slug=f"provider-{slug}"),
    )


def test_preview_uses_runtime_output_budget_and_skips_unfunded_candidate():
    primary = _model("primary")
    secondary = _model("secondary")
    route = SimpleNamespace(
        ordered_models=[primary, secondary],
        selected=primary,
        estimated_input_tokens=120,
    )
    conversation = SimpleNamespace(
        routing_mode="auto",
        project_id=None,
        memory_enabled=False,
    )
    quoted_output_tokens = []

    def fake_quote(_price, _input_tokens, output_tokens, **_kwargs):
        quoted_output_tokens.append(output_tokens)
        return SimpleNamespace(
            user_charge_rub=Decimal("1.2500"),
            provider_cost_rub=Decimal("0.5000"),
            pricing_snapshot={},
            fx_snapshot=None,
        )

    with (
        patch.object(cost_preview, "resolve_chat_attachments", return_value=([], [])),
        patch.object(cost_preview, "select_route", return_value=route),
        patch.object(cost_preview, "_existing_history_tokens", return_value=0),
        patch.object(cost_preview, "active_price", side_effect=lambda slug: slug),
        patch.object(cost_preview, "quote", side_effect=fake_quote),
        patch.object(cost_preview, "require_margin", side_effect=lambda value: value),
        patch.object(cost_preview, "quote_has_procurement_capacity", side_effect=[False, True]),
        patch.object(cost_preview.Wallet.objects, "get_or_create", return_value=(object(), False)),
        patch.object(
            cost_preview,
            "spend_guard_snapshot",
            return_value={
                "single_request_limit_rub": None,
                "single_request_balance_percent": Decimal("100"),
                "burst_limit_rub": None,
                "burst_window_minutes": 10,
                "daily_system_limit_rub": None,
            },
        ),
    ):
        result = cost_preview.chat_cost_preview(
            user=object(),
            conversation=conversation,
            content="Проверь маршрут",
        )

    assert quoted_output_tokens == [cost_preview.MAX_OUTPUT_TOKENS, cost_preview.MAX_OUTPUT_TOKENS]
    assert result["selected_model"] == "secondary"
    assert [item["model"] for item in result["models"]] == ["secondary"]
    assert result["estimated_max_rub"] == Decimal("1.2500")
