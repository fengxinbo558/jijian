import tempfile
import unittest
from pathlib import Path

from idcops.constraints import ConstraintRegistry
from idcops.store import IncidentStore


class ConstraintRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(str(Path(self.tempdir.name) / "constraints.db"))
        self.registry = ConstraintRegistry(self.store)
        self.registry.ensure_seeded()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_default_policy_is_published_and_hard_guards_are_read_only(self):
        policy = self.registry.get("investigation-policy")
        self.assertEqual(policy["published_version"], "1.0.0")
        self.assertEqual(policy["published"]["settings"]["retrieval_top_k"], 8)
        self.assertTrue(policy["hard_guards"])
        self.assertTrue(all(item["editable"] is False for item in policy["hard_guards"]))
        self.assertNotIn("disable_hard_guards", policy["published"]["settings"])

    def test_draft_does_not_change_published_policy(self):
        draft = self.registry.create_version(
            "investigation-policy",
            {
                "version": "1.1.0",
                "settings": {
                    "retrieval_top_k": 3,
                    "vector_assist_enabled": False,
                    "vector_only_min_similarity": 0.3,
                    "evidence_excerpt_limit": 5,
                    "no_evidence_mode": "insufficient",
                    "allowed_domains": ["storage", "network"],
                },
            },
            "ai-admin-test",
        )
        self.assertEqual(draft["release_status"], "draft")
        self.assertEqual(self.registry.published_settings()["retrieval_top_k"], 8)

    def test_invalid_adjustable_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Top-K"):
            self.registry.create_version(
                "investigation-policy",
                {"version": "bad", "settings": {"retrieval_top_k": 30}},
                "ai-admin-test",
            )

    def test_schema_initialization_is_idempotent(self):
        self.store.initialize()
        self.registry.ensure_seeded()
        with self.store.connect() as connection:
            profiles = connection.execute(
                "SELECT COUNT(*) AS count FROM constraint_profiles"
            ).fetchone()["count"]
            versions = connection.execute(
                "SELECT COUNT(*) AS count FROM constraint_versions"
            ).fetchone()["count"]
        self.assertEqual(profiles, 1)
        self.assertEqual(versions, 1)


if __name__ == "__main__":
    unittest.main()
