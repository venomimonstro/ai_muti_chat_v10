from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from apps.admin_ops import recovery as recovery_module

from . import execution_fence
from .models import Generation, GenerationAttempt


class ChatExecutionFenceTests(SimpleTestCase):
    def _install(self, raw_recover=None):
        raw_finish = MagicMock(return_value=None)
        module = SimpleNamespace(_finish_attempt=raw_finish)
        recover = raw_recover or MagicMock(return_value=True)
        with patch.object(recovery_module, "_recover_generation", recover):
            execution_fence.install(module)
            installed_recover = recovery_module._recover_generation
        return module, raw_finish, installed_recover, recover

    def test_completed_attempt_requires_atomic_live_running_lease(self):
        module, raw_finish, _installed_recover, _raw_recover = self._install()
        queryset = MagicMock()
        queryset.update.return_value = 0
        with patch.object(execution_fence.GenerationAttempt.objects, "filter", return_value=queryset):
            with self.assertRaises(execution_fence.GenerationExecutionFenced):
                module._finish_attempt(
                    SimpleNamespace(pk="attempt-1"),
                    state=GenerationAttempt.State.COMPLETED,
                    started=0,
                )
        queryset.update.assert_called_once()
        raw_finish.assert_not_called()

    def test_failed_attempt_cannot_overwrite_recovery_revocation(self):
        module, raw_finish, _installed_recover, _raw_recover = self._install()
        queryset = MagicMock()
        queryset.update.return_value = 0
        error = SimpleNamespace(code="timeout", retryable=True)
        with patch.object(execution_fence.GenerationAttempt.objects, "filter", return_value=queryset):
            with self.assertRaises(execution_fence.GenerationExecutionFenced):
                module._finish_attempt(
                    SimpleNamespace(pk="attempt-2"),
                    state=GenerationAttempt.State.FAILED,
                    started=0,
                    error=error,
                )
        queryset.update.assert_called_once()
        raw_finish.assert_not_called()

    def test_live_attempt_finishes_with_single_compare_and_swap(self):
        module, raw_finish, _installed_recover, _raw_recover = self._install()
        queryset = MagicMock()
        queryset.update.return_value = 1
        attempt = SimpleNamespace(pk="attempt-3")
        with patch.object(execution_fence.GenerationAttempt.objects, "filter", return_value=queryset):
            module._finish_attempt(
                attempt,
                state=GenerationAttempt.State.COMPLETED,
                started=0,
            )
        self.assertEqual(attempt.state, GenerationAttempt.State.COMPLETED)
        queryset.update.assert_called_once()
        raw_finish.assert_not_called()

    def test_outer_guard_swallows_revoked_worker_only_after_terminal_recovery(self):
        def revoked_run(_generation, *args, **kwargs):
            if False:
                yield None
            raise execution_fence.GenerationExecutionFenced("revoked")

        streaming_module = SimpleNamespace(run=revoked_run)
        managed_stream_module = SimpleNamespace(run=revoked_run)
        execution_fence.install_outer_guard(streaming_module, managed_stream_module)
        generation = SimpleNamespace(state=Generation.State.FAILED)
        generation.refresh_from_db = MagicMock()

        self.assertEqual(list(streaming_module.run(generation)), [])
        generation.refresh_from_db.assert_called_once_with(fields=["state"])

    def test_outer_guard_never_hides_fence_without_terminal_recovery(self):
        def revoked_run(_generation, *args, **kwargs):
            if False:
                yield None
            raise execution_fence.GenerationExecutionFenced("revoked")

        streaming_module = SimpleNamespace(run=revoked_run)
        managed_stream_module = SimpleNamespace(run=revoked_run)
        execution_fence.install_outer_guard(streaming_module, managed_stream_module)
        generation = SimpleNamespace(state=Generation.State.RUNNING)
        generation.refresh_from_db = MagicMock()

        with self.assertRaises(execution_fence.GenerationExecutionFenced):
            list(streaming_module.run(generation))

    def test_stale_recovery_revokes_running_attempt_before_financial_recovery(self):
        raw_recover = MagicMock(return_value=True)
        _module, _raw_finish, installed_recover, _ = self._install(raw_recover)
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
