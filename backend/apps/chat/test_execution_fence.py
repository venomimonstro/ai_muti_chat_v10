from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError
from apps.admin_ops import recovery as recovery_module

from . import execution_fence
from .models import Generation, GenerationAttempt


class ChatExecutionFenceTests(SimpleTestCase):
    def _install(self, raw_recover=None):
        raw_finish = MagicMock(return_value=None)
        raw_record_failure = MagicMock(return_value=None)
        module = SimpleNamespace(
            _finish_attempt=raw_finish,
            record_failure=raw_record_failure,
        )
        recover = raw_recover or MagicMock(return_value=True)
        with patch.object(recovery_module, "_recover_generation", recover):
            execution_fence.install(module)
            installed_recover = recovery_module._recover_generation
        return module, raw_finish, raw_record_failure, installed_recover, recover

    def test_completed_attempt_requires_live_running_lease(self):
        module, raw_finish, _raw_failure, _installed_recover, _raw_recover = self._install()
        queryset = MagicMock()
        queryset.values_list.return_value.first.return_value = GenerationAttempt.State.FAILED
        with patch.object(execution_fence.GenerationAttempt.objects, "filter", return_value=queryset):
            with self.assertRaises(ProviderError) as raised:
                module._finish_attempt(
                    SimpleNamespace(pk="attempt-1"),
                    state=GenerationAttempt.State.COMPLETED,
                    started=0,
                )
        self.assertEqual(raised.exception.code, execution_fence.FENCE_ERROR_CODE)
        raw_finish.assert_not_called()

    def test_fence_failure_does_not_degrade_provider(self):
        module, _raw_finish, raw_failure, _installed_recover, _raw_recover = self._install()
        error = ProviderError("revoked", code=execution_fence.FENCE_ERROR_CODE, retryable=False)
        result = module.record_failure(SimpleNamespace(), error)
        self.assertIsNone(result)
        raw_failure.assert_not_called()

    def test_stale_recovery_revokes_running_attempt_before_financial_recovery(self):
        raw_recover = MagicMock(return_value=True)
        _module, _raw_finish, _raw_failure, installed_recover, _ = self._install(raw_recover)
        cutoff = timezone.now()
        generation = SimpleNamespace(
            id="generation-1",
            state=Generation.State.RUNNING,
            created_at=cutoff - timedelta(seconds=1),
        )

        generation_qs = MagicMock()
        generation_qs.filter.return_value.first.return_value = generation
        attempt_qs = MagicMock()
        attempt_qs.exists.return_value = False
        attempt_qs.update.return_value = 1

        with (
            patch.object(execution_fence.transaction, "atomic", return_value=nullcontext()),
            patch.object(execution_fence.Generation.objects, "select_for_update", return_value=generation_qs),
            patch.object(execution_fence.GenerationAttempt.objects, "filter", return_value=attempt_qs),
            patch.object(recovery_module, "_cutoff", return_value=cutoff),
        ):
            self.assertTrue(installed_recover("generation-1"))

        attempt_qs.update.assert_called_once()
        update = attempt_qs.update.call_args.kwargs
        self.assertEqual(update["state"], GenerationAttempt.State.FAILED)
        self.assertEqual(update["error_code"], "stale_operation_recovered")
        raw_recover.assert_called_once_with("generation-1")
