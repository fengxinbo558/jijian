import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from idcops.server import create_server


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "api.db")
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

    def request_json(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_ingest_list_and_status(self):
        status, health = self.request_json("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["analysis_mode"], "rules_only")
        self.assertEqual(health["knowledge"]["card_count"], 40)
        self.assertEqual(health["collectors_connected"], [])
        status, incident = self.request_json(
            "/api/ingest/alert",
            {
                "site": "BJYZ",
                "sn": "API-FULL-SN-001",
                "rack_position": "RACK-A-01",
                "device_type": "switch",
                "summary": "链路中断",
                "message": "interface HundredGigE7/0/36 link down",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(incident["category"], "network")
        _status, listing = self.request_json("/api/incidents")
        self.assertEqual(listing["items"][0]["devices"][0]["sn"], "API-FULL-SN-001")
        _status, updated = self.request_json(
            f"/api/incidents/{incident['id']}/status", {"status": "processing"}
        )
        self.assertEqual(updated["status"], "processing")

    def test_demo_endpoint_runs_real_pipeline(self):
        status, payload = self.request_json("/api/demos/network-optic/run", {})
        self.assertEqual(status, 201)
        self.assertEqual(payload["incidents"][0]["category"], "network")
        self.assertTrue(payload["incidents"][0]["investigation"]["simulation"])
        self.assertTrue(
            all(
                item["source_label"] == "内置测试场景"
                for item in payload["incidents"][0]["investigation"]["intake"]
            )
        )

    def test_log_and_onsite_ingest_endpoints(self):
        status, log_incident = self.request_json(
            "/api/ingest/log",
            {
                "site": "BJYZ",
                "sn": "LOG-ENDPOINT-SN-001",
                "rack_position": "RACK-L-01",
                "device_type": "server",
                "summary": "服务启动失败",
                "log_text": "Address already in use: 0.0.0.0:8080",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(log_incident["category"], "application")

        status, onsite_incident = self.request_json(
            "/api/ingest/onsite",
            {
                "site": "BJYZ",
                "sn": "ONSITE-ENDPOINT-SN-001",
                "rack_position": "RACK-O-01",
                "device_type": "server",
                "summary": "现场发现硬盘告警灯",
                "observation": "硬盘异常灯常亮，等待接口人确认操作范围",
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(onsite_incident["onsite_card"]["required"])
        self.assertEqual(
            onsite_incident["onsite_card"]["device"]["sn"],
            "ONSITE-ENDPOINT-SN-001",
        )

    def test_sources_signoz_webhook_and_bounded_investigation_endpoints(self):
        status, sources = self.request_json("/api/sources")
        self.assertEqual(status, 200)
        source_states = {item["id"]: item["state"] for item in sources["items"]}
        self.assertEqual(source_states["manual_log"], "available")
        self.assertEqual(source_states["signoz"], "not_configured")

        status, created = self.request_json(
            "/api/ingest/signoz-alert",
            {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "LinkDown",
                            "site": "BJYZ",
                            "serial_number": "SIGNOZ-ENDPOINT-SN-001",
                            "rack_position": "RACK-S-01",
                            "device_type": "switch",
                        },
                        "annotations": {
                            "summary": "交换机链路中断",
                            "description": "interface HundredGigE7/0/36 link down",
                        },
                    }
                ]
            },
        )
        self.assertEqual(status, 201)
        incident = created["incidents"][0]
        self.assertEqual(incident["category"], "network")

        status, investigated = self.request_json(
            f"/api/incidents/{incident['id']}/investigate", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            investigated["investigation"]["external_checks"][0]["state"],
            "not_configured",
        )


if __name__ == "__main__":
    unittest.main()
