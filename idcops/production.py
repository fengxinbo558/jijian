"""Deterministic production alert governance before AI incident investigation."""

from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Mapping, Optional

from .models import utc_now
from .store import IncidentStore, _dump, _load


ACTIVE_ALERT_STATES = {"firing", "acknowledged", "suppressed", "silenced"}
ALERT_STATES = ACTIVE_ALERT_STATES | {"recovered", "expired"}
PROTECTED_IDENTITY_FIELDS = {
    "sn",
    "rack_position",
    "device_name",
    "bmc_ip",
    "network_port",
}
AUTHORITY_RANKS = {
    "oms_cmdb": 100,
    "onsite_scan": 95,
    "bmc_redfish": 90,
    "network_nms": 80,
    "dcim": 75,
    "linux_agent": 65,
    "application": 55,
    "manual": 40,
}


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: Any) -> datetime:
    text = _text(value)
    if not text:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"无效时间：{text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entity(payload: Mapping[str, Any]) -> Dict[str, Any]:
    value = payload.get("entity") if isinstance(payload.get("entity"), Mapping) else {}
    return {
        "sn": _text(value.get("sn") or payload.get("sn")),
        "device_name": _text(
            value.get("device_name")
            or value.get("name")
            or payload.get("device_name")
            or payload.get("hostname")
        ),
        "rack_position": _text(value.get("rack_position") or payload.get("rack_position")),
        "ip": _text(value.get("ip") or payload.get("ip")),
        "interface": _text(value.get("interface") or payload.get("interface")),
        "asset_id": _text(value.get("asset_id") or payload.get("asset_id")),
        "device_type": _text(value.get("device_type") or payload.get("device_type") or "unknown"),
    }


def entity_key(value: Mapping[str, Any]) -> str:
    explicit = _text(value.get("entity_key"))
    if explicit:
        return explicit
    entity = _entity(value)
    if entity["sn"]:
        return f"sn:{entity['sn']}"
    if entity["device_name"]:
        return f"name:{entity['device_name']}"
    if entity["asset_id"]:
        return f"asset:{entity['asset_id']}"
    if entity["ip"]:
        return f"ip:{entity['ip']}"
    if entity["rack_position"]:
        return f"rack:{entity['rack_position']}"
    raise ValueError("告警必须提供明确的 SN、设备名、资产ID、IP或机架位，禁止由AI猜测设备")


