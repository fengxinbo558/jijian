import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = IncidentService(
            IncidentStore(str(Path(self.tempdir.name) / "primary.db"))
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_online_backup_contains_lab_events_and_passes_restore_check(self):
        scenario = self.service.run_lab_scenario("network-module-cascade")
        backup = self.service.backups.create("test-admin")
        self.assertEqual(backup["status"], "verified")
        self.assertTrue(Path(backup["path"]).exists())
        self.assertEqual(len(backup["checksum"]), 64)
        self.assertTrue(backup["summary"]["ok"])
        self.assertEqual(
            backup["summary"]["integration_event_count"],
            len(scenario["deliveries"]),
        )


if __name__ == "__main__":
    unittest.main()
