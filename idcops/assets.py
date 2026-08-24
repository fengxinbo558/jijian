"""Versioned knowledge and prompt assets backed by the incident SQLite database."""

from __future__ import annotations

import json
import re
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

    def published_knowledge_payload(self) -> Dict[str, Any]:
        with self.store.connect() as connection:
            source_rows = connection.execute(
                "SELECT source_key, content_json FROM knowledge_sources ORDER BY source_key"
            ).fetchall()
            card_rows = connection.execute(
                """
                SELECT kv.content_json FROM knowledge_versions kv
                JOIN knowledge_cards kc
                  ON kc.card_id = kv.card_id AND kc.published_version = kv.version
                WHERE kc.lifecycle_status = 'published'
                ORDER BY kc.card_id
                """
            ).fetchall()
        return {
            "schema_version": "database-v1",
            "sources": {
                str(row["source_key"]): _load(row["content_json"], {}) for row in source_rows
            },
            "cards": [_load(row["content_json"], {}) for row in card_rows],
        }

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

    def get_prompt_version(self, prompt_key: str, version: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM prompt_versions
                WHERE prompt_key = ? AND version = ?
                """,
                (prompt_key, version),
            ).fetchone()
        if row is None:
            return None
        return self._decode_prompt_version(row)

    def get_published_prompt_version(self, prompt_key: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT pv.* FROM prompt_versions pv
                JOIN prompt_definitions pd ON pd.prompt_key = pv.prompt_key
                WHERE pv.prompt_key = ? AND pv.version = pd.published_version
                """,
                (prompt_key,),
            ).fetchone()
        return self._decode_prompt_version(row) if row is not None else None

    @staticmethod
    def _decode_prompt_version(row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "prompt_key": row["prompt_key"],
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

    def create_prompt_version(
        self, prompt_key: str, payload: Mapping[str, Any], actor: str = "local_admin"
    ) -> Dict[str, Any]:
        version = str(payload.get("version") or "").strip()
        system_content = str(payload.get("system_content") or "").strip()
        user_template = str(payload.get("user_template") or "").strip()
        variables = payload.get("variables", [])
        output_schema = payload.get("output_schema", [])
        settings = payload.get("settings", {})
        if not version:
            raise ValueError("提示词版本不能为空")
        if not user_template:
            raise ValueError("用户提示词模板不能为空")
        if not isinstance(variables, list):
            raise ValueError("提示词变量必须是列表")
        if not isinstance(output_schema, (list, dict)):
            raise ValueError("输出结构必须是列表或对象")
        if not isinstance(settings, Mapping):
            raise ValueError("模型参数必须是对象")
        now = utc_now()
        content = {
            "purpose": str(payload.get("purpose") or ""),
            "forbidden": list(payload.get("forbidden", [])),
        }
        with self.store.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM prompt_definitions WHERE prompt_key = ?", (prompt_key,)
            ).fetchone()
            if exists is None:
                raise ValueError("提示词不存在")
            try:
                connection.execute(
                    """
                    INSERT INTO prompt_versions (
                        prompt_key, version, release_status, system_content,
                        user_template, variables_json, output_schema_json,
                        settings_json, content_json, created_by, created_at, published_at
                    ) VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, '')
                    """,
                    (
                        prompt_key,
                        version,
                        system_content,
                        user_template,
                        _dump(variables),
                        _dump(output_schema),
                        _dump(dict(settings)),
                        _dump(content),
                        actor,
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE prompt_definitions SET updated_at = ? WHERE prompt_key = ?",
                    (now, prompt_key),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError("这个提示词版本已经存在") from exc
                raise
        created = self.get_prompt_version(prompt_key, version)
        assert created is not None
        return created

    def preview_prompt(
        self, prompt_key: str, version: str, values: Mapping[str, Any]
    ) -> Dict[str, Any]:
        prompt = self.get_prompt_version(prompt_key, version)
        if prompt is None:
            raise ValueError("提示词版本不存在")
        rendered = str(prompt["user_template"])
        for name in prompt["variables"]:
            key = str(name)
            value = values.get(key, "")
            replacement = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            rendered = re.sub(r"{{\s*" + re.escape(key) + r"\s*}}", replacement, rendered)
        return {
            "prompt_key": prompt_key,
            "version": version,
            "messages": [
                {"role": "system", "content": prompt["system_content"]},
                {"role": "user", "content": rendered},
            ],
            "output_schema": prompt["output_schema"],
            "settings": prompt["settings"],
        }

    def create_knowledge_version(
        self, card_id: str, payload: Mapping[str, Any], actor: str = "local_admin"
    ) -> Dict[str, Any]:
        content = dict(payload.get("content") or payload)
        version = str(content.get("version") or payload.get("version") or "").strip()
        if not version:
            raise ValueError("知识版本不能为空")
        content["id"] = card_id
        content["version"] = version
        now = utc_now()
        with self.store.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM knowledge_cards WHERE card_id = ?", (card_id,)
            ).fetchone()
            if exists is None:
                required = {"domain", "title"}
                if not required.issubset(content):
                    raise ValueError("新知识必须包含领域和标题")
                connection.execute(
                    """
                    INSERT INTO knowledge_cards (
                        card_id, domain, title, lifecycle_status, published_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'draft', '', ?, ?)
                    """,
                    (card_id, str(content["domain"]), str(content["title"]), now, now),
                )
            try:
                connection.execute(
                    """
                    INSERT INTO knowledge_versions (
                        card_id, version, release_status, content_json, created_by,
                        created_at, published_at
                    ) VALUES (?, ?, 'draft', ?, ?, ?, '')
                    """,
                    (card_id, version, _dump(content), actor, now),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError("这个知识版本已经存在") from exc
                raise
        created = self.get_knowledge(card_id)
        assert created is not None
        return created["versions"][0]
