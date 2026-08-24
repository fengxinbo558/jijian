"""Multiplatform simulation lab backed by the same incident ingest boundary."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Mapping, Optional

from .correlation import correlation_result, identity_candidates
from .models import utc_now
from .platform_contracts import PLATFORM_DEFINITIONS, normalize_platform_event
from .store import IncidentStore, _dump, _load


CONNECTION_STATES = {
    "connected",
    "disconnected",
    "degraded",
    "delayed",
    "permission_denied",
    "invalid_payload",
}


class PlatformUnavailable(ValueError):
    """Raised when a simulated platform is intentionally unavailable."""


class IntegrationLab:
    def __init__(self, store: IncidentStore) -> None:
        self.store = store
        self.ensure_seeded()
        self.seed_default_topology()

    def ensure_seeded(self) -> None:
        now = utc_now()
        with self.store.connect() as connection:
            for item in PLATFORM_DEFINITIONS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO integration_platforms (
                        platform_key, display_name, platform_type, connection_state,
                        latency_ms, config_json, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, 'connected', 0, ?, '', ?, ?)
                    """,
                    (
                        item["platform_key"],
                        item["display_name"],
                        item["platform_type"],
                        _dump({"description": item["description"], "simulation": True}),
                        now,
                        now,
                    ),
                )

    def seed_default_topology(self) -> Dict[str, int]:
        now = utc_now()
        entities = (
            (
                "ENT-SWITCH-ADC-S1",
                "switch",
                "name:HB-BJYZD2SC-ADC-S1",
                {"name": "HB-BJYZD2SC-ADC-S1", "site": "BJYZ"},
            ),
            (
                "ENT-PORT-ADC-S1-7036",
                "interface",
                "interface:HB-BJYZD2SC-ADC-S1|HundredGigE7/0/36",
                {
                    "name": "HB-BJYZD2SC-ADC-S1",
                    "interface": "HundredGigE7/0/36",
                    "site": "BJYZ",
                },
            ),
            (
                "ENT-SERVER-001",
                "server",
                "sn:SERVER-SN-20260824-001",
                {
                    "sn": "SERVER-SN-20260824-001",
                    "name": "bjyz-app-001",
                    "rack_position": "BJYZD2SC-A-08-10",
                    "site": "BJYZ",
                },
            ),
            (
                "ENT-SERVICE-ORDER",
                "application",
                "asset:service:order-api",
                {"asset_id": "service:order-api", "name": "order-api", "site": "BJYZ"},
            ),
            (
                "ENT-ZONE-D2SC-A",
                "facility_zone",
                "asset:zone:BJYZ-D2SC-A",
                {"asset_id": "zone:BJYZ-D2SC-A", "site": "BJYZ"},
            ),
        )
        links = (
            ("ENT-SWITCH-ADC-S1", "ENT-PORT-ADC-S1-7036", "owns"),
            ("ENT-PORT-ADC-S1-7036", "ENT-SERVER-001", "connects_to"),
            ("ENT-SERVER-001", "ENT-SERVICE-ORDER", "hosts"),
            ("ENT-ZONE-D2SC-A", "ENT-SERVER-001", "contains"),
        )
        with self.store.connect() as connection:
            for entity_id, entity_type, canonical_key, attributes in entities:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO topology_entities (
                        entity_id, entity_type, canonical_key, attributes_json,
                        source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'simulation_seed', ?, ?)
                    """,
                    (entity_id, entity_type, canonical_key, _dump(attributes), now, now),
                )
            for from_id, to_id, link_type in links:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO topology_links (
                        from_entity_id, to_entity_id, link_type, attributes_json,
                        source, created_at, updated_at
                    ) VALUES (?, ?, ?, '{}', 'simulation_seed', ?, ?)
                    """,
                    (from_id, to_id, link_type, now, now),
                )
            entity_count = connection.execute(
                "SELECT COUNT(*) AS count FROM topology_entities"
            ).fetchone()["count"]
            link_count = connection.execute(
                "SELECT COUNT(*) AS count FROM topology_links"
            ).fetchone()["count"]
        return {"entities": int(entity_count), "links": int(link_count)}

    def topology(self) -> Dict[str, Any]:
        with self.store.connect() as connection:
            entity_rows = connection.execute(
                "SELECT * FROM topology_entities ORDER BY entity_type, entity_id"
            ).fetchall()
            link_rows = connection.execute(
                "SELECT * FROM topology_links ORDER BY id"
            ).fetchall()
        entities = []
        for row in entity_rows:
            value = dict(row)
            value["attributes"] = _load(value.pop("attributes_json"), {})
            entities.append(value)
        links = []
        for row in link_rows:
            value = dict(row)
            value["attributes"] = _load(value.pop("attributes_json"), {})
            links.append(value)
        return {"entities": entities, "links": links}

    def _matched_topology_entities(self, entity: Mapping[str, Any]) -> list:
        candidates = set(identity_candidates(entity))
        if not candidates:
            return []
        topology = self.topology()
        matched = []
        for item in topology["entities"]:
            aliases = {item["canonical_key"]}
            aliases.update(identity_candidates(item.get("attributes", {})))
            if candidates.intersection(aliases):
                matched.append(item["entity_id"])
        return sorted(set(matched))

    def _topology_component(self, matched: list) -> list:
        if not matched:
            return []
        topology = self.topology()
        adjacency: Dict[str, set] = {}
        for link in topology["links"]:
            left = str(link["from_entity_id"])
            right = str(link["to_entity_id"])
            adjacency.setdefault(left, set()).add(right)
            adjacency.setdefault(right, set()).add(left)
        visited = set(matched)
        pending = list(matched)
        while pending:
            current = pending.pop()
            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append(neighbor)
        return sorted(visited)

    def list_platforms(self) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT p.*,
                    COUNT(e.id) AS event_count,
                    MAX(e.received_at) AS last_event_at
                FROM integration_platforms p
                LEFT JOIN integration_events e ON e.platform_key = p.platform_key
                GROUP BY p.platform_key
                ORDER BY CASE p.platform_type
                    WHEN 'facility' THEN 0 WHEN 'network' THEN 1
                    WHEN 'hardware' THEN 2 WHEN 'system' THEN 3
                    WHEN 'asset' THEN 4 ELSE 5 END
                """
            ).fetchall()
        return [self._decode_platform(row) for row in rows]

    def get_platform(self, platform_key: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM integration_platforms WHERE platform_key = ?",
                (platform_key,),
            ).fetchone()
        return self._decode_platform(row) if row is not None else None

    @staticmethod
    def _decode_platform(row: Any) -> Dict[str, Any]:
        value = dict(row)
        value["config"] = _load(value.pop("config_json", ""), {})
        value["event_count"] = int(value.get("event_count") or 0)
        return value

    def set_platform_state(
        self, platform_key: str, state: str, latency_ms: int = 0, last_error: str = ""
    ) -> Dict[str, Any]:
        if state not in CONNECTION_STATES:
            raise ValueError("不支持的平台连接状态")
        latency = max(0, min(int(latency_ms), 2000))
        if self.get_platform(platform_key) is None:
            raise ValueError("模拟平台不存在")
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE integration_platforms SET connection_state = ?, latency_ms = ?,
                    last_error = ?, updated_at = ? WHERE platform_key = ?
                """,
                (state, latency, str(last_error or ""), utc_now(), platform_key),
            )
        result = self.get_platform(platform_key)
        assert result is not None
        return result

    def prepare_event(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        normalized = normalize_platform_event(payload)
        platform = self.get_platform(normalized["platform_key"])
        if platform is None:
            raise ValueError("模拟平台不存在")
        state = platform["connection_state"]
        if state in {"disconnected", "permission_denied"}:
            raise PlatformUnavailable(
                "平台已断开" if state == "disconnected" else "平台查询权限不足"
            )
        if state == "invalid_payload":
            raise ValueError("平台当前模拟返回无效结构")
        if state == "delayed" and platform["latency_ms"]:
            time.sleep(platform["latency_ms"] / 1000)

        existing = self.find_event(
            normalized["platform_key"], normalized["source_event_id"]
        )
        if existing is not None:
            return {"duplicate": True, "event": existing, "normalized": normalized}

        matched = self._matched_topology_entities(normalized["entity"])
        component = self._topology_component(matched)
        correlation = correlation_result(normalized, matched, component)
        derived_key = str(correlation.get("incident_key") or "")
        ingest_key = derived_key or (
            f"LAB-ISOLATED-{normalized['platform_key']}-{normalized['source_event_id']}"
        )
        normalized["ingest_payload"]["incident_key"] = ingest_key

        event_id = "IPE-" + uuid.uuid4().hex[:12].upper()
        received_at = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO integration_events (
                    id, platform_key, source_event_id, occurred_at, received_at,
                    site, explicit_incident_key, derived_incident_key, entity_json,
                    signal_type, severity, summary, raw_payload_json, normalized_json,
                    field_provenance_json, delivery_status, incident_id,
                    correlation_json, simulation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'pending', '', ?, 1, ?)
                """,
                (
                    event_id,
                    normalized["platform_key"],
                    normalized["source_event_id"],
                    normalized["occurred_at"],
                    received_at,
                    normalized["site"],
                    normalized["explicit_incident_key"],
                    derived_key,
                    _dump(normalized["entity"]),
                    normalized["signal_type"],
                    normalized["severity"],
                    normalized["summary"],
                    _dump(normalized["raw_payload"]),
                    _dump(normalized["ingest_payload"]),
                    _dump(normalized["field_provenance"]),
                    _dump(correlation),
                    received_at,
                ),
            )
        result = self.find_event(normalized["platform_key"], normalized["source_event_id"])
        assert result is not None
        return {
            "duplicate": False,
            "event": result,
            "normalized": normalized,
            "correlation": correlation,
        }

    def complete_event(
        self,
        event_id: str,
        incident_id: str,
        derived_incident_key: str,
        correlation: Mapping[str, Any],
    ) -> Dict[str, Any]:
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE integration_events SET delivery_status = 'accepted',
                    incident_id = ?, derived_incident_key = ?, correlation_json = ?
                WHERE id = ?
                """,
                (incident_id, derived_incident_key, _dump(dict(correlation)), event_id),
            )
        result = self.get_event(event_id)
        assert result is not None
        return result

    def fail_event(self, event_id: str, message: str) -> None:
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE integration_events SET delivery_status = 'failed',
                    correlation_json = ? WHERE id = ?
                """,
                (_dump({"error": str(message)}), event_id),
            )

    def find_event(self, platform_key: str, source_event_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM integration_events
                WHERE platform_key = ? AND source_event_id = ?
                """,
                (platform_key, source_event_id),
            ).fetchone()
        return self._decode_event(row) if row is not None else None

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM integration_events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._decode_event(row) if row is not None else None

    def list_events(self, limit: int = 200) -> list:
        safe_limit = max(1, min(int(limit), 500))
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM integration_events ORDER BY received_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._decode_event(row) for row in rows]

    def query_platform(
        self,
        platform_key: str,
        incident_id: str = "",
        entity_key: str = "",
        limit: int = 50,
    ) -> Dict[str, Any]:
        platform = self.get_platform(platform_key)
        if platform is None:
            raise ValueError("模拟平台不存在")
        state = platform["connection_state"]
        if state == "disconnected":
            return {"state": "disconnected", "message": "平台已断开", "records": []}
        if state == "permission_denied":
            return {"state": "permission_denied", "message": "平台只读查询无权限", "records": []}
        if state == "invalid_payload":
            return {"state": "invalid_payload", "message": "平台返回结构无效", "records": []}
        if state == "delayed" and platform["latency_ms"]:
            time.sleep(platform["latency_ms"] / 1000)
        safe_limit = max(1, min(int(limit), 100))
        clauses = ["platform_key = ?"]
        parameters: list = [platform_key]
        if incident_id:
            clauses.append("incident_id = ?")
            parameters.append(incident_id)
        with self.store.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM integration_events WHERE {' AND '.join(clauses)} "
                "ORDER BY occurred_at DESC LIMIT ?",
                (*parameters, safe_limit),
            ).fetchall()
        records = [self._decode_event(row) for row in rows]
        if entity_key:
            records = [
                item
                for item in records
                if entity_key in identity_candidates(item.get("entity", {}))
                or entity_key in item.get("entity", {}).values()
            ]
        return {
            "state": "degraded" if state == "degraded" else "completed",
            "message": f"查询到 {len(records)} 条只读记录",
            "records": records,
            "platform": platform_key,
        }

    @staticmethod
    def _decode_event(row: Any) -> Dict[str, Any]:
        value = dict(row)
        for raw, public, fallback in (
            ("entity_json", "entity", {}),
            ("raw_payload_json", "raw_payload", {}),
            ("normalized_json", "normalized", {}),
            ("field_provenance_json", "field_provenance", {}),
            ("correlation_json", "correlation", {}),
        ):
            value[public] = _load(value.pop(raw, ""), fallback)
        value["simulation"] = bool(value["simulation"])
        return value
