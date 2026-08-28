import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from idcops.server import create_server

try:
    from tests.test_asset_governance import knowledge_candidate
except ImportError:  # `unittest discover -s tests` imports modules without package context.
    from test_asset_governance import knowledge_candidate


class AssetGovernanceAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "governance-api.db")
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

    def request(self, path, payload=None, role="ai_admin", method=None, actor="api-tester"):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-IDCAI-Role": role,
                "X-IDCAI-User": actor,
            },
            method=method or ("POST" if data is not None else "GET"),
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_admin_can_browse_update_and_trace_assets(self):
        status, summary = self.request("/api/admin/governance/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["asset_counts"]["knowledge"], 48)

        status, catalog = self.request(
            "/api/admin/assets?asset_type=knowledge&page=1&page_size=5&q=NVMe"
        )
        self.assertEqual(status, 200)
        self.assertLessEqual(len(catalog["items"]), 5)
        self.assertTrue(catalog["items"])

        key = urllib.parse.quote("STORAGE-IO-001")
        status, detail = self.request(f"/api/admin/assets/knowledge/{key}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["content"]["card_id"], "STORAGE-IO-001")

        status, metadata = self.request(
            f"/api/admin/assets/knowledge/{key}",
            {"owner": "storage-lead", "tags": ["重点复审"]},
            method="PATCH",
        )
        self.assertEqual(status, 200)
        self.assertEqual(metadata["owner"], "storage-lead")

        status, activity = self.request("/api/admin/activity")
        self.assertEqual(status, 200)
        self.assertTrue(
            any(
                item["activity_type"] == "asset_governance"
                and item["asset"] == "knowledge:STORAGE-IO-001"
                for item in activity["items"]
            )
        )

        status, lineage = self.request(
            f"/api/admin/lineage?asset_type=knowledge&asset_key={key}&version=1.0.0"
        )
        self.assertEqual(status, 200)
        self.assertTrue(lineage["sources"])

    def test_import_issue_relation_feedback_and_test_case_endpoints(self):
        candidate = knowledge_candidate()
        status, batch = self.request(
            "/api/admin/import-batches",
            {"format": "json", "source_label": "API导入", "items": [candidate]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(batch["summary"]["ready"], 1)

        status, confirmed = self.request(
            f"/api/admin/import-batches/{batch['id']}/confirm", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(confirmed["status"], "completed")

        status, relation = self.request(
            "/api/admin/asset-relations",
            {
                "source_type": "knowledge",
                "source_key": candidate["id"],
                "source_version": candidate["version"],
                "relation_type": "related",
                "target_type": "knowledge",
                "target_key": "STORAGE-IO-001",
                "target_version": "1.0.0",
                "basis": {"reason": "共同调查路径"},
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(relation["relation_type"], "related")

        status, feedback = self.request(
            "/api/asset-feedback",
            {
                "asset_type": "knowledge",
                "asset_key": candidate["id"],
                "version": candidate["version"],
                "incident_id": "INC-API",
                "outcome": "unverified",
                "note": "测试反馈",
            },
            role="onsite_operator",
            actor="onsite-a",
        )
        self.assertEqual(status, 201)
        self.assertEqual(feedback["created_by"], "onsite-a")

        status, case = self.request(
            "/api/admin/test-cases",
            {
                "case_key": "CASE-API-001",
                "name": "API测试用例",
                "domain": "network",
                "version": "1.0.0",
                "input": {"text": "link down"},
                "expected": {"knowledge_cards": [candidate["id"]]},
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(case["case_key"], "CASE-API-001")

        status, relations = self.request("/api/admin/asset-relations")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == relation["id"] for item in relations["items"]))

    def test_issue_can_be_resolved_and_is_audited(self):
        issue = self.server.service.governance.create_issue(
            "near_duplicate",
            "review",
            "knowledge",
            "STORAGE-IO-001",
            "1.0.0",
            {"reason": "测试"},
            "test",
            related=("knowledge", "STORAGE-SMART-002", "1.0.0"),
        )
        status, resolved = self.request(
            f"/api/admin/governance/issues/{issue['id']}/resolve",
            {"action": "keep_separate", "note": "适用场景不同"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(resolved["status"], "resolved")
        status, audit = self.request("/api/admin/governance/audit")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["action"] == "governance_issue_resolved" for item in audit["items"]))

    def test_non_admin_cannot_open_governance_admin_api(self):
        for path in (
            "/api/admin/governance/summary",
            "/api/admin/assets",
            "/api/admin/governance/issues",
            "/api/admin/import-batches",
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
