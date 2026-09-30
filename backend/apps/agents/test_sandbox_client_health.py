from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .sandbox_client import sandbox_health


class SandboxHealthTests(SimpleTestCase):
    @patch.dict("os.environ", {"SANDBOX_SHARED_SECRET": "test-secret", "SANDBOX_URL": "http://sandbox:8090"}, clear=False)
    @patch("apps.agents.sandbox_client.httpx.get")
    def test_workspace_api_health_is_required(self, get):
        get.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "ok",
                "configured": True,
                "workspace_api": True,
                "commands": ["python-compile", "json-check"],
            },
        )
        result = sandbox_health()
        self.assertTrue(result["healthy"])
        self.assertTrue(result["workspace_api"])
        self.assertIn("python-compile", result["commands"])

    @patch.dict("os.environ", {"SANDBOX_SHARED_SECRET": "test-secret", "SANDBOX_URL": "http://sandbox:8090"}, clear=False)
    @patch("apps.agents.sandbox_client.httpx.get")
    def test_unconfigured_remote_sandbox_is_unhealthy(self, get):
        get.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "ok", "configured": False, "workspace_api": True},
        )
        result = sandbox_health()
        self.assertFalse(result["healthy"])
        self.assertFalse(result["configured"])

    @patch.dict("os.environ", {"SANDBOX_SHARED_SECRET": ""}, clear=False)
    def test_missing_secret_fails_closed_without_network(self):
        with patch("apps.agents.sandbox_client.httpx.get") as get:
            result = sandbox_health()
        self.assertFalse(result["healthy"])
        self.assertEqual(result["error"], "sandbox_not_configured")
        get.assert_not_called()
