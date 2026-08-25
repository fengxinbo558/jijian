import json
import tempfile
import unittest
from pathlib import Path

from idcops.production import ProductionGovernance
from idcops.public_datasets import PublicDatasetService
from idcops.store import IncidentStore


class PublicDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = IncidentStore(str(self.root / "datasets.db"))
        self.incidents = []

        def ingest(_source, payload):
            incident = {"id": f"INC-{len(self.incidents) + 1:03d}", "title": payload["summary"]}
            self.incidents.append(incident)
            return incident

        self.governance = ProductionGovernance(self.store, ingest)
        catalog = Path(__file__).resolve().parent.parent / "data" / "public-datasets" / "catalog.json"
        self.datasets = PublicDatasetService(
            self.store, self.governance, catalog, self.root / "cache"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_catalog_explains_six_sources_and_distribution_policy(self):
        items = self.datasets.list_datasets()
        self.assertEqual(len(items), 6)
        self.assertTrue(all(item["license_summary"] for item in items))
        self.assertTrue(all(item["truth_level"] for item in items))
        self.assertIn("local_cache_only", {item["distribution_policy"] for item in items})

    def test_loghub_and_gaia_samples_create_audited_import_reports(self):
        log_report = self.datasets.import_sample(
            "loghub-linux",
            "tester",
            sample_text="kernel: device ready\nkernel: I/O error on sda\nservice failed to start",
        )
        self.assertEqual(log_report["status"], "completed")
        self.assertEqual(log_report["record_count"], 3)
        self.assertGreaterEqual(log_report["alert_count"], 1)
        self.assertTrue(log_report["checksum"])

        gaia_report = self.datasets.import_sample(
            "gaia-aiops",
            "tester",
            sample_text="timestamp,value,label\n1546272000000,10,0\n1546272300000,99,1\n",
        )
        self.assertEqual(gaia_report["record_count"], 2)
        self.assertEqual(gaia_report["alert_count"], 1)
        self.assertEqual(len(self.datasets.list_imports()), 2)

    def test_redfish_healthy_mockup_does_not_invent_a_fault(self):
        sample = json.dumps(
            {
                "Id": "437XR1138R2",
                "SerialNumber": "437XR1138R2",
                "Model": "3500RX",
                "Status": {"State": "Enabled", "Health": "OK"},
            }
        )
        report = self.datasets.import_sample(
            "dmtf-redfish", "tester", sample_text=sample
        )
        self.assertEqual(report["record_count"], 1)
        self.assertEqual(report["alert_count"], 0)
        self.assertIn("健康", report["report"]["summary"])

    def test_runtime_generator_is_explicitly_not_fake_production_data(self):
        result = self.datasets.import_sample("otel-demo", "tester")
        self.assertEqual(result["status"], "requires_runtime")
        self.assertIn("本地运行", result["message"])


if __name__ == "__main__":
    unittest.main()
