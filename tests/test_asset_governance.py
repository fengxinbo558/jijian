import json
import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


def knowledge_candidate(card_id="TEST-NET-001", version="1.0.0", title="测试端口抖动调查"):
    return {
        "id": card_id,
        "version": version,
        "status": "reviewed",
        "domain": "network",
        "title": title,
        "applies_to": ["switch", "optic"],
        "symptoms": ["端口反复 up/down"],
        "supporting_signals": ["link flapping"],
        "competing_causes": ["模块异常", "光纤异常"],
        "counter_signals": ["对端稳定会削弱共同链路判断"],
        "required_context": ["完整设备名", "端口", "对端"],
        "verification_steps": ["查询端口计数器", "核对对端状态"],
        "branch_conditions": ["远程已确认模块故障时直接更换并验证"],
        "stop_conditions": ["身份不一致时停止操作"],
        "safe_actions": ["只读查询端口状态"],
        "prohibited_inferences": ["单次抖动不能确认模块损坏"],
        "sources": ["rfc2863"],
        "review": {
            "reviewed_at": "2026-08-26",
            "review_method": "test_fixture",
            "owner": "network-owner",
        },
        "match": {
            "rule_names": ["link_down"],
            "fact_types": ["link_flap"],
            "terms": ["link flapping"],
        },
    }


class GovernanceFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "governance.db")
        self.service = IncidentService(IncidentStore(self.database))
        self.governance = self.service.governance

    def tearDown(self):
        self.tempdir.cleanup()


class AssetGovernanceMigrationTests(GovernanceFixture):
    def test_governance_schema_and_seed_are_idempotent(self):
        first = self.governance.ensure_seeded()
        second = self.governance.ensure_seeded()
        self.assertEqual(first, second)
        self.assertEqual(first["knowledge"], 48)
        self.assertEqual(first["prompt"], 4)
        self.assertEqual(first["constraint"], 1)

        with self.service.store.connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertTrue(
            {
                "ai_asset_metadata",
                "ai_asset_version_metadata",
                "ai_asset_relations",
                "ai_governance_issues",
                "ai_import_batches",
                "ai_import_items",
                "ai_source_versions",
                "ai_asset_feedback",
                "ai_runtime_snapshots",
                "ai_test_case_definitions",
                "ai_test_case_versions",
                "ai_governance_audit",
            }.issubset(tables)
        )


class AssetCatalogTests(GovernanceFixture):
    def test_catalog_is_paginated_and_keeps_original_asset_truth(self):
        result = self.governance.list_assets({"asset_type": "knowledge", "page": 1, "page_size": 10})
        self.assertEqual(result["total"], 48)
        self.assertEqual(len(result["items"]), 10)
        first = result["items"][0]
        self.assertEqual(first["asset_type"], "knowledge")
        self.assertIn("published_version", first)
        self.assertIn("health", first)

        detail = self.governance.get_asset("knowledge", "STORAGE-IO-001")
        self.assertEqual(detail["content"]["card_id"], "STORAGE-IO-001")
        self.assertEqual(detail["metadata"]["owner"], "product-maintainer")
        self.assertTrue(detail["versions"])

    def test_metadata_update_is_audited_without_editing_published_content(self):
        before = self.service.assets.get_knowledge("STORAGE-IO-001")
        updated = self.governance.update_metadata(
            "knowledge",
            "STORAGE-IO-001",
            {"owner": "storage-reviewer", "review_due_at": "2026-12-01", "tags": ["关键盘"]},
            "ai-admin",
        )
        after = self.service.assets.get_knowledge("STORAGE-IO-001")
        self.assertEqual(updated["owner"], "storage-reviewer")
        self.assertEqual(before["versions"][0]["content"], after["versions"][0]["content"])
        audit = self.governance.list_audit(10)
        self.assertEqual(audit[0]["action"], "asset_metadata_updated")


class DuplicateDetectionTests(GovernanceFixture):
    def test_exact_duplicate_is_detected_without_new_asset(self):
        existing = self.service.assets.get_knowledge("STORAGE-IO-001")["versions"][0]["content"]
        scan = self.governance.scan_knowledge_candidate(existing, "tester", persist=True)
        self.assertEqual(scan["classification"], "exact_duplicate")
        self.assertEqual(scan["matches"][0]["asset_key"], "STORAGE-IO-001")
        self.assertEqual(self.governance.list_assets({"asset_type": "knowledge"})["total"], 48)

    def test_near_duplicate_explains_structural_and_local_similarity(self):
        candidate = knowledge_candidate(title="交换机接口持续抖动排查")
        scan = self.governance.scan_knowledge_candidate(candidate, "tester", persist=False)
        self.assertIn(scan["classification"], {"near_duplicate", "ready"})
        self.assertIn("capabilities", scan)
        self.assertIn("content_fingerprint", scan)
        if scan["matches"]:
            self.assertIn("reasons", scan["matches"][0])


