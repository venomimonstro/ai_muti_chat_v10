from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.ai_registry.models import ProviderApiKey
from apps.chat import search_policy_hardening


class _PaidModule:
    def __init__(self):
        self.calls = []
        self._ai_workspace_search_policy_hardened = False
        self._premium_search = lambda query: True
        self._account_secret = lambda account: "secret"
        self._mark_key = self._raw_mark_key

    def _raw_mark_key(self, account, *, healthy, error_code=""):
        self.calls.append((healthy, error_code))
        return True


class SearchPolicyHardeningTests(SimpleTestCase):
    def setUp(self):
        self.module = _PaidModule()
        search_policy_hardening.install(self.module)

    def test_only_explicit_yandex_request_is_yandex_first(self):
        self.assertFalse(self.module._premium_search("Подскажи лучшие стоматологии в Москве"))
        self.assertFalse(self.module._premium_search("Найди актуальные цены на ноутбуки в России"))
        self.assertTrue(self.module._premium_search("Найди это именно в Яндексе"))
        self.assertTrue(self.module._premium_search("Yandex поиск по этой теме"))

    def test_degraded_key_is_not_used_for_customer_search(self):
        degraded = SimpleNamespace(
            api_key_id="1",
            api_key=SimpleNamespace(
                enabled=True,
                health_state=ProviderApiKey.HealthState.DEGRADED,
            ),
        )
        healthy = SimpleNamespace(
            api_key_id="2",
            api_key=SimpleNamespace(
                enabled=True,
                health_state=ProviderApiKey.HealthState.HEALTHY,
            ),
        )
        unknown = SimpleNamespace(
            api_key_id="3",
            api_key=SimpleNamespace(
                enabled=True,
                health_state=ProviderApiKey.HealthState.UNKNOWN,
            ),
        )
        self.assertEqual(self.module._account_secret(degraded), "")
        self.assertEqual(self.module._account_secret(healthy), "secret")
        self.assertEqual(self.module._account_secret(unknown), "secret")

    def test_query_level_failure_does_not_poison_credential(self):
        account = SimpleNamespace(api_key_id="1")
        self.module._mark_key(account, healthy=False, error_code="search_failed")
        self.assertEqual(self.module.calls, [])

        self.module._mark_key(account, healthy=False, error_code="authentication_error")
        self.assertEqual(self.module.calls, [(False, "authentication_error")])

        self.module._mark_key(account, healthy=True, error_code="")
        self.assertEqual(self.module.calls[-1], (True, ""))
