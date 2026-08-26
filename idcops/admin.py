"""Read-only database browser and append-only annotations for administrators."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .assets import AssetRegistry
from .models import utc_now
from .store import IncidentStore, _dump, _load


class AdminService:
    def __init__(self, store: IncidentStore, assets: AssetRegistry) -> None:
        self.store = store
        self.assets = assets

    def summary(self) -> Dict[str, Any]:
        value = self.assets.summary()
        with self.store.connect() as connection:
            value.update(
                {
                    "audit_records": int(
                        connection.execute("SELECT COUNT(*) AS count FROM audit_log").fetchone()[
                            "count"
                        ]
                    ),
                    "facility_profiles": int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM facility_profiles"
                        ).fetchone()["count"]
                    ),
                    "rag_runs": int(
                        connection.execute("SELECT COUNT(*) AS count FROM rag_runs").fetchone()[
                            "count"
                        ]
                    ),
                    "constraint_profiles": int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM constraint_profiles"
                        ).fetchone()["count"]
                    ),
                    "retrieval_test_runs": int(
                        connection.execute(
                            "SELECT COUNT(*) AS count FROM retrieval_test_runs"
                        ).fetchone()["count"]
                    ),
                }
            )
        return value

    def list_records(self, record_type: str, query: str = "", limit: int = 100) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        query = str(query or "").strip()
        if record_type == "knowledge":
            items = self.assets.list_knowledge()
        elif record_type == "prompts":
            items = self.assets.list_prompts()
        else:
            items = self._database_records(record_type, query, safe_limit)
        if query and record_type in {"knowledge", "prompts"}:
            lowered = query.lower()
            items = [item for item in items if lowered in str(item).lower()]
        return {"record_type": record_type, "total": len(items), "items": items[:safe_limit]}

    def list_activity(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return one readable timeline across AI asset and audit tables."""

        safe_limit = max(1, min(int(limit), 500))
        items: List[Dict[str, Any]] = []
        with self.store.connect() as connection:
            for row in connection.execute(
                "SELECT id, incident_id, action, details_json, created_at "
                "FROM audit_log ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall():
                items.append(
                    {
                        "id": f"AUD-{row['id']}",
                        "activity_type": "system_audit",
                        "action": row["action"],
                        "actor": "system_or_role_action",
                        "asset": row["incident_id"] or "system",
                        "status": "recorded",
                        "details": _load(row["details_json"], {}),
                        "created_at": row["created_at"],
                    }
                )
            for row in connection.execute(
                "SELECT * FROM retrieval_test_runs ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall():
                result = _load(row["result_json"], {})
                items.append(
                    {
                        "id": row["id"],
                        "activity_type": "retrieval_test",
                        "action": "运行检索测试",
                        "actor": row["actor"],
                        "asset": f"knowledge@{row['knowledge_version']}",
                        "status": result.get("coverage", "recorded"),
                        "details": {
                            "query": _load(row["query_json"], {}),
                            "hit_count": len(result.get("hits", [])),
                            "constraint_version": row["constraint_version"],
                            "production_incident_created": False,
                        },
                        "created_at": row["created_at"],
                    }
                )
            for row in connection.execute(
                "SELECT * FROM release_runs ORDER BY updated_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall():
                items.append(
                    {
                        "id": row["id"],
                        "activity_type": "release",
                        "action": "版本测试、发布或回滚",
                        "actor": row["approved_by"] or row["requested_by"],
                        "asset": f"{row['asset_type']}:{row['asset_key']}@{row['version']}",
                        "status": row["status"],
                        "details": {
                            "requested_by": row["requested_by"],
                            "approved_by": row["approved_by"],
                            "environment": row["environment"],
                            "diff": _load(row["diff_json"], {}),
                        },
                        "created_at": row["updated_at"],
                    }
                )
            version_queries = (
                ("prompt_draft", "prompt_key", "prompt_versions"),
                ("knowledge_draft", "card_id", "knowledge_versions"),
                ("constraint_draft", "policy_key", "constraint_versions"),
            )
            for activity_type, key_column, table in version_queries:
                rows = connection.execute(
                    f"SELECT {key_column} AS asset_key, version, release_status, "
                    f"created_by, created_at FROM {table} "
                    "WHERE created_by != 'system_seed' ORDER BY created_at DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
                for row in rows:
                    items.append(
                        {
                            "id": f"{activity_type}:{row['asset_key']}@{row['version']}",
                            "activity_type": activity_type,
                            "action": "创建资产版本",
                            "actor": row["created_by"],
                            "asset": f"{row['asset_key']}@{row['version']}",
                            "status": row["release_status"],
                            "details": {},
                            "created_at": row["created_at"],
                        }
                    )
        items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return items[:safe_limit]

    def _database_records(self, record_type: str, query: str, limit: int) -> List[Dict[str, Any]]:
        specifications = {
            "incidents": (
                "SELECT id, title, status, severity, category, site, summary, created_at, updated_at "
                "FROM incidents WHERE (? = '' OR id LIKE ? OR title LIKE ? OR summary LIKE ? OR site LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                4,
            ),
            "event_inputs": (
                "SELECT id, incident_id, source, event_time, payload_json, created_at "
                "FROM event_inputs WHERE (? = '' OR incident_id LIKE ? OR payload_json LIKE ?) "
                "ORDER BY id DESC LIMIT ?",
                2,
            ),
            "audit_log": (
                "SELECT id, incident_id, action, details_json, created_at "
                "FROM audit_log WHERE (? = '' OR incident_id LIKE ? OR action LIKE ? OR details_json LIKE ?) "
                "ORDER BY id DESC LIMIT ?",
                3,
            ),
            "facilities": (
                "SELECT * FROM facility_profiles WHERE (? = '' OR site LIKE ? OR display_name LIKE ?) "
                "ORDER BY site LIMIT ?",
                2,
            ),
        }
        if record_type not in specifications:
            raise ValueError("不支持的数据类型")
        statement, copies = specifications[record_type]
        like = f"%{query}%"
        params: List[Any] = [query] + [like] * copies + [limit]
        with self.store.connect() as connection:
            rows = connection.execute(statement, params).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            if "payload_json" in item:
                item["payload"] = _load(item.pop("payload_json"), {})
            if "details_json" in item:
                item["details"] = _load(item.pop("details_json"), {})
            items.append(item)
        return items

    def add_annotation(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        record_type = str(payload.get("record_type") or "").strip()
        record_id = str(payload.get("record_id") or "").strip()
        note = str(payload.get("note") or "").strip()
        if not record_type or not record_id or not note:
            raise ValueError("记录类型、记录ID和备注不能为空")
        now = utc_now()
        with self.store.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO record_annotations (
                    record_type, record_id, note, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (record_type, record_id, note, actor, now),
            )
        return {
            "id": int(cursor.lastrowid),
            "record_type": record_type,
            "record_id": record_id,
            "note": note,
            "created_by": actor,
            "created_at": now,
        }
