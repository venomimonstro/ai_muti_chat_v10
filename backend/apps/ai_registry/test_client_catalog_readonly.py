from django.test import TestCase

from .models import AIModel, Provider, RoutingTierAssignment
from .serializers import AIModelSerializer
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

    def test_gigachat_is_exposed_only_as_branded_llm_system(self):
        provider, _ = Provider.objects.get_or_create(slug="gigachat", defaults={"name": "GigaChat API"})
        provider.enabled = True
        provider.emergency_disabled = False
        provider.health_state = Provider.HealthState.HEALTHY
        provider.save(update_fields=["enabled", "emergency_disabled", "health_state"])
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

        self.assertIn(gigachat.id, ids)
        self.assertIn(visible.id, ids)
        payload = AIModelSerializer(gigachat).data
        self.assertEqual(payload["provider"], "llm-system")
        self.assertEqual(payload["provider_name"], "LLM System")
        self.assertEqual(payload["display_name"], "LLM System · System Max")
        self.assertEqual(payload["exact_api_id"], "")

    def test_llm_system_tier_uses_upstream_identity_not_local_slug(self):
        provider, _ = Provider.objects.get_or_create(slug="gigachat", defaults={"name": "GigaChat API"})
        cases = (
            ("system-import-a", "GigaChat-2", "LLM System · System Lite"),
            ("system-import-b", "GigaChat-2-Pro", "LLM System · System Pro"),
            ("system-import-c", "GigaChat-2-Max", "LLM System · System Max"),
        )
        for slug, upstream, expected in cases:
            model = AIModel.objects.create(
                provider=provider,
                slug=slug,
                display_name="Imported system model",
                upstream_model=upstream,
                enabled=True,
            )
            self.assertEqual(AIModelSerializer(model).data["display_name"], expected)

    def test_catalog_serializes_admin_managed_tier_membership(self):
        provider = Provider.objects.create(slug="tier-provider", name="Tier provider")
        model = AIModel.objects.create(
            provider=provider,
            slug="tier-model",
            display_name="Tier model",
            upstream_model="tier-model",
            enabled=True,
        )
        RoutingTierAssignment.objects.create(
            tier=RoutingTierAssignment.Tier.SIMPLE,
            model=model,
            priority=10,
            enabled=True,
        )
        RoutingTierAssignment.objects.create(
            tier=RoutingTierAssignment.Tier.COMPLEX,
            model=model,
            priority=20,
            enabled=False,
        )

        payload = AIModelSerializer(
            model,
            context={"routing_tiers_configured": True},
        ).data

        self.assertEqual(payload["routing_tiers"], [RoutingTierAssignment.Tier.SIMPLE])
        self.assertTrue(payload["routing_tiers_configured"])
