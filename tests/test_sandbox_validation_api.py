import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idcops.server import create_server


class SandboxValidationAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "sandbox-api.db")
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

    def request(self, path, role="ai_admin", actor="sandbox-admin", payload=None):
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
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_ai_admin_can_run_and_inspect_without_hidden_answer(self):
        status, summary = self.request("/api/admin/sandbox/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["suite"]["case_count"], 120)
        self.assertFalse(summary["boundaries"]["production_accuracy_claimed"])

        status, run = self.request(
            "/api/admin/sandbox/runs",
            payload={"seed": 20260827, "tracks": ["baseline", "agent"]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["tracks"]["agent"]["status"], "not_run")

        status, listing = self.request(f"/api/admin/sandbox/runs/{run['id']}/cases?limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["items"]), 5)
        case_id = listing["items"][0]["case_id"]
        status, detail = self.request(f"/api/admin/sandbox/runs/{run['id']}/cases/{case_id}")
        self.assertEqual(status, 200)
        self.assertNotIn("secret", detail)
        self.assertIn("baseline", detail["tracks"])

        status, report = self.request(f"/api/admin/sandbox/reports/{run['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(report["run_id"], run["id"])
        self.assertTrue(report["production_unchanged"])

    def test_permissions_and_reveal_boundary(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request("/api/admin/sandbox/summary", role="onsite_operator")
        self.assertEqual(raised.exception.code, 403)

        _status, run = self.request(
            "/api/admin/sandbox/runs",
            payload={"seed": 88, "tracks": ["baseline"]},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                f"/api/admin/sandbox/runs/{run['id']}/reveal",
                role="ai_admin",
                payload={"confirmed": True},
            )
        self.assertEqual(raised.exception.code, 403)

        status, revealed = self.request(
            f"/api/admin/sandbox/runs/{run['id']}/reveal",
            role="super_admin",
            actor="root-audit",
            payload={"confirmed": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(revealed["suite_status"], "revealed")
        self.assertEqual(len(revealed["answers"]), 120)


if __name__ == "__main__":
    unittest.main()
