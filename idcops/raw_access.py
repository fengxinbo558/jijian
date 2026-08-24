"""Audited break-glass access to raw incident and AI investigation records."""

from __future__ import annotations

from typing import Any, Dict

from .models import utc_now
from .store import IncidentStore, _dump, _load


class RawAccessService:
    def __init__(self, store: IncidentStore, incident_service: Any) -> None:
        self.store = store
        self.incident_service = incident_service

    def open(
        self,
        record_type: str,
        record_id: str,
        reason: str,
        actor: str,
        role: str,
        confirmed: bool,
    ) -> Dict[str, Any]:
        if not confirmed:
            raise ValueError("必须确认本次原始数据访问")
        clean_reason = str(reason or "").strip()
        if len(clean_reason) < 5:
            raise ValueError("请填写不少于5个字的访问原因")
        if record_type == "incident":
            item = self.incident_service.get_incident(record_id)
        elif record_type == "agent_run":
            item = self.incident_service.agent_traces.get(record_id)
        elif record_type == "integration_event":
            item = self.incident_service.lab.get_event(record_id)
        else:
            raise ValueError("不支持的原始记录类型")
        if item is None:
            raise ValueError("原始记录不存在")
        now = utc_now()
        with self.store.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO raw_access_audit (
                    actor, role, record_type, record_id, reason, fields_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (actor, role, record_type, record_id, clean_reason, _dump({"scope": "full_record"}), now),
            )
            audit_id = int(cursor.lastrowid)
        return {
            "audit_id": audit_id,
            "record_type": record_type,
            "record_id": record_id,
            "reason": clean_reason,
            "accessed_at": now,
            "raw": item,
        }

    def list_audit(self, limit: int = 100) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM raw_access_audit ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["fields"] = _load(item.pop("fields_json", ""), {})
            result.append(item)
        return result
