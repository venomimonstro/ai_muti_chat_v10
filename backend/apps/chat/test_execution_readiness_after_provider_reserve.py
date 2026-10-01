from decimal import Decimal

from django.test import TestCase, override_settings

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.ai_registry.reliability import provider_available
from apps.procurement.models import ProviderFundingAccount

from .runtime_readiness import execution_model_ready


class ExecutionReadinessAfterProviderReserveTests(TestCase):
    @override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    def test_request_does_not_invalidate_itself_after_consuming_free_provider_balance(self):
        provider = Provider.objects.create(
            slug="execution-funded-provider",
            name="Execution Funded Provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        key = ProviderApiKey(provider=provider, label="primary", enabled=True)
        key.set_secret("sk-execution-funded")
        key.health_state = ProviderApiKey.HealthState.HEALTHY
        key.save()
        ProviderFundingAccount.objects.create(
            provider=provider,
            api_key=key,
            label="Primary funding",
            currency="USD",
            active=True,
            is_default=True,
            funded_native=Decimal("1.000000"),
            reserved_native=Decimal("1.000000"),
            spent_native=Decimal("0.000000"),
        )
        model = AIModel.objects.create(
            provider=provider,
            slug="execution-funded-model",
            display_name="Execution Funded Model",
            upstream_model="provider-model-id",
            enabled=True,
        )

        # Catalog/preflight readiness correctly sees no *new* free provider funds.
        self.assertFalse(provider_available(provider))
        # The already-funded in-flight request must still be allowed to call the API.
        self.assertTrue(execution_model_ready(model))

    @override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    def test_execution_still_blocks_dead_provider_after_reservation(self):
        provider = Provider.objects.create(
            slug="execution-dead-provider",
            name="Execution Dead Provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            enabled=True,
            health_state=Provider.HealthState.OPEN,
        )
        key = ProviderApiKey(provider=provider, label="primary", enabled=True)
        key.set_secret("sk-execution-dead")
        key.health_state = ProviderApiKey.HealthState.HEALTHY
        key.save()
        ProviderFundingAccount.objects.create(
            provider=provider,
            api_key=key,
            label="Primary funding",
            currency="USD",
            active=True,
            is_default=True,
            funded_native=Decimal("1.000000"),
            reserved_native=Decimal("1.000000"),
            spent_native=Decimal("0.000000"),
        )
        model = AIModel.objects.create(
            provider=provider,
            slug="execution-dead-model",
            display_name="Execution Dead Model",
            upstream_model="provider-model-id",
            enabled=True,
        )

        self.assertFalse(execution_model_ready(model))
