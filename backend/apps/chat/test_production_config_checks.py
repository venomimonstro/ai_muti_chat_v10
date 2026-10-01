from django.test import SimpleTestCase, override_settings

from apps.chat.apps import chat_production_configuration_check
from apps.procurement.apps import procurement_production_configuration_check


class ProductionChatConfigurationCheckTests(SimpleTestCase):
    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    def test_locmem_cache_is_blocked_in_production(self):
        errors = chat_production_configuration_check(None)
        self.assertEqual([error.id for error in errors], ["chat.E001"])

    @override_settings(
        DEBUG=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}},
    )
    def test_shared_cache_contract_is_accepted(self):
        self.assertEqual(chat_production_configuration_check(None), [])

    @override_settings(DEBUG=False, PROCUREMENT_RUNTIME_FAIL_CLOSED=False)
    def test_permissive_procurement_is_blocked_in_production(self):
        errors = procurement_production_configuration_check(None)
        self.assertEqual([error.id for error in errors], ["procurement.E001"])

    @override_settings(DEBUG=False, PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    def test_strict_procurement_contract_is_accepted(self):
        self.assertEqual(procurement_production_configuration_check(None), [])

    @override_settings(
        DEBUG=True,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        PROCUREMENT_RUNTIME_FAIL_CLOSED=False,
    )
    def test_development_remains_permissive(self):
        self.assertEqual(chat_production_configuration_check(None), [])
        self.assertEqual(procurement_production_configuration_check(None), [])
