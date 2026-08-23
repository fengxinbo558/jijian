import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from idcops.integrations import IntegrationHub, IntegrationSettings


class FakeIntegrationHandler(BaseHTTPRequestHandler):
    def log_message(self, _fmt, *_args):
        return

    def do_GET(self):  # noqa: N802
        if self.path in {"/api/v1/health", "/healthz"}:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if self.path == "/api/v5/query_range":
            FakeIntegrationHandler.last_signoz_request = body
            signal = body["compositeQuery"]["queries"][0]["spec"]["signal"]
            if signal == "metrics":
                response = {
                    "data": {
                        "rows": [
                            {"metric": "system.memory.usage", "value": 7340032000}
                        ]
                    }
                }
            else:
                response = {
                    "data": {
                        "rows": [
                            {
                                "body": "kernel: Buffer I/O error on dev sdb",
                                "host.name": "bjyz-host-01",
                            },
                            {"body": "smartd: SMART Failure Predicted on /dev/sdb"},
                        ]
                    }
                }
        elif self.path == "/api/chat":
            FakeIntegrationHandler.last_holmes_request = body
            response = {
                "analysis": "查询结果同时出现 I/O error 与 SMART 失败，仍需核对槽位映射。",
                "tool_calls": [
                    {
                        "tool_name": "signoz_query_logs",
                        "description": "按主机和时间窗查询日志",
                    }
                ],
            }
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeIntegrationHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_unconfigured_sources_are_honest(self):
        hub = IntegrationHub(IntegrationSettings())
        statuses = {item["id"]: item for item in hub.source_statuses()}
        self.assertEqual(statuses["manual_log"]["state"], "available")
        self.assertEqual(statuses["signoz"]["state"], "not_configured")
        self.assertEqual(statuses["holmes"]["state"], "not_configured")
        self.assertEqual(hub.connected_collectors(), [])

    def test_config_rejects_credentials_in_url(self):
        with self.assertRaises(ValueError):
            IntegrationSettings.from_environ(
                {"IDCAI_SIGNOZ_URL": "http://user:secret@example.invalid"}
            )

    def test_signoz_and_holmes_read_only_investigation(self):
        settings = IntegrationSettings(
            signoz_url=self.base,
            signoz_api_key="signoz-secret",
            holmes_url=self.base,
            holmes_api_key="holmes-secret",
            holmes_model="local-model",
            request_timeout=2,
            query_window_minutes=15,
            max_records=10,
            max_response_bytes=100_000,
        )
        hub = IntegrationHub(settings)
        statuses = {item["id"]: item for item in hub.source_statuses()}
        self.assertEqual(statuses["signoz"]["state"], "connected")
        self.assertEqual(statuses["holmes"]["state"], "connected")
        incident = {
            "id": "INC-TEST",
            "site": "BJYZ",
            "title": "磁盘异常",
            "summary": "服务器磁盘告警",
            "created_at": "2026-08-24T01:00:00+00:00",
            "updated_at": "2026-08-24T01:05:00+00:00",
            "devices": [
                {
                    "sn": "FULL-SN-001",
                    "name": "bjyz-host-01",
                    "ip": "10.0.0.1",
                    "rack_position": "BJYZ-A-01",
                }
            ],
            "investigation": {"extracted_facts": []},
        }
        observations = hub.investigate(incident)
        self.assertEqual(observations[0]["state"], "completed")
        self.assertEqual(observations[0]["record_count"], 2)
        self.assertEqual(observations[0]["metric_count"], 1)
        self.assertEqual(len(observations[0]["tool_calls"]), 2)
        self.assertTrue(observations[0]["tool_calls"][0]["read_only"])
        self.assertEqual(observations[1]["state"], "completed")
        self.assertEqual(observations[1]["tool_calls"][0]["tool"], "signoz_query_logs")
        self.assertEqual(FakeIntegrationHandler.last_holmes_request["model"], "local-model")
        serialized = json.dumps(observations, ensure_ascii=False)
        self.assertNotIn("signoz-secret", serialized)
        self.assertNotIn("holmes-secret", serialized)


if __name__ == "__main__":
    unittest.main()
