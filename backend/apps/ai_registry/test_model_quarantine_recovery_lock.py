from unittest.mock import patch

from django.test import SimpleTestCase

from .model_quarantine import recover_quarantined_models


class ModelQuarantineRecoveryLockTests(SimpleTestCase):
    @patch("apps.ai_registry.model_quarantine.cache.add", return_value=False)
    def test_overlapping_recovery_is_skipped_without_probe(self, cache_add):
        with patch("apps.ai_registry.model_quarantine._recover_quarantined_models") as recover:
            result = recover_quarantined_models(limit=4)

        cache_add.assert_called_once()
        recover.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_running")

    @patch("apps.ai_registry.model_quarantine.cache.delete")
    @patch("apps.ai_registry.model_quarantine.cache.add", return_value=True)
    def test_recovery_lease_is_released_after_success(self, cache_add, cache_delete):
        expected = {"checked": 1, "recovered": 1, "still_quarantined": 0, "skipped": 0}
        with patch(
            "apps.ai_registry.model_quarantine._recover_quarantined_models",
            return_value=expected,
        ) as recover:
            result = recover_quarantined_models(limit=4)

        cache_add.assert_called_once()
        recover.assert_called_once_with(limit=4)
        cache_delete.assert_called_once()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["recovered"], 1)

    @patch("apps.ai_registry.model_quarantine.cache.delete")
    @patch("apps.ai_registry.model_quarantine.cache.add", return_value=True)
    def test_recovery_lease_is_released_after_exception(self, cache_add, cache_delete):
        with patch(
            "apps.ai_registry.model_quarantine._recover_quarantined_models",
            side_effect=RuntimeError("probe crashed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "probe crashed"):
                recover_quarantined_models(limit=2)

        cache_add.assert_called_once()
        cache_delete.assert_called_once()
