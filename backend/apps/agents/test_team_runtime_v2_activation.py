import inspect
from django.test import SimpleTestCase

from . import team_runtime
from . import team_runtime_v2


class DevTeamRuntimeV2ActivationTests(SimpleTestCase):
    def test_v2_stage_is_installed_process_wide(self):
        self.assertIs(team_runtime._run_llm_stage, team_runtime_v2._run_llm_stage_v2)
        self.assertIs(team_runtime_v2.execute_team_run, team_runtime.execute_team_run)


    def test_v2_stage_passes_step_and_preserves_settlement_recovery_contract(self):
        source = inspect.getsource(team_runtime_v2._run_llm_stage_v2)
        self.assertIn("step=step", source)
        self.assertIn("settlement_checkpoint", source)
        self.assertIn("legacy.agent_provider_checkpoint_pending(step)", source)
        self.assertIn("legacy._defer_settlement_recovery", source)
