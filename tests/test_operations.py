import tempfile
import unittest
from pathlib import Path

from idcops.operations import OperationService
from idcops.store import IncidentStore


class OperationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(str(Path(self.tempdir.name) / "operations.db"))
        self.operations = OperationService(self.store)

    def tearDown(self):
        self.tempdir.cleanup()

    def create_case(self, suffix="001", **overrides):
        payload = {
            "order_no": f"OMS-{suffix}",
            "site": "BJYZ",
            "target_sn": f"FULL-SERVER-SN-{suffix}",
            "rack_position": "BJYZD2SC-A-08-10",
            "device_name": "bjyz-server-001",
            "operation_type": "replace_disk",
            "urgency": "normal",
            "from_reinstall": "no",
            "power_policy": "needs_confirmation",
        }
        payload.update(overrides)
        return self.operations.import_work_order(payload, "interface-a")

    def test_full_sn_identity_permission_and_review_are_independent_gates(self):
        case = self.create_case()
        self.assertEqual(case["status"], "awaiting_identity")

        mismatch = self.operations.verify_identity(
            case["id"], {"observed_sn": "SN-001", "method": "manual"}, "onsite-a"
        )
        self.assertEqual(mismatch["identity_status"], "mismatch")
        self.assertEqual(mismatch["status"], "blocked_identity")

        matched = self.operations.verify_identity(
            case["id"], {"observed_sn": "FULL-SERVER-SN-001", "method": "barcode"}, "onsite-a"
        )
        self.assertEqual(matched["identity_status"], "confirmed")
        self.assertEqual(matched["permission_status"], "needs_confirmation")
        self.assertEqual(matched["status"], "awaiting_permission")

        allowed = self.operations.set_permission(case["id"], "allowed", "sim-a", "业务已迁移")
        self.assertEqual(allowed["status"], "awaiting_review")
        self.assertNotEqual(allowed["identity_status"], allowed["permission_status"])

        with self.assertRaises(ValueError):
            self.operations.review(
                case["id"], {"decision": "approved", "review_mode": "onsite_peer"}, "onsite-a"
            )

        ready = self.operations.review(
            case["id"], {"decision": "approved", "review_mode": "onsite_peer"}, "onsite-b"
        )
        self.assertEqual(ready["status"], "ready")

    def test_snapshot_is_immutable_and_failed_completion_is_traceable(self):
        case = self.create_case("002", from_reinstall="yes", power_policy="allowed")
        snapshot_before = dict(case["work_order"])
        self.operations.verify_identity(
            case["id"], {"observed_sn": "FULL-SERVER-SN-002", "method": "barcode"}, "onsite-a"
        )
        self.operations.set_permission(case["id"], "allowed", "sim-a", "重装工单已确认")
        self.operations.review(
            case["id"], {"decision": "approved", "review_mode": "remote_authorized"}, "lead-night"
        )
        operating = self.operations.start(case["id"], "onsite-a")
        self.assertEqual(operating["status"], "operating")

        failed = self.operations.complete(
            case["id"],
            {
                "result": "failed",
                "reason": "mainboard_failure",
                "details": "更换工单指定硬盘后仍无法启动，转主板故障调查",
                "offline_sn": "OLD-DISK-SN-002",
                "online_sn": "NEW-DISK-SN-002",
                "timeout_reason": "等待接口人确认失败接单",
            },
            "onsite-a",
        )
        self.assertEqual(failed["status"], "completed_failed")
        self.assertEqual(failed["result_status"], "failed")
        self.assertEqual(failed["work_order"], snapshot_before)
        self.assertIn("主板故障", failed["result_details"])
        self.assertGreaterEqual(len(failed["history"]), 5)

    def test_operation_cannot_start_when_any_gate_is_missing(self):
        case = self.create_case("003", power_policy="allowed")
        with self.assertRaises(ValueError):
            self.operations.start(case["id"], "onsite-a")
        self.operations.verify_identity(
            case["id"], {"observed_sn": "FULL-SERVER-SN-003", "method": "ocr"}, "onsite-a"
        )
        with self.assertRaises(ValueError):
            self.operations.start(case["id"], "onsite-a")


if __name__ == "__main__":
    unittest.main()
