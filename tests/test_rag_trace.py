import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from idcops.server import create_server


class RagTraceAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "rag.db")
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
            headers={"Content-Type": "application/json", "X-IDCAI-Role": "ai_admin"},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_every_incident_records_eight_visible_rag_steps_and_hit_reasons(self):
        status, incident = self.request_json(
            "/api/ingest/log",
            {
                "site": "BJYZ",
                "sn": "RAG-TRACE-SN-001",
                "rack_position": "RACK-R-01",
                "device_type": "server",
                "summary": "NVMe I/O 异常",
                "log_text": "nvme nvme0: I/O timeout\nblk_update_request: I/O error, dev nvme0n1",
            },
        )
        self.assertEqual(status, 201)
        run_id = incident["latest_rag_run_id"]

        status, trace = self.request_json(f"/api/admin/rag-runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(trace["incident_id"], incident["id"])
        self.assertEqual(len(trace["steps"]), 8)
        self.assertEqual(trace["steps"][0]["type"], "raw_input")
        self.assertEqual(trace["steps"][4]["status"], "not_run")
        self.assertTrue(trace["hits"])
        self.assertIn("vector_similarity", trace["hits"][0]["retrieval"])
        self.assertTrue(trace["hits"][0]["reasons"])

        status, listing = self.request_json("/api/admin/rag-runs")
        self.assertEqual(status, 200)
        self.assertEqual(listing["items"][0]["id"], run_id)


if __name__ == "__main__":
    unittest.main()
