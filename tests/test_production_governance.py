import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from idcops.production import ProductionGovernance
from idcops.store import IncidentStore


class ProductionGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(str(Path(self.tempdir.name) / "production.db"))
        self.created_incidents = []

        def ingest(source, payload):
            incident = {
                "id": f"INC-{len(self.created_incidents) + 1:03d}",
                "status": "new",
                "title": payload.get("summary", ""),
            }
            self.created_incidents.append((source, payload, incident))
            return incident

        self.governance = ProductionGovernance(self.store, ingest)

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def network_event(event_id="NMS-001", lifecycle_status="firing", **overrides):
        payload = {
            "source_system": "network_nms",
            "source_event_id": event_id,
            "lifecycle_status": lifecycle_status,
            "site": "BJYZ",
            "occurred_at": "2026-08-25T01:00:00+00:00",
            "entity": {
                "device_name": "HB-BJYZ-TOR-01",
                "interface": "HundredGigE1/0/1",
                "device_type": "switch",
            },
            "signal_type": "link_down",
            "severity": "critical",
            "summary": "TOR 端口链路中断",
            "raw_payload": {"message": "HundredGigE1/0/1 changed state to DOWN"},
        }
        payload.update(overrides)
        return payload

    def test_duplicate_and_recovery_share_one_alert(self):
        first = self.governance.ingest_alert(self.network_event())
        duplicate = self.governance.ingest_alert(
            self.network_event(event_id="NMS-RETRY-001")
        )
        recovered = self.governance.ingest_alert(
            self.network_event(event_id="NMS-RECOVER-001", lifecycle_status="recovered")
        )

        self.assertEqual(first["alert"]["id"], duplicate["alert"]["id"])
        self.assertEqual(duplicate["alert"]["occurrence_count"], 2)
        self.assertEqual(recovered["alert"]["lifecycle_status"], "recovered")
        self.assertEqual(recovered["alert"]["incident_id"], "INC-001")
        self.assertTrue(recovered["requires_service_validation"])
        self.assertEqual(len(self.created_incidents), 1)

    def test_maintenance_and_upstream_fault_suppress_incident_creation(self):
        now = datetime.now(timezone.utc)
        self.governance.create_maintenance_window(
            {
                "site": "BJYZ",
                "entity_key": "name:HB-BJYZ-MAINT-01",
                "starts_at": (now - timedelta(minutes=5)).isoformat(),
                "ends_at": (now + timedelta(minutes=30)).isoformat(),
                "reason": "交换机升级",
            },
            "tester",
        )
        silenced = self.governance.ingest_alert(
            self.network_event(
                event_id="MAINT-001",
                entity={"device_name": "HB-BJYZ-MAINT-01", "device_type": "switch"},
            )
        )
        self.assertEqual(silenced["alert"]["lifecycle_status"], "silenced")
        self.assertFalse(silenced["incident_created"])

        upstream = self.governance.ingest_alert(
            self.network_event(
                event_id="UP-001",
                entity={"device_name": "HB-BJYZ-TOR-UP", "device_type": "switch"},
            )
        )
        downstream = self.governance.ingest_alert(
            self.network_event(
                event_id="DOWN-001",
                entity={"sn": "SERVER-SN-001", "device_type": "server"},
                upstream_entity_key="name:HB-BJYZ-TOR-UP",
            )
        )
        self.assertEqual(downstream["alert"]["lifecycle_status"], "suppressed")
        self.assertEqual(downstream["alert"]["parent_alert_id"], upstream["alert"]["id"])
        self.assertEqual(downstream["alert"]["incident_id"], upstream["alert"]["incident_id"])

    def test_identity_conflict_change_source_health_assignment_and_feedback(self):
        self.governance.record_identity_assertion(
            {
                "entity_key": "sn:SERVER-SN-001",
                "source_system": "oms_cmdb",
                "field_name": "rack_position",
                "field_value": "BJYZ-A-01-01",
                "observed_at": "2026-08-25T01:00:00+00:00",
            },
            "tester",
        )
        conflict = self.governance.record_identity_assertion(
            {
                "entity_key": "sn:SERVER-SN-001",
                "source_system": "onsite_scan",
                "field_name": "rack_position",
                "field_value": "BJYZ-A-01-02",
                "observed_at": "2026-08-25T01:01:00+00:00",
            },
            "tester",
        )
        self.assertTrue(conflict["operation_blocked"])
        self.assertEqual(len(self.governance.list_identity_conflicts("open")), 1)
        repeated_conflict = self.governance.record_identity_assertion(
            {
                "entity_key": "sn:SERVER-SN-001",
                "source_system": "onsite_scan",
                "field_name": "rack_position",
                "field_value": "BJYZ-A-01-02",
            },
            "tester",
        )
        self.assertEqual(repeated_conflict["conflict_id"], conflict["conflict_id"])
        self.assertEqual(len(self.governance.list_identity_conflicts("open")), 1)

        self.governance.record_identity_assertion(
            {
                "entity_key": "sn:SERVER-SN-REVERSE",
                "source_system": "onsite_scan",
                "field_name": "rack_position",
                "field_value": "BJYZ-A-02-09",
            },
            "tester",
        )
        self.governance.record_identity_assertion(
            {
                "entity_key": "sn:SERVER-SN-REVERSE",
                "source_system": "oms_cmdb",
                "field_name": "rack_position",
                "field_value": "BJYZ-A-02-10",
            },
            "tester",
        )
        reverse = self.governance.list_identity_conflicts(
            "open", "sn:SERVER-SN-REVERSE"
        )[0]
        self.assertEqual(reverse["authoritative_source"], "oms_cmdb")
        self.assertEqual(reverse["authoritative_value"], "BJYZ-A-02-10")

        change = self.governance.record_change(
            {
                "site": "BJYZ",
                "entity_key": "sn:SERVER-SN-001",
                "change_type": "firmware",
                "summary": "升级网卡固件",
                "changed_at": "2026-08-25T00:55:00+00:00",
                "before": {"version": "1.0"},
                "after": {"version": "1.1"},
            },
            "tester",
        )
        self.assertEqual(change["causality"], "candidate_only")

        health = self.governance.update_source_health(
            {
                "source_system": "otel_collector",
                "connection_status": "degraded",
                "expected_entities": 100,
                "reporting_entities": 72,
                "queue_depth": 200,
                "dropped_count": 4,
            }
        )
        self.assertEqual(health["coverage_percent"], 72.0)
        self.assertTrue(health["pipeline_problem"])

        alert = self.governance.ingest_alert(self.network_event(event_id="ASSIGN-001"))["alert"]
        assignment = self.governance.assign_incident(
            {
                "incident_id": alert["incident_id"],
                "assignee": "night-operator-a",
                "team": "onsite",
                "priority": "p1",
            },
            "lead-a",
        )
        acknowledged = self.governance.acknowledge_assignment(
            assignment["id"], "night-operator-a"
        )
        self.assertEqual(acknowledged["status"], "acknowledged")
        repeated_assignment = self.governance.assign_incident(
            {
                "incident_id": alert["incident_id"],
                "assignee": "night-operator-a",
                "team": "onsite",
                "priority": "p1",
            },
            "lead-a",
        )
        self.assertEqual(repeated_assignment["id"], assignment["id"])
        self.assertTrue(repeated_assignment["duplicate_assignment"])
        reassigned = self.governance.assign_incident(
            {
                "incident_id": alert["incident_id"],
                "assignee": "day-operator-b",
                "team": "onsite",
                "priority": "p2",
            },
            "lead-a",
        )
        assignments = self.governance.list_assignments()
        self.assertEqual(reassigned["assignee"], "day-operator-b")
        self.assertIn("reassigned", {item["status"] for item in assignments})

        feedback = self.governance.record_feedback(
            {
                "action": "mark_unrelated",
                "alert_id": alert["id"],
                "incident_id": alert["incident_id"],
                "reason": "现场确认是两个独立故障",
            },
            "interface-a",
        )
        self.assertEqual(feedback["action"], "mark_unrelated")
        overview = self.governance.overview()
        self.assertGreaterEqual(overview["identity_conflicts"], 1)
        self.assertGreaterEqual(overview["feedback_count"], 1)

    def test_existing_downstream_can_be_suppressed_then_reopened(self):
        downstream_payload = self.network_event(
            event_id="DOWN-FIRST",
            entity={"sn": "SERVER-SN-009", "device_type": "server"},
            signal_type="host_unreachable",
            summary="服务器失联",
        )
        downstream = self.governance.ingest_alert(downstream_payload)
        upstream = self.governance.ingest_alert(
            self.network_event(
                event_id="UP-LATER",
                entity={"device_name": "HB-BJYZ-TOR-LATER", "device_type": "switch"},
            )
        )
        suppressed = self.governance.ingest_alert(
            {
                **downstream_payload,
                "source_event_id": "DOWN-AGAIN",
                "upstream_entity_key": "name:HB-BJYZ-TOR-LATER",
            }
        )
        self.assertEqual(suppressed["alert"]["lifecycle_status"], "suppressed")
        self.assertEqual(suppressed["alert"]["parent_alert_id"], upstream["alert"]["id"])
        self.assertEqual(suppressed["alert"]["incident_id"], downstream["alert"]["incident_id"])

        self.governance.ingest_alert(
            self.network_event(
                event_id="UP-RECOVERED",
                lifecycle_status="recovered",
                entity={"device_name": "HB-BJYZ-TOR-LATER", "device_type": "switch"},
                summary="TOR链路恢复",
            )
        )
        reopened = self.governance.ingest_alert(
            {**downstream_payload, "source_event_id": "DOWN-STILL-FAILING"}
        )
        self.assertEqual(reopened["alert"]["lifecycle_status"], "firing")
        self.assertEqual(reopened["alert"]["parent_alert_id"], "")
        self.assertEqual(reopened["alert"]["incident_id"], downstream["alert"]["incident_id"])

    def test_concurrent_retries_create_one_alert_and_one_incident(self):
        def send(index):
            return self.governance.ingest_alert(
                self.network_event(event_id=f"CONCURRENT-{index}")
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(send, range(20)))

        self.assertEqual(len({item["alert"]["id"] for item in results}), 1)
        self.assertEqual(self.governance.list_alerts()[0]["occurrence_count"], 20)
        self.assertEqual(len(self.created_incidents), 1)

    def test_stale_active_alert_expires_before_a_new_occurrence(self):
        first = self.governance.ingest_alert(self.network_event(event_id="STALE-1"))
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE managed_alerts SET last_seen_at = ? WHERE id = ?",
                (stale_time, first["alert"]["id"]),
            )
        second = self.governance.ingest_alert(
            self.network_event(event_id="STALE-2", dedup_window_seconds=60)
        )
        self.assertNotEqual(second["alert"]["id"], first["alert"]["id"])
        self.assertEqual(len(self.governance.list_alerts("expired")), 1)
        self.assertEqual(len(self.created_incidents), 2)


if __name__ == "__main__":
    unittest.main()
