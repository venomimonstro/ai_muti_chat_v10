from django.test import TestCase

from .models import AIModel, Provider
from .views import AIModelViewSet


class ClientModelCatalogReadOnlyTests(TestCase):
    def test_catalog_read_does_not_activate_disabled_model(self):
        provider = Provider.objects.create(
            slug="readonly-provider",
            name="Readonly provider",
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        model = AIModel.objects.create(
            provider=provider,
            slug="readonly-disabled-model",
            display_name="Readonly disabled model",
            upstream_model="readonly-disabled-model",
            enabled=False,
        )

        rows = list(AIModelViewSet().get_queryset())

        model.refresh_from_db()
        self.assertFalse(model.enabled)
        self.assertNotIn(model.id, {item.id for item in rows})
