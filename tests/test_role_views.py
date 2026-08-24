import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idcops.server import create_server


class RoleViewTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "roles.db")
        web_dir = str(Path(__file__).resolve().parent.parent / "web")
        self.server = create_server("127.0.0.1", 0, database, web_dir)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        result = self.server.service.run_lab_scenario("network-module-cascade")
        self.incident_id = result["incident_ids"][0]
        self.run_id = self.server.service.run_agent(self.incident_id, "test_stub", 2)["id"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(self, path, role, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-IDCAI-Role": role,
                "X-IDCAI-User": "tester",
            },
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_onsite_and_interface_person_receive_different_incident_fields(self):
        _status, onsite = self.request(
            f"/api/incidents/{self.incident_id}", "onsite_operator"
        )
        _status, interface = self.request(
            f"/api/incidents/{self.incident_id}", "interface_person"
        )
        self.assertNotIn("knowledge_retrieval", onsite["investigation"])
        self.assertNotIn("rule_matches", onsite["investigation"])
        self.assertIn("rule_matches", interface["investigation"])
        self.assertEqual(onsite["access_scope"]["role"], "onsite_operator")

    def test_agent_trace_is_admin_only_and_raw_fields_are_hidden_by_default(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(f"/api/agent/runs/{self.run_id}", "onsite_operator")
        self.assertEqual(raised.exception.code, 403)
        _status, run = self.request(f"/api/agent/runs/{self.run_id}", "ai_admin")
        self.assertNotIn("raw_payload", json.dumps(run, ensure_ascii=False))
        self.assertTrue(run["access_scope"]["raw_requires_break_glass"])

    def test_only_super_admin_can_open_raw_record_with_reason_and_confirmation(self):
        payload = {
            "record_type": "incident",
            "record_id": self.incident_id,
            "reason": "复核模型结论来源",
            "confirmed": True,
        }
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request("/api/admin/raw-access", "ai_admin", payload)
        self.assertEqual(raised.exception.code, 403)
        status, opened = self.request("/api/admin/raw-access", "super_admin", payload)
        self.assertEqual(status, 200)
        self.assertEqual(opened["record_id"], self.incident_id)
        self.assertIn("inputs", opened["raw"])
        _status, audit = self.request("/api/admin/raw-access-audit", "super_admin")
        self.assertEqual(audit["items"][0]["actor"], "tester")


if __name__ == "__main__":
    unittest.main()
