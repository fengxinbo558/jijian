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

    def test_signoz_alertmanager_payload_is_normalized(self):
        incidents = self.service.ingest_signoz_alert(
            {
                "status": "firing",
                "groupKey": "disk-group-1",
                "alerts": [
                    {
                        "status": "firing",
                        "startsAt": "2026-08-24T02:00:00Z",
                        "fingerprint": "alert-fingerprint-1",
                        "labels": {
                            "alertname": "DiskIoError",
                            "severity": "critical",
                            "site": "BJYZ",
                            "serial_number": "SIGNOZ-FULL-SN-001",
                            "host_name": "bjyz-host-01",
                            "rack_position": "BJYZ-A-01",
                            "device_type": "server",
                        },
                        "annotations": {
                            "summary": "服务器磁盘 I/O 异常",
                            "description": "Buffer I/O error on dev sdb",
                        },
                    }
                ],
            }
        )
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(incident["devices"][0]["sn"], "SIGNOZ-FULL-SN-001")
        self.assertEqual(incident["category"], "hardware")
        self.assertEqual(
            incident["investigation"]["intake"][0]["source_label"],
            "SigNoz 告警 Webhook",
        )


class StubIntegrationHub:
    def investigate(self, _incident):
        return [
            {
                "provider": "signoz",
                "state": "completed",
                "message": "查询到 2 条日志记录",
                "records": [
                    {"text": "Buffer I/O error on dev sdb"},
                    {"text": "SMART Failure Predicted on /dev/sdb"},
                ],
                "tool_calls": [{"tool": "signoz.query_logs", "read_only": True}],
            },
            {
                "provider": "holmes",
                "state": "completed",
                "message": "AI 调查完成，返回 1 条工具调用记录",
                "analysis": "存储故障是较大可能，但仍需核对槽位映射。",
                "tool_calls": [{"tool": "signoz_query_logs", "read_only": True}],
            },
        ]

    def source_statuses(self, check_external=True):
        return []


class ExternalInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "external.db")
        self.service = IncidentService(
            IncidentStore(database), integrations=StubIntegrationHub()
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_external_logs_extend_evidence_chain_without_confirming_root_cause(self):
        incident = self.service.ingest(
            "monitor",
            {
                "site": "BJYZ",
                "sn": "EXT-FULL-SN-001",
                "device_name": "bjyz-host-01",
                "rack_position": "BJYZ-A-01",
                "device_type": "server",
                "summary": "服务器存储告警",
                "message": "disk latency high",
            },
        )
        updated = self.service.investigate_external(incident["id"])
        self.assertIsNotNone(updated)
        investigation = updated["investigation"]
        self.assertEqual(investigation["mode"], "tool_assisted")
        self.assertTrue(any(fact["type"] == "smart_failure" for fact in investigation["extracted_facts"]))
        self.assertEqual(
            investigation["external_checks"][1]["analysis"],
            "存储故障是较大可能，但仍需核对槽位映射。",
        )
        self.assertNotEqual(investigation["conclusion"]["grade"], "confirmed")
        self.assertTrue(
            any(
                item["action"] == "external_investigation_completed"
                for item in updated["audit_log"]
            )
        )


if __name__ == "__main__":
    unittest.main()
