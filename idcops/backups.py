"""Consistent SQLite online backups with checksum and restore verification."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional

from .models import utc_now
from .store import IncidentStore, _dump, _load


class BackupService:
    def __init__(self, store: IncidentStore, directory: Optional[str] = None) -> None:
        self.store = store
        configured = directory or os.getenv("IDCAI_BACKUP_DIR", "").strip()
        self.directory = (
            Path(configured).expanduser().resolve()
            if configured
            else Path(store.path).parent / "backups"
        )
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(self, actor: str) -> Dict[str, Any]:
        backup_id = "BKP-" + uuid.uuid4().hex[:12].upper()
        timestamp = utc_now().replace(":", "").replace("+", "_")
        target = self.directory / f"idc-ai-ops-{timestamp}-{backup_id}.sqlite3"
        started = utc_now()
        with self.store.connect() as source:
            with closing(sqlite3.connect(str(target))) as destination:
                source.backup(destination)
        size = target.stat().st_size
        checksum = self._checksum(target)
        verification = self.verify_file(target)
        status = "verified" if verification["ok"] else "failed"
        completed = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO backup_runs (
                    id, backup_type, status, path, size_bytes, checksum,
                    summary_json, requested_by, created_at, completed_at
                ) VALUES (?, 'sqlite_online', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    backup_id,
                    status,
                    str(target),
                    size,
                    checksum,
                    _dump(verification),
                    actor,
                    started,
                    completed,
                ),
            )
        result = self.get(backup_id)
        assert result is not None
        return result

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def verify_file(path: Path) -> Dict[str, Any]:
        try:
            with closing(sqlite3.connect(str(path))) as connection:
                quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                tables = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()
                ]
                incident_count = int(connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0])
                event_count = int(connection.execute("SELECT COUNT(*) FROM integration_events").fetchone()[0])
            required = {"incidents", "integration_events", "agent_runs", "knowledge_cards"}
            return {
                "ok": quick_check == "ok" and required.issubset(set(tables)),
                "quick_check": quick_check,
                "required_tables_present": sorted(required.intersection(tables)),
                "incident_count": incident_count,
                "integration_event_count": event_count,
            }
        except (OSError, sqlite3.Error) as exc:
            return {"ok": False, "error": str(exc)}

    def get(self, backup_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM backup_runs WHERE id = ?", (backup_id,)
            ).fetchone()
        return self._decode(row) if row is not None else None

    def list(self, limit: int = 100) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM backup_runs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:
        value = dict(row)
        value["summary"] = _load(value.pop("summary_json", ""), {})
        return value
