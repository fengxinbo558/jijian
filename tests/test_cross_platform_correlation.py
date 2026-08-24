import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class CrossPlatformCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = IncidentService(
            IncidentStore(str(Path(self.tempdir.name) / "correlation.db"))
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_network_server_and_application_events_share_topology_incident(self):
        result = self.service.run_lab_scenario("network-module-cascade")

        self.assertEqual(len(result["deliveries"]), 4)
        self.assertEqual(len(result["incident_ids"]), 1)
        event_levels = {
            item["event"]["correlation"]["level"] for item in result["deliveries"]
        }
        self.assertEqual(event_levels, {"topology_time_window"})
        incident = result["incidents"][0]
        self.assertEqual(len(incident["inputs"]), 4)
        self.assertGreaterEqual(incident["affected_count"], 3)

    def test_unknown_signal_does_not_auto_merge_from_text_similarity(self):
        base = {
            "source_system": "linux_app",
            "site": "BJYZ",
            "entity": {"device_name": "mystery-host", "device_type": "server"},
            "signal_type": "vendor_unknown_code",
            "severity": "warning",
            "summary": "相似的未知异常文本",
            "raw_payload": {"message": "same words but no deterministic relation"},
        }
        first = self.service.ingest_platform_event({**base, "source_event_id": "UNKNOWN-1"})
        second = self.service.ingest_platform_event({**base, "source_event_id": "UNKNOWN-2"})

        self.assertNotEqual(first["incident"]["id"], second["incident"]["id"])
        self.assertEqual(first["event"]["correlation"]["level"], "insufficient")
        self.assertFalse(first["event"]["correlation"]["deterministic"])


if __name__ == "__main__":
    unittest.main()
