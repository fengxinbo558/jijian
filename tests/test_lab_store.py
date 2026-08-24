import tempfile
import unittest
from pathlib import Path

from idcops.store import IncidentStore


class LabStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "lab.db")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_lab_schema_is_idempotent_and_keeps_existing_tables(self):
        first = IncidentStore(self.database)
        second = IncidentStore(self.database)

        with second.connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            migrations = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }

        self.assertEqual(first.path, second.path)
        self.assertTrue(
            {
                "incidents",
                "integration_platforms",
                "integration_events",
                "topology_entities",
                "topology_links",
                "agent_runs",
                "agent_steps",
                "raw_access_audit",
                "backup_runs",
            }.issubset(tables)
        )
        self.assertIn(3, migrations)


if __name__ == "__main__":
    unittest.main()
