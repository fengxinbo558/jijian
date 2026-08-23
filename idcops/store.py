"""SQLite persistence for incidents, evidence inputs, and audit events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .models import NormalizedInput, utc_now


JSON_COLUMNS = {
    "devices_json": "devices",
    "evidence_json": "evidence",
    "analysis_json": "analysis",
    "onsite_card_json": "onsite_card",
    "cc_reminder_json": "cc_reminder",
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


class IncidentStore:
    """Small repository that opens a fresh connection per operation."""

    def __init__(self, path: str) -> None:
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    site TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    correlation_key TEXT NOT NULL,
                    identity_keys TEXT NOT NULL,
                    devices_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    onsite_card_json TEXT NOT NULL,
                    cc_reminder_json TEXT NOT NULL,
                    communication_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_status_updated
                    ON incidents(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_incidents_correlation
                    ON incidents(correlation_key, status);

                CREATE TABLE IF NOT EXISTS event_inputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for raw_name, public_name in JSON_COLUMNS.items():
            fallback: Any = [] if public_name in {"devices", "evidence"} else {}
            result[public_name] = _load(result.pop(raw_name), fallback)
        result["affected_count"] = len(result["devices"])
        return result

    def list_incidents(self, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM incidents
                ORDER BY CASE status
                    WHEN 'new' THEN 0
                    WHEN 'processing' THEN 1
                    ELSE 2
                END, updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if row is None:
                return None
            inputs = connection.execute(
                """
                SELECT id, source, event_time, payload_json, created_at
                FROM event_inputs WHERE incident_id = ? ORDER BY id
                """,
                (incident_id,),
            ).fetchall()
            audits = connection.execute(
                """
                SELECT action, details_json, created_at
                FROM audit_log WHERE incident_id = ? ORDER BY id
                """,
                (incident_id,),
            ).fetchall()
        result = self._decode(row)
        result["inputs"] = [
            {
                "id": item["id"],
                "source": item["source"],
                "event_time": item["event_time"],
                "payload": _load(item["payload_json"], {}),
                "created_at": item["created_at"],
            }
            for item in inputs
        ]
        result["audit_log"] = [
            {
                "action": item["action"],
                "details": _load(item["details_json"], {}),
                "created_at": item["created_at"],
            }
            for item in audits
        ]
        return result

    def find_merge_candidate(
        self, event: NormalizedInput, category: str, correlation_key: str
    ) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            if event.labels.get("incident_key"):
                row = connection.execute(
                    """
                    SELECT * FROM incidents
                    WHERE correlation_key = ? AND status != 'resolved'
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (correlation_key,),
                ).fetchone()
            elif event.device.identity_key():
                identity = f"|{event.device.identity_key()}|"
                row = connection.execute(
                    """
                    SELECT * FROM incidents
                    WHERE site = ? AND category = ? AND status != 'resolved'
                      AND identity_keys LIKE ?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (event.site, category, f"%{identity}%"),
                ).fetchone()
            else:
                row = None
        return self._decode(row) if row is not None else None

    def create_incident(self, incident: Mapping[str, Any], event: NormalizedInput) -> Dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    id, title, status, severity, category, site, summary,
                    correlation_key, identity_keys, devices_json, evidence_json,
                    analysis_json, onsite_card_json, cc_reminder_json,
                    communication_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident["id"],
                    incident["title"],
                    incident["status"],
                    incident["severity"],
                    incident["category"],
                    incident["site"],
                    incident["summary"],
                    incident["correlation_key"],
                    incident["identity_keys"],
                    _dump(incident["devices"]),
                    _dump(incident["evidence"]),
                    _dump(incident["analysis"]),
                    _dump(incident["onsite_card"]),
                    _dump(incident["cc_reminder"]),
                    incident["communication_text"],
                    incident["created_at"],
                    incident["updated_at"],
                ),
            )
            self._insert_input(connection, incident["id"], event)
            self._audit(connection, incident["id"], "incident_created", {"source": event.source})
        result = self.get_incident(str(incident["id"]))
        assert result is not None
        return result

    def merge_incident(
        self,
        incident_id: str,
        update: Mapping[str, Any],
        event: NormalizedInput,
    ) -> Dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE incidents SET
                    title = ?, severity = ?, summary = ?, identity_keys = ?,
                    devices_json = ?, evidence_json = ?, analysis_json = ?,
                    onsite_card_json = ?, cc_reminder_json = ?, communication_text = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    update["title"],
                    update["severity"],
                    update["summary"],
                    update["identity_keys"],
                    _dump(update["devices"]),
                    _dump(update["evidence"]),
                    _dump(update["analysis"]),
                    _dump(update["onsite_card"]),
                    _dump(update["cc_reminder"]),
                    update["communication_text"],
                    update["updated_at"],
                    incident_id,
                ),
            )
            self._insert_input(connection, incident_id, event)
            self._audit(connection, incident_id, "evidence_merged", {"source": event.source})
        result = self.get_incident(incident_id)
        assert result is not None
        return result

    def update_status(self, incident_id: str, status: str) -> Optional[Dict[str, Any]]:
        if status not in {"new", "processing", "resolved"}:
            raise ValueError("status must be new, processing, or resolved")
        now = utc_now()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT status FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if current is None:
                return None
            connection.execute(
                "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, incident_id),
            )
            self._audit(
                connection,
                incident_id,
                "status_changed",
                {"from": current["status"], "to": status},
            )
        return self.get_incident(incident_id)

    def _insert_input(
        self, connection: sqlite3.Connection, incident_id: str, event: NormalizedInput
    ) -> None:
        connection.execute(
            """
            INSERT INTO event_inputs (incident_id, source, event_time, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (incident_id, event.source, event.event_time, _dump(event.to_dict()), utc_now()),
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        incident_id: str,
        action: str,
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log (incident_id, action, details_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (incident_id, action, _dump(details), utc_now()),
        )

