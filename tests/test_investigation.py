import tempfile
import unittest
import sqlite3
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class EvidenceAwareFakeAI:
    enabled = True

    def enrich(self, _event, _analysis, investigation):
        evidence_id = investigation["evidence"][0]["id"]
        return {
            "impact_summary": "模型把两类存储信号整理为同一调查方向",
            "candidate_causes": [
                {
                    "title": "模型补充的存储路径候选",
                    "evidence_ids": [evidence_id],
                    "counter_evidence": "若同控制器其他盘正常则需继续缩小范围",
                    "status": "high_likelihood",
                }
            ],
            "missing_information": ["控制器下其他盘状态"],
        }


class InvestigationTraceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = IncidentService(
            IncidentStore(str(Path(self.tempdir.name) / "investigation.db"))
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_disk_trace_preserves_provenance_and_does_not_fake_confirmation(self):
        incident = self.service.ingest(
            "log",
            {
                "site": "BJYZ",
                "sn": "TRACE-FULL-SN-001",
                "rack_position": "BJYZ-A-01-01",
                "device_type": "server",
                "summary": "磁盘异常",
                "log_text": (
                    "kernel: blk_update_request: I/O error, dev sdb\n"
                    "smartd: SMART Failure Predicted on /dev/sdb"
                ),
            },
        )
        investigation = incident["investigation"]
        self.assertEqual(investigation["mode"], "rules_only")
        self.assertFalse(investigation["simulation"])
        fact_types = {item["type"] for item in investigation["extracted_facts"]}
        self.assertIn("block_io_error", fact_types)
        self.assertIn("smart_failure", fact_types)
        fields = {item["field"]: item for item in investigation["field_provenance"]}
        self.assertEqual(fields["device.sn"]["method"], "provided")
        self.assertEqual(fields["device.sn"]["reliability"], "reported")
        self.assertTrue(
            all(item["status"] != "confirmed" for item in investigation["hypotheses"])
        )
        self.assertTrue(
            all("confidence" not in item for item in investigation["hypotheses"])
        )
        self.assertEqual(investigation["verification_plan"][0]["risk"], "read_only")

    def test_explicit_incident_key_is_not_presented_as_shared_root_cause(self):
        base = {
            "site": "BJYZ",
            "device_type": "server",
            "summary": "区域温度升高",
            "message": "temperature high 36C; fan full speed",
            "incident_key": "EXTERNAL-GROUP-ONE",
        }
        self.service.ingest("monitor", {**base, "sn": "TRACE-HEAT-001"})
        incident = self.service.ingest("monitor", {**base, "sn": "TRACE-HEAT-002"})
        correlation = incident["investigation"]["correlation"]
        self.assertEqual(correlation["level"], "explicit")
        self.assertIn("没有独立证明", correlation["reason"])

    def test_unrecognized_log_keeps_raw_input_and_reports_insufficient_coverage(self):
        incident = self.service.ingest(
            "log",
            {"site": "BJYZ", "summary": "未知异常", "log_text": "mysterious marker xyz"},
        )
        investigation = incident["investigation"]
        self.assertEqual(investigation["knowledge_retrieval"]["coverage"], "insufficient")
        self.assertEqual(investigation["intake"][0]["raw_text"], "mysterious marker xyz")
        self.assertEqual(investigation["conclusion"]["grade"], "candidate")

    def test_existing_database_is_migrated_without_dropping_incidents_table(self):
        path = str(Path(self.tempdir.name) / "legacy.db")
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE incidents (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                    severity TEXT NOT NULL, category TEXT NOT NULL, site TEXT NOT NULL,
                    summary TEXT NOT NULL, correlation_key TEXT NOT NULL,
                    identity_keys TEXT NOT NULL, devices_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL, analysis_json TEXT NOT NULL,
                    onsite_card_json TEXT NOT NULL, cc_reminder_json TEXT NOT NULL,
                    communication_text TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        store = IncidentStore(path)
        with store.connect() as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(incidents)")
            }
        self.assertIn("investigation_json", columns)

    def test_model_enrichment_adds_only_evidence_referenced_hypothesis(self):
        service = IncidentService(
            IncidentStore(str(Path(self.tempdir.name) / "ai.db")),
            ai=EvidenceAwareFakeAI(),
        )
        incident = service.ingest(
            "log",
            {
                "site": "BJYZ",
                "sn": "AI-INTEGRATION-SN-001",
                "rack_position": "BJYZ-A-02-01",
                "device_type": "server",
                "summary": "存储异常",
                "log_text": "kernel: I/O error, dev sdb",
            },
        )
        investigation = incident["investigation"]
        self.assertEqual(investigation["mode"], "ai_enriched")
        model_hypotheses = [
            item
            for item in investigation["hypotheses"]
            if item["generated_by"] == "model_enhanced"
        ]
        self.assertEqual(len(model_hypotheses), 1)
        self.assertTrue(model_hypotheses[0]["supporting_evidence_ids"])
        self.assertNotEqual(model_hypotheses[0]["status"], "confirmed")
        self.assertEqual(investigation["conclusion"]["grade"], "high_likelihood")
        self.assertEqual(
            investigation["conclusion"]["leading_hypothesis"],
            "模型补充的存储路径候选",
        )


if __name__ == "__main__":
    unittest.main()
