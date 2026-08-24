import tempfile
import unittest
from pathlib import Path

from idcops.providers import ProviderRegistry
from idcops.store import IncidentStore


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(str(Path(self.tempdir.name) / "providers.db"))
        self.providers = ProviderRegistry(self.store)
        self.providers.ensure_seeded()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_seeded_adapters_are_visible_but_not_claimed_connected(self):
        items = self.providers.list()
        self.assertGreaterEqual(len(items), 3)
        self.assertTrue(any(item["provider_type"] == "local" for item in items))
        self.assertTrue(any(item["provider_type"] == "private_partner" for item in items))
        self.assertTrue(all(item["connection_state"] in {"not_configured", "configured_not_tested"} for item in items))

    def test_secret_is_never_returned_or_stored_in_public_config(self):
        updated = self.providers.upsert(
            "partner-a",
            {
                "display_name": "合作厂商 A",
                "provider_type": "private_partner",
                "enabled": True,
                "endpoint": "http://model-gateway.internal/v1",
                "model": "ops-model",
                "api_key": "SHOULD-NOT-BE-STORED",
                "secret_configured": True,
                "data_residency": "customer_datacenter",
            },
        )
        self.assertNotIn("api_key", updated["config"])
        self.assertNotIn("SHOULD-NOT-BE-STORED", str(updated))
        self.assertTrue(updated["secret_configured"])
        self.assertEqual(updated["connection_state"], "configured_not_tested")


if __name__ == "__main__":
    unittest.main()
