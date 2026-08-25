import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idcops.server import create_server


class DrillAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "drill-api.db")
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

    def request(self, path, role="ai_admin", actor="drill-admin", payload=None):
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
        with urllib.request.urlopen(request, timeout=6) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_admin_can_start_and_advance_real_drill_api(self):
        status, catalog = self.request("/api/drills/catalog")
        self.assertEqual(status, 200)
        self.assertEqual(catalog["count"], 25)
        status, run = self.request(
            "/api/drills/runs",
            payload={
                "mode": "directed",
                "scenario_id": "net-optical-module",
                "playback_mode": "auto",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["status"], "waiting_human")
        self.assertNotIn("hidden_truth", run)
        _status, run = self.request(
            f"/api/drills/runs/{run['id']}/feedback",
            payload={"action_id": "query_config", "notes": "配置正常"},
        )
        self.assertEqual(run["current_step_id"], "network-replace-module")

    def test_non_admin_cannot_view_or_trigger_drills(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request("/api/drills/catalog", role="onsite_operator")
        self.assertEqual(raised.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                "/api/drills/runs",
                role="interface_person",
                payload={"mode": "blind", "category": "network"},
            )
        self.assertEqual(raised.exception.code, 403)

    def test_blind_answer_reveal_requires_terminal_and_owner_or_super_admin(self):
        _status, run = self.request(
            "/api/drills/runs",
            actor="owner-a",
            payload={
                "mode": "blind",
                "category": "application",
                "autostart": False,
            },
        )
        _status, active = self.request(
            f"/api/drills/runs/{run['id']}?reveal=1", actor="owner-a"
        )
        self.assertNotIn("hidden_truth", active)
        self.request(
            f"/api/drills/runs/{run['id']}/terminate",
            actor="owner-a",
            payload={"reason": "结束盲测"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                f"/api/drills/runs/{run['id']}?reveal=1", actor="another-admin"
            )
        self.assertEqual(raised.exception.code, 403)
        _status, revealed = self.request(
            f"/api/drills/runs/{run['id']}?reveal=1", actor="owner-a"
        )
        self.assertIn("hidden_truth", revealed)
        _status, super_view = self.request(
            f"/api/drills/runs/{run['id']}?reveal=1",
            role="super_admin",
            actor="root-audit",
        )
        self.assertIn("hidden_truth", super_view)


if __name__ == "__main__":
    unittest.main()
