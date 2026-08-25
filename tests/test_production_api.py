import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from idcops.server import create_server


class ProductionAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "production-api.db")
        web_dir = str(Path(__file__).resolve().parent.parent / "web")
        self.server = create_server("127.0.0.1", 0, database, web_dir)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path, role="ai_admin", payload=None, actor="api-tester"):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-IDCAI-Role": role,
                "X-IDCAI-User": actor,
            },
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    @staticmethod
    def alert():
        return {
            "source_system": "network_nms",
            "source_event_id": "API-PROD-001",
            "site": "BJYZ",
            "entity": {"device_name": "HB-BJYZ-TOR-01", "device_type": "switch"},
            "signal_type": "link_down",
            "severity": "critical",
            "summary": "TOR 上联中断",
            "raw_payload": {"message": "HundredGigE1/0/1 down"},
        }

    def test_alert_overview_and_role_projection(self):
        status, result = self.request("/api/production/alerts", payload=self.alert())
        self.assertEqual(status, 201)
        self.assertTrue(result["incident_created"])

        _status, overview = self.request("/api/production/overview")
        self.assertEqual(overview["active_alerts"], 1)
        _status, onsite = self.request(
            "/api/production/alerts", role="onsite_operator"
        )
        self.assertNotIn("payload", onsite["items"][0])
        self.assertIn("data_quality", onsite["items"][0])

    def test_maintenance_write_is_role_protected(self):
        now = datetime.now(timezone.utc)
        payload = {
            "site": "BJYZ",
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
            "reason": "计划维护",
        }
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                "/api/production/maintenance", role="onsite_operator", payload=payload
            )
        self.assertEqual(raised.exception.code, 403)
        status, created = self.request(
            "/api/production/maintenance", role="facility_lead", payload=payload
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["reason"], "计划维护")

    def test_source_health_identity_change_and_assignment_routes(self):
        _status, health = self.request(
            "/api/production/source-health",
            payload={
                "source_system": "otel_collector",
                "connection_status": "degraded",
                "expected_entities": 10,
                "reporting_entities": 4,
                "queue_depth": 9,
            },
        )
        self.assertTrue(health["pipeline_problem"])

        _status, assertion = self.request(
            "/api/production/identities",
            payload={
                "entity_key": "sn:API-SN-001",
                "source_system": "oms_cmdb",
                "field_name": "rack_position",
                "field_value": "BJYZ-A-01-01",
            },
        )
        self.assertEqual(assertion["authority_rank"], 100)

        _status, change = self.request(
            "/api/production/changes",
            role="interface_person",
            payload={
                "site": "BJYZ",
                "entity_key": "sn:API-SN-001",
                "change_type": "configuration",
                "summary": "修改网卡参数",
            },
        )
        self.assertEqual(change["causality"], "candidate_only")

        _status, result = self.request("/api/production/alerts", payload=self.alert())
        incident_id = result["alert"]["incident_id"]
        _status, assigned = self.request(
            "/api/production/assignments",
            role="interface_person",
            payload={
                "incident_id": incident_id,
                "assignee": "api-tester",
                "team": "onsite",
                "priority": "p1",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                f"/api/production/assignments/{assigned['id']}/acknowledge",
                role="onsite_operator",
                actor="another-operator",
                payload={},
            )
        self.assertEqual(raised.exception.code, 403)
        _status, acknowledged = self.request(
            f"/api/production/assignments/{assigned['id']}/acknowledge",
            role="onsite_operator",
            payload={},
        )
        self.assertEqual(acknowledged["status"], "acknowledged")

    def test_public_dataset_catalog_and_import_endpoint(self):
        _status, catalog = self.request("/api/public-datasets", role="onsite_operator")
        self.assertEqual(len(catalog["items"]), 6)
        status, report = self.request(
            "/api/public-datasets/loghub-linux/import-sample",
            payload={"sample_text": "kernel ready\nkernel I/O error on sda"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["record_count"], 2)
        _status, projected = self.request("/api/public-datasets", role="onsite_operator")
        imported = next(item for item in projected["items"] if item["id"] == "loghub-linux")
        self.assertNotIn("local_path", imported["last_import"])
        self.assertNotIn("checksum", imported["last_import"])
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                "/api/public-datasets/loghub-linux/import-sample",
                role="onsite_operator",
                payload={"sample_text": "error"},
            )
        self.assertEqual(raised.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
