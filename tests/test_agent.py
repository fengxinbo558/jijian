import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idcops.service import IncidentService
from idcops.store import IncidentStore


class AgentInvestigatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {}, clear=True):
            self.service = IncidentService(
                IncidentStore(str(Path(self.tempdir.name) / "agent.db"))
            )
        scenario = self.service.run_lab_scenario("network-module-cascade")
        self.incident_id = scenario["incident_ids"][0]

    def tearDown(self):
        self.tempdir.cleanup()

    def test_real_model_mode_is_not_faked_when_model_is_missing(self):
        run = self.service.run_agent(self.incident_id, "model")
        self.assertEqual(run["status"], "not_run")
        self.assertEqual(run["stop_reason"], "model_not_configured")
        self.assertFalse(run["summary"]["real_ai"])
        self.assertEqual(run["steps"][0]["status"], "not_run")

    def test_test_stub_calls_read_only_tools_and_is_clearly_labeled(self):
        run = self.service.run_agent(self.incident_id, "test_stub", 4)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["summary"]["label"], "测试模型桩（非真实AI）")
        self.assertFalse(run["summary"]["real_ai"])
        self.assertGreaterEqual(len(run["steps"]), 3)
        self.assertTrue(all(step["validation"]["read_only"] for step in run["steps"]))
        self.assertTrue(
            any(step["tool_output"].get("records") for step in run["steps"])
        )

    def test_baseline_does_not_claim_active_agent(self):
        run = self.service.run_agent(self.incident_id, "baseline")
        self.assertEqual(run["summary"]["label"], "固定规则基线")
        self.assertFalse(run["steps"][0]["validation"]["active_tool_calls"])

    def test_real_model_planner_can_call_tool_then_stop_with_auditable_trace(self):
        class FakePlanner:
            prompt_version = "fake-agent-v1"

            def __init__(self):
                self.calls = 0

            def propose(self, incident, hypotheses, tools, history, round_no):
                self.calls += 1
                common = {
                    "rationale": "先核对端口证据，再根据返回结果停止。",
                    "evidence_ids": [],
                    "hypotheses": [],
                    "conclusion": "交换机链路异常仍是候选，需人工决定是否操作。",
                    "_trace": {"model": "fake", "validation": "accepted"},
                }
                if self.calls == 1:
                    return {
                        **common,
                        "next_action": {
                            "tool": "network.query_port",
                            "args": {"incident_id": incident["id"], "limit": 50},
                        },
                        "stop": False,
                        "stop_reason": "",
                    }
                return {
                    **common,
                    "next_action": None,
                    "stop": True,
                    "stop_reason": "enough_read_only_evidence",
                }

        self.service.ai.allow_external = True
        self.service.ai.url = "https://model.example/v1/chat/completions"
        self.service.ai.model = "example-model"
        self.service.agent.planner = FakePlanner()
        run = self.service.run_agent(self.incident_id, "model", 4)
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run["summary"]["real_ai"])
        self.assertEqual(run["stop_reason"], "enough_read_only_evidence")
        self.assertEqual(run["steps"][0]["tool_name"], "network.query_port")
        self.assertTrue(run["steps"][0]["validation"]["read_only"])
        self.assertEqual(run["steps"][1]["step_type"], "model_stop")


if __name__ == "__main__":
    unittest.main()
