import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idcops.server import create_server


class OperationAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "operations-api.db")
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

    def request(self, path, payload=None, role="ai_admin", actor="local-admin"):
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
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_role_guarded_operation_flow(self):
        status, case = self.request(
            "/api/operations/import",
            {
                "order_no": "OMS-API-001",
                "site": "BJYZ",
                "target_sn": "FULL-SN-API-001",
                "rack_position": "BJYZ-RACK-01",
                "power_policy": "needs_confirmation",
            },
            role="interface_person",
            actor="sim-a",
        )
        self.assertEqual(status, 201)
        operation_id = case["id"]

        _status, case = self.request(
            f"/api/operations/{operation_id}/identity",
            {"observed_sn": "FULL-SN-API-001", "method": "barcode"},
            role="onsite_operator",
            actor="onsite-a",
        )
        self.assertEqual(case["status"], "awaiting_permission")

        _status, case = self.request(
            f"/api/operations/{operation_id}/permission",
            {"decision": "allowed", "reason": "业务已迁移"},
            role="interface_person",
            actor="sim-a",
        )
        self.assertEqual(case["status"], "awaiting_review")

        _status, case = self.request(
            f"/api/operations/{operation_id}/review",
            {"decision": "approved", "review_mode": "onsite_peer"},
            role="onsite_operator",
            actor="onsite-b",
        )
        self.assertEqual(case["status"], "ready")

        _status, case = self.request(
            f"/api/operations/{operation_id}/start",
            {},
            role="onsite_operator",
            actor="onsite-a",
        )
        self.assertEqual(case["status"], "operating")

    def test_onsite_user_cannot_import_or_grant_permission(self):
        request = urllib.request.Request(
            self.base + "/api/operations/import",
            data=json.dumps(
                {"order_no": "NO", "target_sn": "FULL", "rack_position": "RACK"}
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-IDCAI-Role": "onsite_operator",
                "X-IDCAI-User": "onsite-a",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
