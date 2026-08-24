import tempfile
import unittest
from pathlib import Path

from idcops.lab import PlatformUnavailable
from idcops.service import IncidentService
from idcops.store import IncidentStore


def event_payload(source_event_id="NMS-001"):
    return {
        "source_system": "network_nms",
        "source_event_id": source_event_id,
        "occurred_at": "2026-08-24T10:00:00+08:00",
        "site": "BJYZ",
        "incident_key": "NETWORK-LAB-001",
        "entity": {
            "device_name": "HB-BJYZD2SC-ADC-S1",
            "interface": "HundredGigE7/0/36",
            "device_type": "switch",
        },
        "signal_type": "link_flap",
        "severity": "critical",
        "summary": "交换机端口持续抖动",
        "raw_payload": {
            "message": "HundredGigE7/0/36 changed state to down; CRC errors 4281"
        },
    }


class IntegrationLabTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database = str(Path(self.tempdir.name) / "lab.db")
        self.service = IncidentService(IncidentStore(database))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_six_platforms_are_seeded_and_independently_configurable(self):
        platforms = self.service.lab.list_platforms()
        self.assertEqual(len(platforms), 6)
        self.assertTrue(all(item["connection_state"] == "connected" for item in platforms))

        changed = self.service.lab.set_platform_state("bmc_redfish", "disconnected")
        self.assertEqual(changed["connection_state"], "disconnected")
        network = self.service.lab.get_platform("network_nms")
        self.assertEqual(network["connection_state"], "connected")

    def test_event_uses_real_incident_pipeline_and_is_idempotent(self):
        first = self.service.ingest_platform_event(event_payload())
        second = self.service.ingest_platform_event(event_payload())

        self.assertTrue(first["accepted"])
        self.assertFalse(first["duplicate"])
        self.assertEqual(first["event"]["delivery_status"], "accepted")
        self.assertTrue(first["incident"]["investigation"]["simulation"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["incident"]["id"], first["incident"]["id"])
        self.assertEqual(len(self.service.lab.list_events()), 1)

    def test_disconnected_platform_does_not_create_event_or_incident(self):
        self.service.lab.set_platform_state("network_nms", "disconnected")
        with self.assertRaises(PlatformUnavailable):
            self.service.ingest_platform_event(event_payload("NMS-OFFLINE"))
        self.assertEqual(self.service.lab.list_events(), [])
        self.assertEqual(self.service.list_incidents(), [])


if __name__ == "__main__":
    unittest.main()