class ProductionGovernance:
    """Stateful, auditable governance layer that never delegates identity to an LLM."""

    def __init__(
        self,
        store: IncidentStore,
        incident_ingestor: Callable[[str, Mapping[str, Any]], Dict[str, Any]],
    ) -> None:
        self.store = store
        self.incident_ingestor = incident_ingestor
        self._ingest_lock = threading.RLock()

    @staticmethod
    def _fingerprint(payload: Mapping[str, Any], resolved_entity_key: str) -> str:
        parts = [
            _text(payload.get("source_system") or "unknown"),
            _text(payload.get("site")).upper(),
            resolved_entity_key,
            _text(payload.get("signal_type") or "unknown").lower(),
        ]
        drill_run_id = _text(payload.get("drill_run_id"))
        if drill_run_id and bool(payload.get("is_demo") or payload.get("simulation")):
            parts.append(drill_run_id)
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _decode_alert(row: Any) -> Dict[str, Any]:
        value = dict(row)
        value["entity"] = _load(value.pop("entity_json", ""), {})
        value["data_quality"] = _load(value.pop("data_quality_json", ""), {})
        value["payload"] = _load(value.pop("payload_json", ""), {})
        value["occurrence_count"] = int(value.get("occurrence_count") or 0)
        value["requires_service_validation"] = bool(value.get("requires_service_validation"))
        return value

    def get_alert(self, alert_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_alerts WHERE id = ?", (alert_id,)
            ).fetchone()
        return self._decode_alert(row) if row is not None else None

    def list_alerts(self, lifecycle_status: str = "", limit: int = 200) -> list:
        safe_limit = max(1, min(int(limit), 1000))
        parameters: list = []
        where = ""
        if lifecycle_status:
            where = "WHERE lifecycle_status = ?"
            parameters.append(lifecycle_status)
        parameters.append(safe_limit)
        with self.store.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM managed_alerts {where} ORDER BY last_seen_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._decode_alert(row) for row in rows]

    def _active_by_fingerprint(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        placeholders = ",".join("?" for _item in ACTIVE_ALERT_STATES)
        with self.store.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM managed_alerts WHERE fingerprint = ? "
                f"AND lifecycle_status IN ({placeholders}) ORDER BY last_seen_at DESC LIMIT 1",
                (fingerprint, *sorted(ACTIVE_ALERT_STATES)),
            ).fetchone()
        return self._decode_alert(row) if row is not None else None

    def _data_quality(
        self, payload: Mapping[str, Any], resolved_entity_key: str
    ) -> Dict[str, Any]:
        source = _text(payload.get("source_system") or "unknown")
        health = self.get_source_health(source)
        timestamp_valid = True
        try:
            _parse_time(payload.get("occurred_at") or payload.get("event_time"))
        except ValueError:
            timestamp_valid = False
        identity_conflicts = self.list_identity_conflicts(
            "open", entity_key_filter=resolved_entity_key
        )
        pipeline_problem = bool(health and health.get("pipeline_problem"))
        score = 100
        if not timestamp_valid:
            score -= 25
        if pipeline_problem:
            score -= 25
        if identity_conflicts:
            score -= 35
        return {
            "score": max(score, 0),
            "identity_key": resolved_entity_key,
            "timestamp_valid": timestamp_valid,
            "source_health": health.get("connection_status", "unknown") if health else "unknown",
            "pipeline_problem": pipeline_problem,
            "identity_conflict_count": len(identity_conflicts),
            "operation_blocked": any(item["operation_blocked"] for item in identity_conflicts),
        }

    def _active_maintenance(
        self, site: str, resolved_entity_key: str, source_system: str
    ) -> Optional[Dict[str, Any]]:
        now = utc_now()
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM maintenance_windows
                WHERE enabled = 1 AND starts_at <= ? AND ends_at >= ?
                  AND (site = '' OR site = ?)
                  AND (entity_key = '' OR entity_key = ?)
                  AND (source_system = '' OR source_system = ?)
                ORDER BY starts_at DESC LIMIT 1
                """,
                (now, now, site, resolved_entity_key, source_system),
            ).fetchone()
        return self._decode_maintenance(row) if row is not None else None

    def _active_upstream(self, upstream_entity_key: str) -> Optional[Dict[str, Any]]:
        if not upstream_entity_key:
            return None
        placeholders = ",".join("?" for _item in ACTIVE_ALERT_STATES)
        with self.store.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM managed_alerts WHERE entity_key = ? "
                f"AND lifecycle_status IN ({placeholders}) "
                "AND severity IN ('critical', 'warning') ORDER BY last_seen_at DESC LIMIT 1",
                (upstream_entity_key, *sorted(ACTIVE_ALERT_STATES)),
            ).fetchone()
        return self._decode_alert(row) if row is not None else None

    def ingest_alert(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        # The local MVP is a single service process. Serialize the fingerprint
        # check and incident creation so concurrent webhook retries cannot create
        # orphan duplicate incidents before SQLite's unique index rejects one.
        with self._ingest_lock:
            return self._ingest_alert(payload)

    def _ingest_alert(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        source_system = _text(payload.get("source_system") or "unknown")
        source_event_id = _text(payload.get("source_event_id") or _id("SOURCE"))
        site = _text(payload.get("site")).upper()
        signal_type = _text(payload.get("signal_type") or "unknown").lower()
        severity = _text(payload.get("severity") or "unknown").lower()
        if severity not in {"info", "warning", "critical", "unknown"}:
            severity = "unknown"
        lifecycle_status = _text(
            payload.get("lifecycle_status") or payload.get("status") or "firing"
        ).lower()
        if lifecycle_status == "resolved":
            lifecycle_status = "recovered"
        if lifecycle_status not in ALERT_STATES:
            raise ValueError("告警状态必须是 firing、acknowledged、recovered、suppressed、silenced 或 expired")
        summary = _text(payload.get("summary") or payload.get("message"))
        if not summary:
            raise ValueError("告警摘要不能为空")
        resolved_entity = entity_key(payload)
        fingerprint = self._fingerprint(payload, resolved_entity)
        now = utc_now()
        self.update_source_health(
            {
                "source_system": source_system,
                "connection_status": "connected",
                "last_received_at": now,
                "increment_received": 1,
            }
        )
        current = self._active_by_fingerprint(fingerprint)
        dedup_window_seconds = max(
            30, min(int(payload.get("dedup_window_seconds") or 1800), 86400)
        )
        if current is not None:
            age_seconds = (
                _parse_time(now) - _parse_time(current["last_seen_at"])
            ).total_seconds()
            if age_seconds > dedup_window_seconds:
                with self.store.connect() as connection:
                    connection.execute(
                        """
                        UPDATE managed_alerts SET lifecycle_status = 'expired',
                            suppression_reason = ?, updated_at = ? WHERE id = ?
                        """,
                        ("超过去重窗口且未继续上报", now, current["id"]),
                    )
                current = None
        if lifecycle_status == "recovered":
            if current is None:
                return self._create_recovered_observation(
                    payload, fingerprint, resolved_entity, source_event_id
                )
            with self.store.connect() as connection:
                connection.execute(
                    """
                    UPDATE managed_alerts SET lifecycle_status = 'recovered',
                        source_event_id = ?, last_seen_at = ?, recovered_at = ?,
                        requires_service_validation = 1, payload_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (source_event_id, now, now, _dump(dict(payload)), now, current["id"]),
                )
            alert = self.get_alert(current["id"])
            assert alert is not None
            return {
                "accepted": True,
                "duplicate": False,
                "incident_created": False,
                "requires_service_validation": True,
                "decision": "recovered_waiting_service_validation",
                "alert": alert,
                "incident": None,
            }

        maintenance = self._active_maintenance(site, resolved_entity, source_system)
        upstream = self._active_upstream(_text(payload.get("upstream_entity_key")))
        if current is not None:
            next_severity = self._stronger_severity(current["severity"], severity)
            next_status = current["lifecycle_status"]
            next_reason = current["suppression_reason"]
            next_parent = current["parent_alert_id"]
            next_incident_id = current["incident_id"]
            incident: Optional[Dict[str, Any]] = None
            decision = "deduplicated"
            if maintenance:
                next_status = "silenced"
                next_reason = f"维护窗口：{maintenance['reason']}"
                next_parent = ""
                decision = "deduplicated_and_silenced_by_maintenance"
            elif upstream:
                next_status = "suppressed"
                next_reason = f"上游活动告警：{upstream['summary']}"
                next_parent = upstream["id"]
                decision = "deduplicated_and_suppressed_by_upstream"
            elif (
                (current["lifecycle_status"] == "silenced" and current["suppression_reason"].startswith("维护窗口："))
                or (current["lifecycle_status"] == "suppressed" and current["parent_alert_id"])
            ):
                inherited_parent_incident = False
                if current["parent_alert_id"]:
                    parent = self.get_alert(current["parent_alert_id"])
                    inherited_parent_incident = bool(
                        parent and parent["incident_id"] == current["incident_id"]
                    )
                if not next_incident_id or inherited_parent_incident:
                    incident = self.incident_ingestor(
                        "monitor", self._incident_payload(payload, resolved_entity)
                    )
                    next_incident_id = _text(incident.get("id"))
                next_status = "firing"
                next_reason = ""
                next_parent = ""
                decision = "reopened_after_suppression_or_maintenance"
            with self.store.connect() as connection:
                connection.execute(
                    """
                    UPDATE managed_alerts SET source_event_id = ?, severity = ?,
                        last_seen_at = ?, occurrence_count = occurrence_count + 1,
                        lifecycle_status = ?, suppression_reason = ?,
                        parent_alert_id = ?, incident_id = ?, payload_json = ?,
                        updated_at = ? WHERE id = ?
                    """,
                    (
                        source_event_id, next_severity, now, next_status,
                        next_reason, next_parent, next_incident_id,
                        _dump(dict(payload)), now, current["id"],
                    ),
                )
            alert = self.get_alert(current["id"])
            assert alert is not None
            return {
                "accepted": True,
                "duplicate": True,
                "incident_created": incident is not None,
                "requires_service_validation": False,
                "decision": decision,
                "alert": alert,
                "incident": incident,
            }

        decision = "create_incident"
        status = "firing"
        reason = ""
        parent_alert_id = ""
        incident: Optional[Dict[str, Any]] = None
        incident_id = ""
        if maintenance:
            status = "silenced"
            decision = "silenced_by_maintenance"
            reason = f"维护窗口：{maintenance['reason']}"
        elif upstream:
            status = "suppressed"
            decision = "suppressed_by_upstream"
            reason = f"上游活动告警：{upstream['summary']}"
            parent_alert_id = upstream["id"]
            incident_id = upstream["incident_id"]
        elif lifecycle_status in {"suppressed", "silenced", "expired"}:
            status = lifecycle_status
            decision = f"source_marked_{lifecycle_status}"
            reason = _text(payload.get("suppression_reason"))
        else:
            incident = self.incident_ingestor(
                "monitor", self._incident_payload(payload, resolved_entity)
            )
            incident_id = _text(incident.get("id"))

        alert_id = _id("ALT")
        quality = self._data_quality(payload, resolved_entity)
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO managed_alerts (
                    id, fingerprint, source_system, source_event_id, signal_type,
                    site, entity_key, entity_json, severity, summary,
                    lifecycle_status, incident_id, first_seen_at, last_seen_at,
                    recovered_at, occurrence_count, suppression_reason,
                    parent_alert_id, requires_service_validation, data_quality_json,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 1,
                          ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    alert_id,
                    fingerprint,
                    source_system,
                    source_event_id,
                    signal_type,
                    site,
                    resolved_entity,
                    _dump(_entity(payload)),
                    severity,
                    summary,
                    status,
                    incident_id,
                    _text(payload.get("occurred_at") or payload.get("event_time") or now),
                    now,
                    reason,
                    parent_alert_id,
                    _dump(quality),
                    _dump(dict(payload)),
                    now,
                    now,
                ),
            )
        alert = self.get_alert(alert_id)
        assert alert is not None
        return {
            "accepted": True,
            "duplicate": False,
            "incident_created": incident is not None,
            "requires_service_validation": False,
            "decision": decision,
            "alert": alert,
            "incident": incident,
        }

    def _create_recovered_observation(
        self,
        payload: Mapping[str, Any],
        fingerprint: str,
        resolved_entity: str,
        source_event_id: str,
    ) -> Dict[str, Any]:
        now = utc_now()
        alert_id = _id("ALT")
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO managed_alerts (
                    id, fingerprint, source_system, source_event_id, signal_type,
                    site, entity_key, entity_json, severity, summary,
                    lifecycle_status, incident_id, first_seen_at, last_seen_at,
                    recovered_at, occurrence_count, suppression_reason,
                    parent_alert_id, requires_service_validation, data_quality_json,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'recovered', '', ?, ?, ?,
                          1, '未找到本系统内的活动告警', '', 0, ?, ?, ?, ?)
                """,
                (
                    alert_id,
                    fingerprint,
                    _text(payload.get("source_system") or "unknown"),
                    source_event_id,
                    _text(payload.get("signal_type") or "unknown"),
                    _text(payload.get("site")).upper(),
                    resolved_entity,
                    _dump(_entity(payload)),
                    _text(payload.get("severity") or "unknown"),
                    _text(payload.get("summary") or "收到恢复信号"),
                    now,
                    now,
                    now,
                    _dump(self._data_quality(payload, resolved_entity)),
                    _dump(dict(payload)),
                    now,
                    now,
                ),
            )
        alert = self.get_alert(alert_id)
        assert alert is not None
        return {
            "accepted": True,
            "duplicate": False,
            "incident_created": False,
            "requires_service_validation": False,
            "decision": "recovery_observation_without_active_alert",
            "alert": alert,
            "incident": None,
        }

    @staticmethod
    def _stronger_severity(left: str, right: str) -> str:
        rank = {"unknown": 0, "info": 1, "warning": 2, "critical": 3}
        return left if rank.get(left, 0) >= rank.get(right, 0) else right

    @staticmethod
    def _incident_payload(payload: Mapping[str, Any], resolved_entity: str) -> Dict[str, Any]:
        entity = _entity(payload)
        raw_payload = payload.get("raw_payload")
        raw_text = _dump(raw_payload) if isinstance(raw_payload, Mapping) else _text(raw_payload)
        return {
            "site": _text(payload.get("site")).upper(),
            "severity": _text(payload.get("severity") or "unknown"),
            "sn": entity["sn"],
            "device_name": entity["device_name"],
            "rack_position": entity["rack_position"],
            "ip": entity["ip"],
            "device_type": entity["device_type"],
            "summary": _text(payload.get("summary")),
            "message": raw_text or _text(payload.get("message") or payload.get("summary")),
            "event_time": _text(payload.get("occurred_at") or payload.get("event_time")),
            "source_system": _text(payload.get("source_system")),
            "incident_key": _text(payload.get("incident_key")),
            "is_demo": bool(payload.get("is_demo") or payload.get("simulation")),
            "demo_id": _text(payload.get("demo_id")),
            "labels": {
                "governed_alert": True,
                "entity_key": resolved_entity,
                "signal_type": _text(payload.get("signal_type")),
                "drill_run_id": _text(payload.get("drill_run_id")),
                "platform_simulation": bool(
                    payload.get("is_demo") or payload.get("simulation")
                ),
            },
        }

    def acknowledge_alert(self, alert_id: str, actor: str) -> Dict[str, Any]:
        alert = self.get_alert(alert_id)
        if alert is None:
            raise ValueError("告警不存在")
        if alert["lifecycle_status"] != "firing":
            raise ValueError("只有正在发生的告警可以确认")
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE managed_alerts SET lifecycle_status = 'acknowledged', updated_at = ? WHERE id = ?",
                (utc_now(), alert_id),
            )
        result = self.get_alert(alert_id)
        assert result is not None
        result["acknowledged_by"] = actor
        return result

    @staticmethod
    def _decode_maintenance(row: Any) -> Dict[str, Any]:
        value = dict(row)
        value["enabled"] = bool(value.get("enabled"))
        return value

    def create_maintenance_window(
        self, payload: Mapping[str, Any], actor: str
    ) -> Dict[str, Any]:
        starts_at = _parse_time(payload.get("starts_at"))
        ends_at = _parse_time(payload.get("ends_at"))
        if ends_at <= starts_at:
            raise ValueError("维护结束时间必须晚于开始时间")
        reason = _text(payload.get("reason"))
        if not reason:
            raise ValueError("维护窗口必须填写原因")
        now = utc_now()
        item = {
            "id": _id("MW"),
            "site": _text(payload.get("site")).upper(),
            "entity_key": _text(payload.get("entity_key")),
            "source_system": _text(payload.get("source_system")),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": reason,
            "enabled": True,
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO maintenance_windows (
                    id, site, entity_key, source_system, starts_at, ends_at,
                    reason, enabled, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    item["id"], item["site"], item["entity_key"], item["source_system"],
                    item["starts_at"], item["ends_at"], item["reason"], actor, now, now,
                ),
            )
        return item

    def list_maintenance_windows(self) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM maintenance_windows ORDER BY starts_at DESC"
            ).fetchall()
        return [self._decode_maintenance(row) for row in rows]

    def update_source_health(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        source = _text(payload.get("source_system"))
        if not source:
            raise ValueError("数据源名称不能为空")
        current = self.get_source_health(source) or {}
        expected = int(payload.get("expected_entities", current.get("expected_entities", 0)) or 0)
        reporting = int(payload.get("reporting_entities", current.get("reporting_entities", 0)) or 0)
        coverage = round(reporting / expected * 100, 2) if expected > 0 else 0.0
        received = int(current.get("received_count", 0)) + int(payload.get("increment_received", 0) or 0)
        received = int(payload.get("received_count", received) or 0)
        now = utc_now()
        item = {
            "source_system": source,
            "connection_status": _text(payload.get("connection_status") or current.get("connection_status") or "unknown"),
            "last_received_at": _text(payload.get("last_received_at") or current.get("last_received_at")),
            "received_count": received,
            "rejected_count": int(payload.get("rejected_count", current.get("rejected_count", 0)) or 0),
            "queue_depth": int(payload.get("queue_depth", current.get("queue_depth", 0)) or 0),
            "dropped_count": int(payload.get("dropped_count", current.get("dropped_count", 0)) or 0),
            "retry_count": int(payload.get("retry_count", current.get("retry_count", 0)) or 0),
            "expected_entities": expected,
            "reporting_entities": reporting,
            "coverage_percent": coverage,
            "details": dict(payload.get("details") or {}) if isinstance(payload.get("details"), Mapping) else {},
            "updated_at": now,
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_health (
                    source_system, connection_status, last_received_at,
                    received_count, rejected_count, queue_depth, dropped_count,
                    retry_count, expected_entities, reporting_entities,
                    coverage_percent, details_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_system) DO UPDATE SET
                    connection_status=excluded.connection_status,
                    last_received_at=excluded.last_received_at,
                    received_count=excluded.received_count,
                    rejected_count=excluded.rejected_count,
                    queue_depth=excluded.queue_depth,
                    dropped_count=excluded.dropped_count,
                    retry_count=excluded.retry_count,
                    expected_entities=excluded.expected_entities,
                    reporting_entities=excluded.reporting_entities,
                    coverage_percent=excluded.coverage_percent,
                    details_json=excluded.details_json,
                    updated_at=excluded.updated_at
                """,
                (
                    source, item["connection_status"], item["last_received_at"],
                    item["received_count"], item["rejected_count"], item["queue_depth"],
                    item["dropped_count"], item["retry_count"], expected, reporting,
                    coverage, _dump(item["details"]), now,
                ),
            )
        result = self.get_source_health(source)
        assert result is not None
        return result

    @staticmethod
    def _decode_source_health(row: Any) -> Dict[str, Any]:
        value = dict(row)
        value["details"] = _load(value.pop("details_json", ""), {})
        value["pipeline_problem"] = (
            value["connection_status"] not in {"connected", "healthy"}
            or int(value["dropped_count"]) > 0
            or int(value["queue_depth"]) > 0
            or (
                int(value["expected_entities"]) > 0
                and float(value["coverage_percent"]) < 90
            )
        )
        return value

    def get_source_health(self, source_system: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_health WHERE source_system = ?", (source_system,)
            ).fetchone()
        return self._decode_source_health(row) if row is not None else None

    def list_source_health(self) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_health ORDER BY source_system"
            ).fetchall()
        return [self._decode_source_health(row) for row in rows]

    def record_identity_assertion(
        self, payload: Mapping[str, Any], actor: str
    ) -> Dict[str, Any]:
        resolved_entity = _text(payload.get("entity_key"))
        source = _text(payload.get("source_system"))
        field_name = _text(payload.get("field_name"))
        field_value = _text(payload.get("field_value"))
        if not all((resolved_entity, source, field_name, field_value)):
            raise ValueError("身份断言必须包含实体、来源、字段和值")
        rank = int(payload.get("authority_rank") or AUTHORITY_RANKS.get(source, 30))
        observed = _parse_time(payload.get("observed_at"))
        expires_text = _text(payload.get("expires_at"))
        expires = _parse_time(expires_text) if expires_text else observed + timedelta(days=30)
        now = utc_now()
        assertion_id = _id("IDA")
        with self.store.connect() as connection:
            authoritative = connection.execute(
                """
                SELECT * FROM identity_assertions
                WHERE entity_key = ? AND field_name = ?
                ORDER BY authority_rank DESC, observed_at DESC LIMIT 1
                """,
                (resolved_entity, field_name),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO identity_assertions (
                    id, entity_key, source_system, field_name, field_value,
                    authority_rank, observed_at, expires_at, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assertion_id, resolved_entity, source, field_name, field_value,
                    rank, observed.isoformat(), expires.isoformat(), actor, now,
                ),
            )
            conflict_id = ""
            operation_blocked = False
            if authoritative is not None and str(authoritative["field_value"]) != field_value:
                operation_blocked = field_name in PROTECTED_IDENTITY_FIELDS
                new_is_authoritative = rank > int(authoritative["authority_rank"]) or (
                    rank == int(authoritative["authority_rank"])
                    and observed > _parse_time(authoritative["observed_at"])
                )
                if new_is_authoritative:
                    authoritative_source = source
                    authoritative_value = field_value
                    conflicting_source = str(authoritative["source_system"])
                    conflicting_value = str(authoritative["field_value"])
                else:
                    authoritative_source = str(authoritative["source_system"])
                    authoritative_value = str(authoritative["field_value"])
                    conflicting_source = source
                    conflicting_value = field_value
                existing_conflict = connection.execute(
                    """
                    SELECT id FROM identity_conflicts
                    WHERE entity_key = ? AND field_name = ? AND status = 'open'
                      AND authoritative_source = ? AND authoritative_value = ?
                      AND conflicting_source = ? AND conflicting_value = ?
                    LIMIT 1
                    """,
                    (
                        resolved_entity, field_name, authoritative_source,
                        authoritative_value, conflicting_source, conflicting_value,
                    ),
                ).fetchone()
                if existing_conflict is not None:
                    conflict_id = str(existing_conflict["id"])
                else:
                    conflict_id = _id("IDC")
                    connection.execute(
                        """
                        INSERT INTO identity_conflicts (
                            id, entity_key, field_name, authoritative_source,
                            authoritative_value, conflicting_source, conflicting_value,
                            status, operation_blocked, created_at, resolved_at,
                            resolved_by, resolution
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, '', '', '')
                        """,
                        (
                            conflict_id, resolved_entity, field_name,
                            authoritative_source, authoritative_value,
                            conflicting_source, conflicting_value,
                            int(operation_blocked), now,
                        ),
                    )
        return {
            "id": assertion_id,
            "entity_key": resolved_entity,
            "source_system": source,
            "field_name": field_name,
            "field_value": field_value,
            "authority_rank": rank,
            "observed_at": observed.isoformat(),
            "expires_at": expires.isoformat(),
            "conflict_id": conflict_id,
            "operation_blocked": operation_blocked,
        }

    def list_identity_assertions(self, entity_key_filter: str = "") -> list:
        with self.store.connect() as connection:
            if entity_key_filter:
                rows = connection.execute(
                    "SELECT * FROM identity_assertions WHERE entity_key = ? ORDER BY field_name, authority_rank DESC",
                    (entity_key_filter,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM identity_assertions ORDER BY created_at DESC LIMIT 500"
                ).fetchall()
        now = datetime.now(timezone.utc)
        result = []
        for row in rows:
            value = dict(row)
            value["stale"] = _parse_time(value["expires_at"]) < now
            result.append(value)
        return result

    def list_identity_conflicts(
        self, status: str = "", entity_key_filter: str = ""
    ) -> list:
        clauses = []
        parameters: list = []
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        if entity_key_filter:
            clauses.append("entity_key = ?")
            parameters.append(entity_key_filter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM identity_conflicts {where} ORDER BY created_at DESC",
                parameters,
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["operation_blocked"] = bool(value["operation_blocked"])
            result.append(value)
        return result

    def resolve_identity_conflict(
        self, conflict_id: str, resolution: str, actor: str
    ) -> Dict[str, Any]:
        if not _text(resolution):
            raise ValueError("必须填写冲突处理结论")
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE identity_conflicts SET status = 'resolved', resolved_at = ?,
                    resolved_by = ?, resolution = ? WHERE id = ? AND status = 'open'
                """,
                (now, actor, resolution, conflict_id),
            )
            row = connection.execute(
                "SELECT * FROM identity_conflicts WHERE id = ?", (conflict_id,)
            ).fetchone()
        if row is None:
            raise ValueError("身份冲突不存在")
        value = dict(row)
        value["operation_blocked"] = bool(value["operation_blocked"])
        return value

    def record_change(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        site = _text(payload.get("site")).upper()
        resolved_entity = _text(payload.get("entity_key"))
        change_type = _text(payload.get("change_type"))
        summary = _text(payload.get("summary"))
        if not all((site, resolved_entity, change_type, summary)):
            raise ValueError("变更必须包含机房、实体、类型和摘要")
        item = {
            "id": _id("CHG"),
            "site": site,
            "entity_key": resolved_entity,
            "change_type": change_type,
            "summary": summary,
            "reference_id": _text(payload.get("reference_id")),
            "changed_by": _text(payload.get("changed_by") or actor),
            "changed_at": _parse_time(payload.get("changed_at")).isoformat(),
            "before": dict(payload.get("before") or {}) if isinstance(payload.get("before"), Mapping) else {},
            "after": dict(payload.get("after") or {}) if isinstance(payload.get("after"), Mapping) else {},
            "source_system": _text(payload.get("source_system") or "manual"),
            "causality": "candidate_only",
            "created_at": utc_now(),
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO change_events (
                    id, site, entity_key, change_type, summary, reference_id,
                    changed_by, changed_at, before_json, after_json,
                    source_system, causality, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"], site, resolved_entity, change_type, summary,
                    item["reference_id"], item["changed_by"], item["changed_at"],
                    _dump(item["before"]), _dump(item["after"]), item["source_system"],
                    item["causality"], item["created_at"],
                ),
            )
        return item

    def list_changes(self, site: str = "", entity_key_filter: str = "", limit: int = 200) -> list:
        clauses = []
        parameters: list = []
        if site:
            clauses.append("site = ?")
            parameters.append(site.upper())
        if entity_key_filter:
            clauses.append("entity_key = ?")
            parameters.append(entity_key_filter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        with self.store.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM change_events {where} ORDER BY changed_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["before"] = _load(value.pop("before_json", ""), {})
            value["after"] = _load(value.pop("after_json", ""), {})
            result.append(value)
        return result

    def create_roster(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        item = {
            "id": _id("DUTY"),
            "site": _text(payload.get("site")).upper(),
            "team": _text(payload.get("team")),
            "person": _text(payload.get("person")),
            "shift_start": _parse_time(payload.get("shift_start")).isoformat(),
            "shift_end": _parse_time(payload.get("shift_end")).isoformat(),
            "escalation_person": _text(payload.get("escalation_person")),
            "created_by": actor,
            "created_at": utc_now(),
        }
        if not all((item["site"], item["team"], item["person"])):
            raise ValueError("值班记录必须包含机房、团队和人员")
        if _parse_time(item["shift_end"]) <= _parse_time(item["shift_start"]):
            raise ValueError("班次结束时间必须晚于开始时间")
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO duty_rosters (
                    id, site, team, person, shift_start, shift_end,
                    escalation_person, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(item.values()),
            )
        return item

    def list_rosters(self, active_only: bool = False) -> list:
        now = utc_now()
        with self.store.connect() as connection:
            if active_only:
                rows = connection.execute(
                    "SELECT * FROM duty_rosters WHERE shift_start <= ? AND shift_end >= ? ORDER BY site, team",
                    (now, now),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM duty_rosters ORDER BY shift_start DESC LIMIT 500"
                ).fetchall()
        return [dict(row) for row in rows]

    def assign_incident(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        incident_id = _text(payload.get("incident_id"))
        assignee = _text(payload.get("assignee"))
        team = _text(payload.get("team"))
        if not all((incident_id, assignee, team)):
            raise ValueError("分派必须包含事故、负责人和团队")
        priority = _text(payload.get("priority") or "p3").lower()
        minutes = {"p1": 5, "p2": 15, "p3": 120, "p4": 480}.get(priority, 120)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.replace(microsecond=0).isoformat()
        item = {
            "id": _id("ASN"),
            "incident_id": incident_id,
            "assignee": assignee,
            "team": team,
            "priority": priority,
            "status": "assigned",
            "due_at": (now_dt + timedelta(minutes=minutes)).replace(microsecond=0).isoformat(),
            "acknowledged_at": "",
            "deferred_reason": "",
            "escalated_to": "",
            "assigned_by": actor,
            "created_at": now,
            "updated_at": now,
        }
        with self.store.connect() as connection:
            active_rows = connection.execute(
                """
                SELECT * FROM incident_assignments
                WHERE incident_id = ? AND status IN ('assigned', 'acknowledged', 'deferred', 'escalated')
                ORDER BY updated_at DESC
                """,
                (incident_id,),
            ).fetchall()
            if active_rows:
                latest = dict(active_rows[0])
                if latest["assignee"] == assignee and latest["team"] == team:
                    latest["duplicate_assignment"] = True
                    return latest
                connection.execute(
                    """
                    UPDATE incident_assignments SET status = 'reassigned', updated_at = ?
                    WHERE incident_id = ? AND status IN ('assigned', 'acknowledged', 'deferred', 'escalated')
                    """,
                    (now, incident_id),
                )
            connection.execute(
                """
                INSERT INTO incident_assignments (
                    id, incident_id, assignee, team, priority, status, due_at,
                    acknowledged_at, deferred_reason, escalated_to, assigned_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(item.values()),
            )
        return item

    def _assignment_action(
        self, assignment_id: str, status: str, actor: str, reason: str = "", escalated_to: str = ""
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM incident_assignments WHERE id = ?", (assignment_id,)
            ).fetchone()
            if row is None:
                raise ValueError("事故分派不存在")
            if row["status"] == "reassigned":
                raise ValueError("该分派已经改派，不能再次确认、延后或升级")
            if status == "deferred" and not reason:
                raise ValueError("延后必须填写原因")
            acknowledged_at = now if status == "acknowledged" else row["acknowledged_at"]
            connection.execute(
                """
                UPDATE incident_assignments SET status = ?, acknowledged_at = ?,
                    deferred_reason = ?, escalated_to = ?, updated_at = ? WHERE id = ?
                """,
                (
                    status, acknowledged_at, reason or row["deferred_reason"],
                    escalated_to or row["escalated_to"], now, assignment_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM incident_assignments WHERE id = ?", (assignment_id,)
            ).fetchone()
        result = dict(updated)
        result["acted_by"] = actor
        return result

    def acknowledge_assignment(self, assignment_id: str, actor: str) -> Dict[str, Any]:
        return self._assignment_action(assignment_id, "acknowledged", actor)

    def defer_assignment(self, assignment_id: str, reason: str, actor: str) -> Dict[str, Any]:
        return self._assignment_action(assignment_id, "deferred", actor, reason=reason)

    def escalate_assignment(
        self, assignment_id: str, escalated_to: str, actor: str
    ) -> Dict[str, Any]:
        if not _text(escalated_to):
            raise ValueError("必须指定升级负责人")
        return self._assignment_action(
            assignment_id, "escalated", actor, escalated_to=escalated_to
        )

    def list_assignments(self) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incident_assignments ORDER BY updated_at DESC LIMIT 500"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_feedback(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        action = _text(payload.get("action"))
        if action not in {"merge", "split", "mark_unrelated", "confirm_related"}:
            raise ValueError("纠正动作必须是 merge、split、mark_unrelated 或 confirm_related")
        reason = _text(payload.get("reason"))
        if not reason:
            raise ValueError("人工纠正必须填写原因")
        item = {
            "id": _id("FB"),
            "action": action,
            "alert_id": _text(payload.get("alert_id")),
            "incident_id": _text(payload.get("incident_id")),
            "target_incident_id": _text(payload.get("target_incident_id")),
            "reason": reason,
            "created_by": actor,
            "created_at": utc_now(),
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO correlation_feedback (
                    id, action, alert_id, incident_id, target_incident_id,
                    reason, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(item.values()),
            )
        return item

    def list_feedback(self) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM correlation_feedback ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_metric(
        self, incident_id: str, metric_name: str, value: float, dimensions: Mapping[str, Any]
    ) -> Dict[str, Any]:
        item = {
            "id": _id("MET"),
            "incident_id": _text(incident_id),
            "metric_name": _text(metric_name),
            "metric_value": float(value),
            "dimensions": dict(dimensions),
            "recorded_at": utc_now(),
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO investigation_metrics (
                    id, incident_id, metric_name, metric_value,
                    dimensions_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"], item["incident_id"], item["metric_name"],
                    item["metric_value"], _dump(item["dimensions"]), item["recorded_at"],
                ),
            )
        return item

    def metrics(self) -> Dict[str, Any]:
        with self.store.connect() as connection:
            state_rows = connection.execute(
                "SELECT lifecycle_status, COUNT(*) AS count FROM managed_alerts GROUP BY lifecycle_status"
            ).fetchall()
            feedback_count = connection.execute(
                "SELECT COUNT(*) AS count FROM correlation_feedback"
            ).fetchone()["count"]
            metric_rows = connection.execute(
                "SELECT metric_name, COUNT(*) AS count, AVG(metric_value) AS average FROM investigation_metrics GROUP BY metric_name"
            ).fetchall()
        states = {str(row["lifecycle_status"]): int(row["count"]) for row in state_rows}
        total = sum(states.values())
        suppressed = states.get("suppressed", 0) + states.get("silenced", 0)
        return {
            "alert_states": states,
            "total_alerts": total,
            "noise_reduction_percent": round(suppressed / total * 100, 2) if total else 0.0,
            "feedback_count": int(feedback_count),
            "investigation_metrics": [dict(row) for row in metric_rows],
        }

    def overview(self) -> Dict[str, Any]:
        metrics = self.metrics()
        with self.store.connect() as connection:
            identity_conflicts = connection.execute(
                "SELECT COUNT(*) AS count FROM identity_conflicts WHERE status = 'open'"
            ).fetchone()["count"]
            recovery_validation = connection.execute(
                """
                SELECT COUNT(*) AS count FROM managed_alerts
                WHERE lifecycle_status = 'recovered' AND requires_service_validation = 1
                """
            ).fetchone()["count"]
            unassigned = connection.execute(
                """
                SELECT COUNT(DISTINCT a.incident_id) AS count FROM managed_alerts a
                WHERE a.incident_id != '' AND a.lifecycle_status IN ('firing', 'acknowledged')
                  AND NOT EXISTS (
                    SELECT 1 FROM incident_assignments s
                    WHERE s.incident_id = a.incident_id
                      AND s.status IN ('assigned', 'acknowledged', 'deferred', 'escalated')
                  )
                """
            ).fetchone()["count"]
        states = metrics["alert_states"]
        sources = self.list_source_health()
        return {
            "active_alerts": states.get("firing", 0) + states.get("acknowledged", 0),
            "suppressed_alerts": states.get("suppressed", 0),
            "silenced_alerts": states.get("silenced", 0),
            "recovery_validation": int(recovery_validation),
            "identity_conflicts": int(identity_conflicts),
            "unassigned_incidents": int(unassigned),
            "pipeline_problems": sum(1 for item in sources if item["pipeline_problem"]),
            "feedback_count": metrics["feedback_count"],
            "noise_reduction_percent": metrics["noise_reduction_percent"],
        }
