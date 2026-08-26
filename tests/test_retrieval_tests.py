import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class RetrievalTestServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(str(Path(self.tempdir.name) / "retrieval.db"))
        self.service = IncidentService(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_real_retriever_returns_ranked_hits_without_creating_incident(self):
        before = len(self.service.list_incidents())
        result = self.service.retrieval_tests.run(
            {
                "text": "kernel: blk_update_request: I/O error, dev sdd",
                "fact_types": ["io_error"],
                "domain": "storage",
                "device_type": "server",
            },
            "ai-admin-test",
        )
        self.assertEqual(result["coverage"], "matched")
        self.assertEqual(result["hits"][0]["card_id"], "STORAGE-IO-001")
        self.assertTrue(result["hits"][0]["reasons"])
        self.assertEqual(len(self.service.list_incidents()), before)
        self.assertEqual(len(self.service.retrieval_tests.list()), 1)

    def test_index_status_is_honest_about_local_vector_capability(self):
        status = self.service.retrieval_tests.index_status()
        self.assertEqual(status["published_cards"], 48)
        self.assertEqual(status["vector_capability"], "local_feature_vector")
        self.assertEqual(status["index_mode"], "runtime_rebuildable")
        self.assertFalse(status["pretrained_semantic_model"])


if __name__ == "__main__":
    unittest.main()
