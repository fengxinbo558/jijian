import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from idcops.sandbox_validation import SandboxValidationService
from idcops.store import IncidentStore


class SandboxValidationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.production_store = IncidentStore(str(self.root / "production.db"))
        self.sandbox = SandboxValidationService(
            self.production_store,
            sandbox_root=self.root / "sandbox",
            project_root=Path(__file__).resolve().parent.parent,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _production_counts(self):
        with self.production_store.connect() as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                    "count"
                ]
                for table in ("incidents", "event_inputs", "integration_events", "rag_runs")
            }

    def test_suite_has_exact_mix_and_seed_is_reproducible(self):
        first = self.sandbox.preview_suite(seed=20260827)
        second = self.sandbox.preview_suite(seed=20260827)
        other = self.sandbox.preview_suite(seed=20260828)

        self.assertEqual(len(first["cases"]), 120)
        self.assertEqual(first["digest"], second["digest"])
        self.assertNotEqual(first["digest"], other["digest"])
        self.assertEqual(
            Counter(item["case_type"] for item in first["cases"]),
            {
                "public_real_log": 30,
                "single_fault": 35,
                "cross_platform_cascade": 25,
                "missing_or_conflicting": 15,
                "normal_or_false_alarm": 10,
                "safety_responsibility": 5,
            },
        )
        serialized = json.dumps(first["cases"], ensure_ascii=False)
        self.assertNotIn("expected_category", serialized)
        self.assertNotIn("root_cause", serialized)
        self.assertTrue(all(item["site"].startswith("SANDBOX-") for item in first["cases"]))

    def test_run_uses_independent_database_and_does_not_pollute_production(self):
        before = self._production_counts()
        created = self.sandbox.create_run(
            {"seed": 20260827, "tracks": ["baseline", "agent"], "execute": False},
            actor="tester",
        )
        self.assertEqual(created["status"], "created")
        run_database = Path(created["run_database"])
        self.assertTrue(run_database.exists())
        self.assertNotEqual(run_database.resolve(), Path(self.production_store.path).resolve())

        completed = self.sandbox.execute(created["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progress"]["total"], 120)
        self.assertEqual(completed["progress"]["completed"], 120)
        self.assertEqual(completed["tracks"]["agent"]["status"], "not_run")
        self.assertEqual(before, self._production_counts())

        with sqlite3.connect(run_database) as connection:
            incident_count = connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            secret_tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = 'sandbox_case_answers'"
            ).fetchall()
        self.assertGreater(incident_count, 0)
        self.assertEqual(secret_tables, [])

    def test_hidden_answers_are_blocked_until_terminal_and_reveal_retires_suite(self):
        created = self.sandbox.create_run(
            {"seed": 77, "tracks": ["baseline"], "execute": False}, actor="tester"
        )
        with self.assertRaises(PermissionError):
            self.sandbox.reveal(created["id"], actor="root", role="super_admin")
        case = self.sandbox.get_case(created["id"], created["case_ids"][0])
        self.assertNotIn("secret", case)

        self.sandbox.execute(created["id"])
        revealed = self.sandbox.reveal(created["id"], actor="root", role="super_admin")
        self.assertEqual(revealed["suite_status"], "revealed")
        self.assertEqual(len(revealed["answers"]), 120)
        with self.assertRaises(PermissionError):
            self.sandbox.reveal(created["id"], actor="ai", role="ai_admin")

    def test_invalid_sandbox_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sandbox.validate_challenge(
                {
                    "simulation": True,
                    "environment": "sandbox",
                    "sandbox_run_id": "SBX-1",
                    "site": "BJYZ",
                },
                "SBX-1",
            )


if __name__ == "__main__":
    unittest.main()
