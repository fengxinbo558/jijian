import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class FacilityAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(str(Path(self.tempdir.name) / "facility.db"))
        self.service = IncidentService(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_core_single_feed_loss_requires_cc(self):
        incident = self.service.ingest(
            "monitor",
            {
                "site": "CORE-A",
                "device_type": "facility",
                "summary": "核心机房单路掉电",
                "message": "feed A lost; feed B carrying load",
                "facility_criticality": "core",
                "event_subtype": "single_feed_loss",
                "impact_level": "redundancy_degraded",
            },
        )
        assessment = incident["analysis"]["facility_assessment"]
        self.assertEqual(assessment["decision"], "required")
        self.assertEqual(assessment["matched_rule_id"], "CC-CORE-SINGLE-FEED")
        self.assertTrue(incident["cc_reminder"]["required"])

    def test_normal_single_feed_and_water_without_impact_do_not_require_cc(self):
        power = self.service.ingest(
            "monitor",
            {
                "site": "NORMAL-A",
                "device_type": "facility",
                "summary": "普通机房单路掉电",
                "message": "feed A lost; feed B healthy; no device outage",
                "facility_criticality": "normal",
                "event_subtype": "single_feed_loss",
                "impact_level": "redundancy_degraded",
            },
        )
        water = self.service.ingest(
            "onsite",
            {
                "site": "NORMAL-A",
                "device_type": "facility",
                "summary": "墙边少量渗水",
                "observation": "漏水未接触设备，没有机器宕机",
                "facility_criticality": "normal",
                "event_subtype": "water_leak",
                "impact_level": "alarm_only",
            },
        )
        self.assertEqual(
            power["analysis"]["facility_assessment"]["decision"], "not_required"
        )
        self.assertEqual(
            water["analysis"]["facility_assessment"]["decision"], "not_required"
        )

    def test_unknown_dual_feed_loss_needs_confirmation(self):
        incident = self.service.ingest(
            "monitor",
            {
                "site": "UNKNOWN-A",
                "device_type": "facility",
                "summary": "双路供电中断",
                "message": "feed A lost; feed B lost",
                "facility_criticality": "unknown",
                "event_subtype": "dual_feed_loss",
                "impact_level": "widespread_outage",
            },
        )
        assessment = incident["analysis"]["facility_assessment"]
        self.assertEqual(assessment["decision"], "needs_confirmation")
        self.assertIn("机房等级", assessment["missing_evidence"])
        self.assertFalse(incident["cc_reminder"].get("required", False))

    def test_water_caused_core_device_failure_requires_cc(self):
        incident = self.service.ingest(
            "monitor",
            {
                "site": "CORE-B",
                "sn": "CORE-SW-SN-001",
                "rack_position": "CORE-RACK-01",
                "device_type": "switch",
                "summary": "漏水导致核心交换机故障",
                "message": "water leak near rack; core switch unreachable",
                "facility_criticality": "core",
                "asset_criticality": "core",
                "event_subtype": "water_caused_core_device_failure",
                "impact_level": "widespread_outage",
            },
        )
        self.assertEqual(
            incident["analysis"]["facility_assessment"]["decision"], "required"
        )
        self.assertTrue(incident["cc_reminder"]["required"])

    def test_local_profile_is_used_and_can_be_replaced_with_history(self):
        first = self.service.upsert_facility_profile(
            {"site": "ROOM-01", "display_name": "一号机房", "criticality": "normal"}
        )
        self.assertEqual(first["criticality"], "normal")
        second = self.service.upsert_facility_profile(
            {
                "site": "ROOM-01",
                "display_name": "一号核心机房",
                "criticality": "core",
                "source": "cmdb",
                "source_reference": "ASSET-ROOM-01",
            }
        )
        self.assertEqual(second["criticality"], "core")
        incident = self.service.ingest(
            "monitor",
            {
                "site": "ROOM-01",
                "device_type": "facility",
                "summary": "单路供电中断",
                "message": "feed A lost; feed B carrying load",
                "event_subtype": "single_feed_loss",
                "impact_level": "redundancy_degraded",
            },
        )
        assessment = incident["analysis"]["facility_assessment"]
        self.assertEqual(assessment["facility"]["criticality"], "core")
        self.assertEqual(assessment["facility"]["source"], "cmdb")
        self.assertEqual(assessment["decision"], "required")
        with self.store.connect() as connection:
            history_count = connection.execute(
                "SELECT COUNT(*) FROM facility_profile_history WHERE site='ROOM-01'"
            ).fetchone()[0]
        self.assertEqual(history_count, 2)

    def test_shared_incident_keeps_one_strongest_cc_decision(self):
        base = {
            "site": "CORE-C",
            "facility_criticality": "core",
            "incident_key": "CORE-POWER-SHARED",
        }
        first = self.service.ingest(
            "monitor",
            {
                **base,
                "device_type": "facility",
                "summary": "核心机房单路掉电",
                "message": "feed A lost; feed B carrying load",
                "event_subtype": "single_feed_loss",
                "impact_level": "redundancy_degraded",
            },
        )
        second = self.service.ingest(
            "monitor",
            {
                **base,
                "sn": "AFFECTED-SN-001",
                "rack_position": "CORE-C-01",
                "device_type": "server",
                "summary": "服务器仍然在线",
                "message": "health check ok",
                "impact_level": "alarm_only",
            },
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(
            second["analysis"]["facility_assessment"]["decision"], "required"
        )
        self.assertTrue(second["cc_reminder"]["required"])
        self.assertEqual(
            len([item for item in second["audit_log"] if item["action"] == "incident_created"]),
            1,
        )

    def test_unknown_input_uses_profile_but_conflicting_input_requires_confirmation(self):
        self.service.upsert_facility_profile(
            {"site": "PROFILE-CORE", "criticality": "core", "display_name": "档案核心机房"}
        )
        from_profile = self.service.ingest(
            "monitor",
            {
                "site": "PROFILE-CORE",
                "device_type": "facility",
                "summary": "A路供电中断",
                "message": "feed A lost; feed B healthy",
                "facility_criticality": "unknown",
            },
        )
        assessment = from_profile["analysis"]["facility_assessment"]
        self.assertEqual(assessment["facility"]["source"], "local_config")
        self.assertEqual(assessment["decision"], "required")

        self.service.upsert_facility_profile(
            {"site": "PROFILE-CONFLICT", "criticality": "normal"}
        )
        conflict = self.service.ingest(
            "monitor",
            {
                "site": "PROFILE-CONFLICT",
                "device_type": "facility",
                "summary": "A路供电中断",
                "message": "feed A lost; feed B healthy",
                "facility_criticality": "core",
            },
        )
        assessment = conflict["analysis"]["facility_assessment"]
        self.assertEqual(assessment["facility"]["source"], "conflict")
        self.assertEqual(assessment["decision"], "needs_confirmation")
        self.assertFalse(conflict["cc_reminder"]["required"])


if __name__ == "__main__":
    unittest.main()
