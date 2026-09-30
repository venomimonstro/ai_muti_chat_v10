from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.ai_registry.adapters import ProviderError

from .dev_provider_retry import generate_with_key_failover


def _model():
    return SimpleNamespace(
        slug="system-pro",
        upstream_model="upstream-pro",
        provider=SimpleNamespace(id=1, slug="provider"),
    )


def test_retry_uses_new_adapter_after_retryable_key_failure():
    model = _model()
    first = Mock()
    first.generate.side_effect = ProviderError("rate limited", code="rate_limited", retryable=True)
    second = Mock()
    expected = SimpleNamespace(text="ok")
    second.generate.return_value = expected
    factory = Mock(side_effect=[first, second])

    with patch("apps.agents.dev_provider_retry.record_failure") as failure, patch(
        "apps.agents.dev_provider_retry.record_success"
    ) as success, patch("apps.agents.dev_provider_retry.provider_available", return_value=True):
        result, attempts = generate_with_key_failover(
            model=model,
            messages=[{"role": "user", "content": "fix"}],
            max_output_tokens=100,
            adapter_factory=factory,
            attempts=3,
        )

    assert result is expected
    assert attempts == 2
    assert factory.call_count == 2
    failure.assert_called_once()
    success.assert_called_once()


def test_non_retryable_provider_error_is_not_replayed():
    model = _model()
    adapter = Mock()
    adapter.generate.side_effect = ProviderError("bad key", code="invalid_api_key", retryable=False)
    factory = Mock(return_value=adapter)

    with patch("apps.agents.dev_provider_retry.record_failure") as failure:
        with pytest.raises(ProviderError, match="bad key"):
            generate_with_key_failover(
                model=model,
                messages=[],
                max_output_tokens=100,
                adapter_factory=factory,
                attempts=3,
            )

    factory.assert_called_once()
    failure.assert_called_once()


def test_retry_stops_when_provider_has_no_healthy_spare():
    model = _model()
    adapter = Mock()
    error = ProviderError("temporary", code="server_error", retryable=True)
    adapter.generate.side_effect = error
    factory = Mock(return_value=adapter)

    with patch("apps.agents.dev_provider_retry.record_failure"), patch(
        "apps.agents.dev_provider_retry.provider_available", return_value=False
    ):
        with pytest.raises(ProviderError) as caught:
            generate_with_key_failover(
                model=model,
                messages=[],
                max_output_tokens=100,
                adapter_factory=factory,
                attempts=3,
            )

    assert caught.value is error
    factory.assert_called_once()
