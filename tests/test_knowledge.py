import unittest

from idcops.knowledge import KnowledgeBase


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        self.knowledge = KnowledgeBase()

    def test_shipped_pack_has_required_domain_coverage(self):
        summary = self.knowledge.summary()
        self.assertEqual(summary["card_count"], 48)
        self.assertGreaterEqual(summary["domains"]["storage"], 8)
        self.assertGreaterEqual(summary["domains"]["compute"], 7)
        self.assertGreaterEqual(summary["domains"]["network"], 8)
        self.assertGreaterEqual(summary["domains"]["facility"], 6)
        self.assertGreaterEqual(summary["domains"]["system"], 6)
        self.assertGreaterEqual(summary["domains"]["application"], 5)

    def test_retrieval_explains_why_each_card_matched(self):
        results = self.knowledge.search(
            rule_names=["disk_io"],
            fact_types=["block_io_error", "smart_failure"],
            text="Buffer I/O error on dev sdb; SMART Failure Predicted",
            device_type="server",
        )
        identifiers = [item["card"]["id"] for item in results]
        self.assertIn("STORAGE-IO-001", identifiers)
        self.assertIn("STORAGE-SMART-002", identifiers)
        self.assertNotIn("STORAGE-CAPACITY-005", identifiers)
        self.assertNotIn("MEMORY-CE-009", identifiers)
        self.assertNotIn("NETWORK-TOR-022", identifiers)
        self.assertTrue(all(item["reasons"] for item in results))

    def test_local_vector_similarity_does_not_override_exact_port_fact(self):
        results = self.knowledge.search(
            rule_names=["application_runtime"],
            fact_types=["service_failed", "port_conflict"],
            text="Failed to start; Address already in use: bind 0.0.0.0:8080",
            device_type="server",
        )
        identifiers = [item["card"]["id"] for item in results]
        self.assertLess(identifiers.index("APP-PORT-036"), identifiers.index("SYSTEM-SERVICE-030"))


if __name__ == "__main__":
    unittest.main()
