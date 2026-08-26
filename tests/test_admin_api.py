import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from idcops.server import create_server


class AdminAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "admin.db")
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

    def request_json(self, path, payload=None, role="ai_admin"):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json", "X-IDCAI-Role": role},
            method="POST" if data is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_admin_can_view_database_knowledge_and_prompt_raw_content(self):
        status, summary = self.request_json("/api/admin/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["knowledge"]["published"], 48)
        self.assertEqual(summary["prompts"]["published"], 4)

        status, knowledge = self.request_json("/api/admin/knowledge")
        self.assertEqual(status, 200)
        self.assertEqual(len(knowledge["items"]), 48)

        status, prompt = self.request_json("/api/admin/prompts/hypothesis")
        self.assertEqual(status, 200)
        self.assertEqual(prompt["versions"][0]["system_content"], "只输出一个JSON对象。")
        self.assertIn("不得猜测设备身份", prompt["versions"][0]["user_template"])

        status, records = self.request_json("/api/admin/records?type=knowledge")
        self.assertEqual(status, 200)
        self.assertEqual(records["record_type"], "knowledge")
        self.assertGreaterEqual(records["total"], 48)

    def test_prompt_draft_preview_two_step_publish_and_rollback(self):
        status, draft = self.request_json(
            "/api/admin/prompts/hypothesis/versions",
            {
                "version": "hypothesis-v1.1.0",
                "system_content": "仅输出JSON，不得编造。",
                "user_template": "分析 {{event_summary}}，引用 {{evidence}}。",
                "variables": ["event_summary", "evidence"],
                "output_schema": ["impact_summary", "candidate_causes"],
                "settings": {"temperature": 0.1},
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(draft["release_status"], "draft")

        status, preview = self.request_json(
            "/api/admin/prompts/hypothesis/preview",
            {
                "version": "hypothesis-v1.1.0",
                "variables": {"event_summary": "磁盘告警", "evidence": ["E-01"]},
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("磁盘告警", preview["messages"][1]["content"])
        self.assertEqual(preview["messages"][0]["content"], "仅输出JSON，不得编造。")

        status, release = self.request_json(
            "/api/admin/releases/test",
            {"asset_type": "prompt", "asset_key": "hypothesis", "version": "hypothesis-v1.1.0"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(release["status"], "tested")

        status, prepared = self.request_json(
            f"/api/admin/releases/{release['id']}/prepare", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(prepared["status"], "prepared")

        request = urllib.request.Request(
            self.base + f"/api/admin/releases/{release['id']}/publish",
            data=json.dumps({"confirmed_online": False}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-IDCAI-Role": "ai_admin"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 400)

        status, published = self.request_json(
            f"/api/admin/releases/{release['id']}/publish", {"confirmed_online": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(published["status"], "published")

        _status, prompt = self.request_json("/api/admin/prompts/hypothesis")
        self.assertEqual(prompt["published_version"], "hypothesis-v1.1.0")

        status, rolled_back = self.request_json(
            f"/api/admin/releases/{release['id']}/rollback", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(rolled_back["status"], "rolled_back")
        _status, prompt = self.request_json("/api/admin/prompts/hypothesis")
        self.assertEqual(prompt["published_version"], "hypothesis-v1.0.0")

    def test_non_admin_cannot_write_assets(self):
        request = urllib.request.Request(
            self.base + "/api/admin/prompts/hypothesis/versions",
            data=json.dumps({"version": "blocked"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-IDCAI-Role": "onsite_operator"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 403)

    def test_model_provider_adapters_are_visible_without_returning_secrets(self):
        status, providers = self.request_json("/api/admin/providers")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(providers["items"]), 3)
        status, updated = self.request_json(
            "/api/admin/providers/private-partner-adapter",
            {
                "display_name": "合作厂商私有部署",
                "provider_type": "private_partner",
                "endpoint": "http://partner-model.internal/v1",
                "model": "ops-model",
                "enabled": True,
                "secret_configured": True,
                "api_key": "NEVER-RETURN-THIS",
                "data_residency": "customer_datacenter",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["connection_state"], "configured_not_tested")
        self.assertNotIn("NEVER-RETURN-THIS", json.dumps(updated, ensure_ascii=False))

    def test_retrieval_test_uses_published_knowledge_without_creating_incident(self):
        _status, before = self.request_json("/api/admin/summary")
        status, index = self.request_json("/api/admin/rag-index")
        self.assertEqual(status, 200)
        self.assertEqual(index["published_cards"], 48)
        self.assertFalse(index["pretrained_semantic_model"])

        status, result = self.request_json(
            "/api/admin/retrieval-tests",
            {
                "text": "nvme0 I/O timeout blk_update_request",
                "domain": "storage",
                "device_type": "server",
            },
        )
        self.assertEqual(status, 201)
        self.assertFalse(result["production_incident_created"])
        self.assertEqual(result["coverage"], "matched")
        self.assertTrue(result["hits"])
        self.assertTrue(all(item["domain"] == "storage" for item in result["hits"]))

        _status, after = self.request_json("/api/admin/summary")
        self.assertEqual(after["incidents"], before["incidents"])
        self.assertEqual(after["retrieval_test_runs"], before["retrieval_test_runs"] + 1)
        status, activity = self.request_json("/api/admin/activity")
        self.assertEqual(status, 200)
        retrieval = next(item for item in activity["items"] if item["id"] == result["id"])
        self.assertEqual(retrieval["activity_type"], "retrieval_test")
        self.assertFalse(retrieval["details"]["production_incident_created"])

    def test_constraint_draft_can_be_tested_published_and_rolled_back(self):
        status, policy = self.request_json("/api/admin/constraints/investigation-policy")
        self.assertEqual(status, 200)
        self.assertEqual(policy["published_version"], "1.0.0")
        self.assertTrue(all(item["editable"] is False for item in policy["hard_guards"]))

        settings = dict(policy["published"]["settings"])
        settings["retrieval_top_k"] = 3
        status, draft = self.request_json(
            "/api/admin/constraints/investigation-policy/versions",
            {"version": "1.1.0", "settings": settings},
        )
        self.assertEqual(status, 201)
        self.assertEqual(draft["release_status"], "draft")

        status, release = self.request_json(
            "/api/admin/releases/test",
            {
                "asset_type": "constraint",
                "asset_key": "investigation-policy",
                "version": "1.1.0",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(release["status"], "tested")
        self.request_json(f"/api/admin/releases/{release['id']}/prepare", {})
        status, published = self.request_json(
            f"/api/admin/releases/{release['id']}/publish", {"confirmed_online": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(published["status"], "published")

        _status, index = self.request_json("/api/admin/rag-index")
        self.assertEqual(index["constraint_version"], "1.1.0")
        _status, rolled_back = self.request_json(
            f"/api/admin/releases/{release['id']}/rollback", {}
        )
        self.assertEqual(rolled_back["status"], "rolled_back")
        _status, policy = self.request_json("/api/admin/constraints/investigation-policy")
        self.assertEqual(policy["published_version"], "1.0.0")

    def test_non_admin_cannot_read_ai_control_console(self):
        for path in (
            "/api/admin/summary",
            "/api/admin/constraints",
            "/api/admin/retrieval-tests",
            "/api/admin/rag-index",
        ):
            request = urllib.request.Request(
                self.base + path,
                headers={"X-IDCAI-Role": "onsite_operator"},
                method="GET",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(raised.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
