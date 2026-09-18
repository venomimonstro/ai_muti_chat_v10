from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.b2b_api.models import APIUsage

from .tests import account, registry


@pytest.mark.django_db(transaction=True)
def test_context_limit_is_rejected_before_usage_or_wallet_charge():
    model = registry()
    model.context_window = 96
    model.max_output_tokens = 32
    model.save(update_fields=["context_window", "max_output_tokens"])
    user, _organization, _key, secret = account("context-limit")
    before = user.wallet.available_rub

    response = APIClient().post(
        "/v1/chat/completions",
        {
            "model": model.slug,
            "messages": [{"role": "user", "content": "очень длинный контекст " * 200}],
            "max_completion_tokens": 32,
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {secret}",
    )

    assert response.status_code == 400
    assert response.data["error"]["code"] == "context_length_exceeded"
    assert APIUsage.objects.count() == 0
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal(before)
