import sqlite3
import tempfile
import unittest
from pathlib import Path

from idcops.assets import AssetRegistry
from idcops.store import IncidentStore


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "assets.db")
        self.store = IncidentStore(self.database)
        self.assets = AssetRegistry(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_schema_and_seed_migration_are_idempotent(self):
        first = self.assets.ensure_seeded()
        second = self.assets.ensure_seeded()

        self.assertEqual(first["knowledge_cards"], 48)
        self.assertEqual(first["knowledge_sources"], 12)
        self.assertEqual(first["prompt_definitions"], 4)
        self.assertEqual(second, first)

        with self.store.connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertTrue(
                {
                    "schema_migrations",
                    "knowledge_cards",
                    "knowledge_versions",
                    "prompt_definitions",
                    "prompt_versions",
                    "release_runs",
                    "rag_runs",
                    "rag_steps",
                    "rag_hits",
                }.issubset(tables)
            )

            knowledge_versions = connection.execute(
                "SELECT COUNT(*) AS count FROM knowledge_versions"
            ).fetchone()["count"]
            prompt_versions = connection.execute(
                "SELECT COUNT(*) AS count FROM prompt_versions"
            ).fetchone()["count"]
        self.assertEqual(knowledge_versions, 48)
        self.assertEqual(prompt_versions, 4)

    def test_seeded_assets_are_visible_with_published_versions(self):
        self.assets.ensure_seeded()

        summary = self.assets.summary()
        self.assertEqual(summary["knowledge"]["published"], 48)
        self.assertEqual(summary["prompts"]["published"], 4)

        card = self.assets.get_knowledge("STORAGE-IO-001")
        self.assertIsNotNone(card)
        self.assertEqual(card["published_version"], "1.0.0")
        self.assertTrue(card["versions"])
        self.assertEqual(card["versions"][0]["release_status"], "published")

        prompt = self.assets.get_prompt("hypothesis")
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["published_version"], "hypothesis-v1.0.0")
        self.assertEqual(prompt["versions"][0]["system_content"], "只输出一个JSON对象。")

    def test_existing_incident_table_survives_asset_migration(self):
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    id, title, status, severity, category, site, summary,
                    correlation_key, identity_keys, devices_json, evidence_json,
                    analysis_json, investigation_json, onsite_card_json,
                    cc_reminder_json, communication_text, created_at, updated_at
                ) VALUES (
                    'INC-OLD', 'old', 'new', 'warning', 'system', 'BJYZ', 'old',
                    'old', '|OLD|', '[]', '[]', '{}', '{}', '{}', '{}', '',
                    '2026-08-24T00:00:00+00:00', '2026-08-24T00:00:00+00:00'
                )
                """
            )

        self.assets.ensure_seeded()
        incident = self.store.get_incident("INC-OLD")
        self.assertIsNotNone(incident)
        self.assertEqual(incident["id"], "INC-OLD")


if __name__ == "__main__":
    unittest.main()
