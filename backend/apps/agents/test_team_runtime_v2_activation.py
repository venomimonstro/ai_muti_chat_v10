from django.test import SimpleTestCase

from . import team_runtime
from . import team_runtime_v2


class DevTeamRuntimeV2ActivationTests(SimpleTestCase):
    def test_v2_stage_is_installed_process_wide(self):
        self.assertIs(team_runtime._run_llm_stage, team_runtime_v2._run_llm_stage_v2)
        self.assertIs(team_runtime_v2.execute_team_run, team_runtime.execute_team_run)
