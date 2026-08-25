import json
import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class DrillServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(str(Path(self.tempdir.name) / "drills.db"))
        self.service = IncidentService(self.store)
        self.drills = self.service.drills

    def tearDown(self):
        self.tempdir.cleanup()

    def test_catalog_has_five_categories_and_twenty_five_specific_faults(self):
        catalog = self.drills.list_catalog()
        self.assertEqual(catalog["count"], 25)
        self.assertEqual(len(catalog["categories"]), 5)
        counts = {}
        for item in catalog["items"]:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
            self.assertNotIn("hidden_truth", item)
            self.assertNotIn("primary_signal", item)
        self.assertEqual(set(counts.values()), {5})

    def test_directed_drill_uses_platform_and_governance_boundaries(self):
        run = self.drills.start(
            {
                "mode": "directed",
                "scenario_id": "net-optical-module",
                "playback_mode": "auto",
            },
            "admin-a",
            "ai_admin",
        )
        self.assertEqual(run["status"], "waiting_human")
        self.assertEqual(run["current_step_id"], "network-check-config")
        self.assertNotIn("hidden_truth", run)
        self.assertGreaterEqual(len(run["incident_ids"]), 1)
        with self.store.connect() as connection:
            event_count = connection.execute(
                "SELECT COUNT(*) AS count FROM integration_events"
            ).fetchone()["count"]
            alert_count = connection.execute(
                "SELECT COUNT(*) AS count FROM managed_alerts"
            ).fetchone()["count"]
        self.assertEqual(event_count, 2)
        self.assertEqual(alert_count, 2)
        incident = self.service.get_incident(run["incident_ids"][0])
        self.assertTrue(incident["investigation"]["simulation"])

    def test_blind_drill_does_not_reveal_scenario_or_truth_until_terminal(self):
        run = self.drills.start(
            {"mode": "blind", "category": "network", "autostart": False},
            "admin-b",
            "ai_admin",
        )
        serialized = json.dumps(run, ensure_ascii=False)
        self.assertEqual(run["scenario"]["id"], "")
        self.assertNotIn("hidden_truth", run)
        secret = self.drills._secret(run["id"])
        self.assertNotIn(str(secret["scenario_id"]), serialized)
        ended = self.drills.terminate(run["id"], "盲测隔离验证", "admin-b")
        self.assertTrue(ended["truth_reveal_available"])
        revealed = self.drills.get(run["id"], reveal=True)
        self.assertIn("hidden_truth", revealed)
        self.assertEqual(revealed["scenario"]["id"], secret["scenario_id"])

    def test_fiber_fault_branches_from_module_to_measurement_to_cable(self):
        run = self.drills.start(
            {
                "mode": "directed",
                "scenario_id": "net-fiber-attenuation",
                "playback_mode": "auto",
            },
            "admin-c",
            "ai_admin",
        )
        run = self.drills.feedback(run["id"], "query_config", "配置一致", "network-a")
        self.assertEqual(run["current_step_id"], "network-replace-module")
        run = self.drills.feedback(run["id"], "replace_module", "新模块仍抖动", "onsite-a")
        self.assertEqual(run["current_step_id"], "network-measure-optics")
        run = self.drills.feedback(run["id"], "measure_optics", "对端收光低", "onsite-a")
        self.assertEqual(run["current_step_id"], "network-replace-cable")
        run = self.drills.feedback(run["id"], "replace_cable", "更换后稳定", "onsite-a")
        self.assertEqual(run["current_step_id"], "checkpoint-verify")
        self.assertEqual(run["status"], "waiting_human")
        run = self.drills.feedback(run["id"], "business_ok", "业务验证通过", "interface-a")
        self.assertEqual(run["status"], "resolved")
        self.assertTrue(run["score"]["diagnosis_match"])
        self.assertNotIn("hidden_truth", run)
        revealed = self.drills.get(run["id"], reveal=True)
        self.assertEqual(revealed["hidden_truth"]["diagnosis"], "fiber_attenuation")
        summaries = [item["summary"] for item in revealed["steps"]]
        self.assertTrue(any("模块故障候选被削弱" in item for item in summaries))
        self.assertTrue(any("线路衰耗异常" in item for item in summaries))

    def test_monitor_recovery_still_waits_for_business_verification(self):
        run = self.drills.start(
            {
                "mode": "directed",
                "scenario_id": "app-port-conflict",
                "playback_mode": "auto",
            },
            "admin-d",
            "ai_admin",
        )
        run = self.drills.feedback(run["id"], "perform_action", "端口已释放", "app-a")
        self.assertEqual(run["status"], "waiting_human")
        self.assertEqual(run["current_step_id"], "checkpoint-verify")
        self.assertFalse(run["truth_reveal_available"])
        run = self.drills.feedback(run["id"], "business_not_ok", "接口仍报错", "app-a")
        self.assertEqual(run["status"], "transferred")
        self.assertTrue(run["score"]["diagnosis_match"])
        self.assertEqual(run["score"]["unsafe_action_count"], 0)

    def test_step_mode_emits_one_signal_at_a_time(self):
        run = self.drills.start(
            {
                "mode": "directed",
                "scenario_id": "net-optical-module",
                "playback_mode": "step",
            },
            "admin-e",
            "ai_admin",
        )
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["current_step_id"], "signal-support-1")
        platform_steps = [item for item in run["steps"] if item["step_type"] == "platform_signal"]
        self.assertEqual(len(platform_steps), 1)
        run = self.drills.advance(run["id"], "step", "admin-e")
        self.assertEqual(run["status"], "waiting_human")
        self.assertEqual(run["current_step_id"], "network-check-config")

    def test_repeated_scenario_runs_are_isolated_from_production_deduplication(self):
        first = self.drills.start(
            {"mode": "directed", "scenario_id": "net-optical-module", "playback_mode": "auto"},
            "admin-f",
            "ai_admin",
        )
        second = self.drills.start(
            {"mode": "directed", "scenario_id": "net-optical-module", "playback_mode": "auto"},
            "admin-f",
            "ai_admin",
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["incident_ids"], second["incident_ids"])
        first_alert = next(item for item in first["steps"] if item["step_type"] == "platform_signal")
        second_alert = next(item for item in second["steps"] if item["step_type"] == "platform_signal")
        self.assertEqual(first_alert["details"]["governance_decision"], "create_incident")
        self.assertEqual(second_alert["details"]["governance_decision"], "create_incident")


if __name__ == "__main__":
    unittest.main()
