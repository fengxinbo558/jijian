"""Versioned knowledge and prompt assets backed by the incident SQLite database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .models import utc_now
from .store import IncidentStore, _dump, _load


class AssetRegistry:
    """Seed and expose the user-visible AI asset store."""

    def __init__(self, store: IncidentStore, root: Optional[Path] = None) -> None:
        self.store = store
        self.root = root or Path(__file__).resolve().parent.parent

    def ensure_seeded(self) -> Dict[str, int]:
        knowledge = json.loads(
            (self.root / "knowledge" / "diagnostic_cards.json").read_text(encoding="utf-8")
        )
        prompts = json.loads(
            (self.root / "prompts" / "contracts.json").read_text(encoding="utf-8")
        )
        now = utc_now()
        with self.store.connect() as connection:
            for key, value in knowledge["sources"].items():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_sources (
                        source_key, content_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (str(key), _dump(value), now, now),
                )
            for card in knowledge["cards"]:
                card_id = str(card["id"])
                version = str(card["version"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_cards (
                        card_id, domain, title, lifecycle_status, published_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'published', ?, ?, ?)
                    """,
                    (card_id, str(card["domain"]), str(card["title"]), version, now, now),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_versions (
                        card_id, version, release_status, content_json, created_by,
                        created_at, published_at
                    ) VALUES (?, ?, 'published', ?, 'system_seed', ?, ?)
                    """,
                    (card_id, version, _dump(card), now, now),
                )
            for prompt_key, contract in prompts["contracts"].items():
                version = str(contract["version"])
                system_content = "只输出一个JSON对象。" if prompt_key == "hypothesis" else ""
                user_template = str(contract.get("instructions") or contract.get("purpose") or "")
                variables = (
                    [
                        "event_summary",
                        "redacted_log_excerpt",
                        "evidence",
                        "facts",
                        "knowledge_cards",
                        "baseline_hypotheses",
                    ]
                    if prompt_key == "hypothesis"
                    else []
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO prompt_definitions (
                        prompt_key, name, purpose, lifecycle_status, published_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'published', ?, ?, ?)
                    """,
                    (
                        prompt_key,
                        self._prompt_name(prompt_key),
                        str(contract.get("purpose") or ""),
                        version,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO prompt_versions (
                        prompt_key, version, release_status, system_content,
                        user_template, variables_json, output_schema_json,
                        settings_json, content_json, created_by, created_at, published_at
                    ) VALUES (?, ?, 'published', ?, ?, ?, ?, ?, ?, 'system_seed', ?, ?)
                    """,
                    (
                        prompt_key,
                        version,
                        system_content,
                        user_template,
                        _dump(variables),
                        _dump(contract.get("output", [])),
                        _dump({"temperature": 0.1}),
                        _dump(contract),
                        now,
                        now,
                    ),
                )
        return self.counts()

    @staticmethod
    def _prompt_name(prompt_key: str) -> str:
        return {
            "parser": "日志事实提取",
            "hypothesis": "故障假设生成",
            "next_step": "下一步验证",
            "communication": "沟通内容整理",
        }.get(prompt_key, prompt_key)

    def counts(self) -> Dict[str, int]:
        with self.store.connect() as connection:
            values = {
                "knowledge_sources": connection.execute(
                    "SELECT COUNT(*) AS count FROM knowledge_sources"
                ).fetchone()["count"],
                "knowledge_cards": connection.execute(
                    "SELECT COUNT(*) AS count FROM knowledge_cards"
                ).fetchone()["count"],
                "prompt_definitions": connection.execute(
                    "SELECT COUNT(*) AS count FROM prompt_definitions"
                ).fetchone()["count"],
            }
        return {key: int(value) for key, value in values.items()}

    def summary(self) -> Dict[str, Any]:
        with self.store.connect() as connection:
            incidents = int(
                connection.execute("SELECT COUNT(*) AS count FROM incidents").fetchone()["count"]
            )
            inputs = int(
                connection.execute("SELECT COUNT(*) AS count FROM event_inputs").fetchone()["count"]
            )
            knowledge_rows = connection.execute(
                "SELECT lifecycle_status, COUNT(*) AS count FROM knowledge_cards GROUP BY lifecycle_status"
            ).fetchall()
            prompt_rows = connection.execute(
                "SELECT lifecycle_status, COUNT(*) AS count FROM prompt_definitions GROUP BY lifecycle_status"
            ).fetchall()
        return {
            "incidents": incidents,
            "event_inputs": inputs,
            "knowledge": {str(row["lifecycle_status"]): int(row["count"]) for row in knowledge_rows},
            "prompts": {str(row["lifecycle_status"]): int(row["count"]) for row in prompt_rows},
        }

    def list_knowledge(self) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT card_id, domain, title, lifecycle_status, published_version,
                       created_at, updated_at
                FROM knowledge_cards ORDER BY domain, card_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_knowledge(self, card_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            card = connection.execute(
                "SELECT * FROM knowledge_cards WHERE card_id = ?", (card_id,)
            ).fetchone()
            if card is None:
                return None
            versions = connection.execute(
                """
                SELECT version, release_status, content_json, created_by,
                       created_at, published_at
                FROM knowledge_versions WHERE card_id = ?
                ORDER BY id DESC
                """,
                (card_id,),
            ).fetchall()
        result = dict(card)
        result["versions"] = [
            {
                **{key: row[key] for key in row.keys() if key != "content_json"},
                "content": _load(row["content_json"], {}),
            }
            for row in versions
        ]
        return result

    def list_prompts(self) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM prompt_definitions ORDER BY prompt_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_prompt(self, prompt_key: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            prompt = connection.execute(
                "SELECT * FROM prompt_definitions WHERE prompt_key = ?", (prompt_key,)
            ).fetchone()
            if prompt is None:
                return None
            versions = connection.execute(
                """
                SELECT version, release_status, system_content, user_template,
                       variables_json, output_schema_json, settings_json, content_json,
                       created_by, created_at, published_at
                FROM prompt_versions WHERE prompt_key = ? ORDER BY id DESC
                """,
                (prompt_key,),
            ).fetchall()
        result = dict(prompt)
        result["versions"] = [
            {
                "version": row["version"],
                "release_status": row["release_status"],
                "system_content": row["system_content"],
                "user_template": row["user_template"],
                "variables": _load(row["variables_json"], []),
                "output_schema": _load(row["output_schema_json"], []),
                "settings": _load(row["settings_json"], {}),
                "content": _load(row["content_json"], {}),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "published_at": row["published_at"],
            }
            for row in versions
        ]
        return result