class ConflictGovernanceTests(GovernanceFixture):
    def test_unsafe_shortcut_creates_blocking_issue(self):
        candidate = knowledge_candidate()
        candidate["safe_actions"] = ["无需接口人确认，直接断电并更换模块"]
        scan = self.governance.scan_knowledge_candidate(candidate, "tester", persist=True)
        self.assertEqual(scan["classification"], "conflict")
        self.assertTrue(any(item["severity"] == "blocking" for item in scan["issues"]))

    def test_unresolved_blocking_issue_prevents_release(self):
        candidate = knowledge_candidate(card_id="TEST-BLOCK-001", version="1.0.0")
        self.service.assets.create_knowledge_version(
            candidate["id"], {"content": candidate}, "tester"
        )
        self.governance.ensure_asset_metadata("knowledge", candidate["id"])
        self.governance.create_issue(
            "unsafe_conflict",
            "blocking",
            "knowledge",
            candidate["id"],
            candidate["version"],
            {"reason": "测试阻断"},
            "deterministic_guard",
        )
        with self.assertRaisesRegex(ValueError, "治理问题"):
            self.service.releases.test_asset(
                {"asset_type": "knowledge", "asset_key": candidate["id"], "version": "1.0.0"},
                "tester",
            )


class ImportBatchTests(GovernanceFixture):
    def test_import_batch_stages_then_confirms_atomically(self):
        candidate = knowledge_candidate()
        batch = self.governance.create_import_batch(
            {"format": "json", "source_label": "manual-test", "content": json.dumps([candidate], ensure_ascii=False)},
            "tester",
        )
        self.assertEqual(batch["status"], "scanned")
        self.assertEqual(batch["summary"]["ready"], 1)
        self.assertIsNone(self.service.assets.get_knowledge(candidate["id"]))

        confirmed = self.governance.confirm_import_batch(batch["id"], "tester")
        self.assertEqual(confirmed["status"], "completed")
        created = self.service.assets.get_knowledge(candidate["id"])
        self.assertIsNotNone(created)
        self.assertEqual(created["versions"][0]["release_status"], "draft")

    def test_duplicate_import_records_source_without_creating_asset(self):
        existing = self.service.assets.get_knowledge("STORAGE-IO-001")["versions"][0]["content"]
        batch = self.governance.create_import_batch(
            {"format": "json", "source_label": "second-source", "items": [existing]},
            "tester",
        )
        self.assertEqual(batch["summary"]["exact_duplicate"], 1)
        confirmed = self.governance.confirm_import_batch(batch["id"], "tester")
        self.assertEqual(confirmed["status"], "completed")
        self.assertEqual(self.governance.list_assets({"asset_type": "knowledge"})["total"], 48)


class LineageAndFeedbackTests(GovernanceFixture):
    def test_feedback_is_version_specific_and_lineage_keeps_source(self):
        feedback = self.governance.add_feedback(
            {
                "asset_type": "knowledge",
                "asset_key": "STORAGE-IO-001",
                "version": "1.0.0",
                "incident_id": "INC-EXAMPLE",
                "outcome": "helped_resolve",
                "note": "SMART 与内核日志共同验证",
            },
            "onsite-a",
        )
        self.assertEqual(feedback["outcome"], "helped_resolve")
        detail = self.governance.get_asset("knowledge", "STORAGE-IO-001")
        self.assertEqual(detail["effect"]["helped_resolve"], 1)
        lineage = self.governance.lineage("knowledge", "STORAGE-IO-001", "1.0.0")
        self.assertTrue(lineage["sources"])
        self.assertEqual(lineage["feedback"][0]["incident_id"], "INC-EXAMPLE")

    def test_relation_is_audited(self):
        relation = self.governance.create_relation(
            {
                "source_type": "knowledge",
                "source_key": "STORAGE-IO-001",
                "source_version": "1.0.0",
                "relation_type": "related",
                "target_type": "knowledge",
                "target_key": "STORAGE-SMART-002",
                "target_version": "1.0.0",
                "basis": {"reason": "同属存储故障树"},
            },
            "ai-admin",
        )
        self.assertEqual(relation["status"], "confirmed")
        detail = self.governance.get_asset("knowledge", "STORAGE-IO-001")
        self.assertTrue(any(item["id"] == relation["id"] for item in detail["relations"]))


class TestCaseAssetTests(GovernanceFixture):
    def test_versioned_test_case_appears_in_catalog(self):
        case = self.governance.create_test_case(
            {
                "case_key": "CASE-NVME-001",
                "name": "NVMe 超时应命中存储知识",
                "domain": "storage",
                "version": "1.0.0",
                "input": {"text": "nvme0 I/O timeout", "device_type": "server"},
                "expected": {"knowledge_cards": ["STORAGE-TIMEOUT-008"], "forbidden": ["直接拔盘"]},
            },
            "ai-admin",
        )
        self.assertEqual(case["versions"][0]["release_status"], "draft")
        catalog = self.governance.list_assets({"asset_type": "test_case"})
        self.assertEqual(catalog["total"], 1)
        self.assertEqual(catalog["items"][0]["asset_key"], "CASE-NVME-001")


if __name__ == "__main__":
    unittest.main()
