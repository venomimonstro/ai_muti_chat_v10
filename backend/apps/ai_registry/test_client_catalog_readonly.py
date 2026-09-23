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

    def test_gigachat_is_internal_only_even_when_enabled_for_auto_router(self):
        provider = Provider.objects.create(
            slug="gigachat",
            name="GigaChat API",
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        gigachat = AIModel.objects.create(
            provider=provider,
            slug="gigachat-test-model",
            display_name="GigaChat Test",
            upstream_model="GigaChat-2-Max",
            enabled=True,
        )
        visible_provider = Provider.objects.create(
            slug="visible-provider",
            name="Visible provider",
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        visible = AIModel.objects.create(
            provider=visible_provider,
            slug="visible-model",
            display_name="Visible model",
            upstream_model="visible-model",
            enabled=True,
        )

        rows = list(AIModelViewSet().get_queryset())
        ids = {item.id for item in rows}

        self.assertNotIn(gigachat.id, ids)
        self.assertIn(visible.id, ids)
