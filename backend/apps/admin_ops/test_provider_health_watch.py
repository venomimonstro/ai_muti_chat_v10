from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider
from apps.admin_ops import tasks


@pytest.mark.django_db
def test_provider_health_watch_counts_customer_unready_transport_healthy_provider():
    provider = Provider.objects.create(
        slug="watch-customer-unready",
        name="Watch customer unready",
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
        last_checked_at=timezone.now(),
    )
    AIModel.objects.create(
        provider=provider,
        slug="watch-customer-unready-model",
        display_name="Watch customer unready model",
        upstream_model="upstream-watch",
        enabled=True,
    )

    with (
        patch.object(tasks.cache, "add", return_value=True),
        patch.object(tasks.cache, "delete"),
        patch(
            "apps.ai_registry.reliability.provider_available",
            return_value=False,
        ),
        patch(
            "apps.ai_registry.reliability.check_provider"
        ) as check_provider,
        patch.object(tasks, "_notify_platform_admins", return_value=1) as notify,
    ):
        result = tasks.provider_health_watch_task.run()

    assert result["ready"] == 0
    assert result["unavailable"] == 1
    assert result["checked"] == 0
    check_provider.assert_not_called()
    notify.assert_called_once()
    assert notify.call_args.kwargs["dedupe_key"].startswith("ai-provider-outage:")
