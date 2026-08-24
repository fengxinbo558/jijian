import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idcops.server import create_server


class LabAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "lab-api.db")
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

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def event(self):
        return {
            "source_system": "network_nms",
            "source_event_id": "API-NMS-001",
            "site": "BJYZ",
            "entity": {
                "device_name": "switch-a",
                "interface": "Ethernet1/1",
                "device_type": "switch",
            },
            "signal_type": "link_down",
            "severity": "critical",
            "summary": "交换机端口中断",
            "raw_payload": {"message": "Ethernet1/1 link down"},
        }

    def test_platform_state_and_event_delivery_are_real_api_calls(self):
        status, platforms = self.request("/api/lab/platforms")
        self.assertEqual(status, 200)
        self.assertEqual(len(platforms["items"]), 6)

        status, created = self.request("/api/lab/events", self.event())
        self.assertEqual(status, 201)
        self.assertTrue(created["accepted"])
        self.assertTrue(created["incident"]["investigation"]["simulation"])

        status, events = self.request("/api/lab/events")
        self.assertEqual(status, 200)
        self.assertEqual(events["items"][0]["source_event_id"], "API-NMS-001")
        self.assertEqual(events["items"][0]["incident_id"], created["incident"]["id"])

    def test_disconnected_platform_returns_503(self):
        self.request(
            "/api/lab/platforms/network_nms/state",
            {"state": "disconnected"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request("/api/lab/events", self.event())
        self.assertEqual(raised.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
