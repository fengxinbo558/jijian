import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class IncidentServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "test.db")
        self.service = IncidentService(IncidentStore(self.database))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_same_explicit_incident_merges_multiple_devices_and_one_cc(self):
        base = {
            "site": "BJYZ",
            "device_type": "server",
            "rack_position": "RACK-A-01",
            "summary": "温度异常",
            "message": "temperature high; fan full speed",
            "incident_key": "HEAT-ONE",
            "cc_required": True,
        }
        first = self.service.ingest("monitor", {**base, "sn": "HEAT-SN-001"})
        second = self.service.ingest(
            "monitor", {**base, "sn": "HEAT-SN-002", "rack_position": "RACK-A-02"}
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["affected_count"], 2)
        self.assertTrue(second["cc_reminder"]["required"])
        self.assertEqual(
            len([item for item in second["audit_log"] if item["action"] == "incident_created"]),
            1,
        )

    def test_different_full_sn_does_not_merge_without_group_key(self):
        payload = {
            "site": "BJYZ",
            "rack_position": "RACK-A-01",
            "device_type": "server",
            "summary": "磁盘错误",
            "log_text": "Buffer I/O error on dev sdb",
        }
        first = self.service.ingest("log", {**payload, "sn": "DISK-SN-001"})
        second = self.service.ingest("log", {**payload, "sn": "DISK-SN-002"})
        self.assertNotEqual(first["id"], second["id"])

    def test_missing_identity_stops_onsite_operation(self):
        incident = self.service.ingest(
            "onsite",
            {
                "site": "BJYZ",
                "device_type": "server",
                "observation": "机器告警灯亮，疑似硬盘异常",
            },
        )
        card = incident["onsite_card"]
        self.assertEqual(card["power"]["gate"], "stop")
        self.assertIn("完整 SN", card["missing_information"])
        self.assertIn("机架位", card["missing_information"])

    def test_from_reinstall_allows_gate_but_not_identity_bypass(self):
        ready = self.service.ingest(
            "log",
            {
                "site": "BJYZ",
                "sn": "REINSTALL-SN-001",
                "rack_position": "RACK-B-02",
                "device_type": "server",
                "summary": "磁盘故障",
                "log_text": "SMART Failure Predicted",
                "from_reinstall": "yes",
            },
        )
        self.assertEqual(ready["onsite_card"]["power"]["gate"], "ready")
        stopped = self.service.ingest(
            "log",
            {
                "site": "BJYZ",
                "device_type": "server",
                "summary": "另一台磁盘故障",
                "log_text": "SMART Failure Predicted on /dev/sdc",
                "from_reinstall": "yes",
            },
        )
        self.assertEqual(stopped["onsite_card"]["power"]["gate"], "stop")

    def test_resolved_event_is_not_reopened_by_new_alert(self):
        payload = {
            "site": "BJYZ",
            "sn": "RECUR-SN-001",
            "rack_position": "RACK-C-01",
            "device_type": "server",
            "summary": "磁盘错误",
            "log_text": "I/O error, dev sda",
        }
        first = self.service.ingest("log", payload)
        self.service.update_status(first["id"], "resolved")
        second = self.service.ingest("log", payload)
        self.assertNotEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()

