import unittest

from idcops.platform_contracts import normalize_platform_event


class PlatformContractTests(unittest.TestCase):
    def test_identity_fields_keep_platform_provenance(self):
        value = normalize_platform_event(
            {
                "source_system": "bmc_redfish",
                "source_event_id": "BMC-001",
                "site": "BJYZ",
                "entity": {"sn": "FULL-SN-001", "rack_position": "RACK-A-01"},
                "signal_type": "power_supply_lost",
                "severity": "warning",
                "summary": "电源1掉电",
                "raw_payload": {"message": "PSU1 input lost"},
            }
        )
        self.assertEqual(value["entity"]["sn"], "FULL-SN-001")
        self.assertEqual(value["field_provenance"]["sn"]["source"], "platform_provided")
        self.assertEqual(value["field_provenance"]["name"]["source"], "unknown")
        self.assertEqual(value["ingest_source"], "monitor")

    def test_unknown_platform_and_missing_event_id_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_platform_event(
                {
                    "source_system": "imaginary",
                    "source_event_id": "X",
                    "site": "BJYZ",
                    "signal_type": "x",
                    "summary": "x",
                }
            )
        with self.assertRaises(ValueError):
            normalize_platform_event(
                {
                    "source_system": "network_nms",
                    "site": "BJYZ",
                    "signal_type": "x",
                    "summary": "x",
                }
            )


if __name__ == "__main__":
    unittest.main()
