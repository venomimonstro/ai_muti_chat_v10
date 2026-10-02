from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.ai_registry.models import AIModel, Provider


class CustomerAIReadinessCommandTests(TestCase):
    def setUp(self):
        AIModel.objects.all().update(enabled=False)
        Provider.objects.all().update(enabled=False)

    def _model(self):
        provider = Provider.objects.create(
            slug="readiness-command-provider",
            name="Readiness command provider",
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        return AIModel.objects.create(
            provider=provider,
            slug="readiness-command-model",
            display_name="Readiness command model",
            upstream_model="readiness-command-v1",
            enabled=True,
        )

    def test_fails_when_no_enabled_customer_models_exist(self):
        with self.assertRaises(CommandError):
            call_command("customer_ai_readiness_check", stdout=StringIO())

    def test_fails_when_enabled_models_are_not_customer_ready(self):
        self._model()
        with patch(
            "apps.admin_ops.management.commands.customer_ai_readiness_check.model_client_ready",
            return_value=False,
        ):
            with self.assertRaises(CommandError):
                call_command("customer_ai_readiness_check", stdout=StringIO())

    def test_passes_when_at_least_one_model_is_customer_ready(self):
        model = self._model()
        output = StringIO()
        with patch(
            "apps.admin_ops.management.commands.customer_ai_readiness_check.model_client_ready",
            side_effect=lambda candidate: candidate.pk == model.pk,
        ):
            call_command("customer_ai_readiness_check", stdout=output)

        rendered = output.getvalue()
        self.assertIn("enabled=1", rendered)
        self.assertIn("ready=1", rendered)
        self.assertIn(model.slug, rendered)
