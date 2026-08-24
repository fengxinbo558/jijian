"""Two-step tested releases for knowledge and prompt versions."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Mapping, Optional

from .assets import AssetRegistry
from .models import utc_now
from .store import IncidentStore, _dump, _load


class ReleaseManager:
    def __init__(self, store: IncidentStore, assets: AssetRegistry) -> None:
        self.store = store
        self.assets = assets

    def test_asset(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        asset_type = str(payload.get("asset_type") or "").strip()
        asset_key = str(payload.get("asset_key") or "").strip()
        version = str(payload.get("version") or "").strip()
        if asset_type not in {"prompt", "knowledge"}:
            raise ValueError("资产类型必须是 prompt 或 knowledge")
        current = self._current_version(asset_type, asset_key)
        target = self._target(asset_type, asset_key, version)
        if target is None:
            raise ValueError("待测试版本不存在")
        if target.get("release_status") != "draft":
            raise ValueError("只有草稿版本可以开始测试")
        checks = self._checks(asset_type, target)
        passed = all(item["passed"] for item in checks)
        if not passed:
            raise ValueError("草稿未通过发布前检查")
        release_id = "REL-" + uuid.uuid4().hex[:12].upper()
        now = utc_now()
        diff = {"previous_version": current, "target_version": version}
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO release_runs (
                    id, asset_type, asset_key, version, environment, status,
                    test_summary_json, diff_json, requested_by, approved_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'test', 'tested', ?, ?, ?, '', ?, ?)
                """,
                (release_id, asset_type, asset_key, version, _dump(checks), _dump(diff), actor, now, now),
            )
        result = self.get(release_id)
        assert result is not None
        return result

    @staticmethod
    def _checks(asset_type: str, target: Mapping[str, Any]) -> list:
        if asset_type == "prompt":
            return [
                {
                    "name": "结构检查：用户模板非空",
                    "passed": bool(target.get("user_template")),
                    "scope": "structural",
                    "does_not_prove": "不证明模型回答正确或适合生产故障",
                },
                {
                    "name": "结构检查：输出格式可读取",
                    "passed": isinstance(target.get("output_schema"), (list, dict)),
                    "scope": "structural",
                    "does_not_prove": "不证明模型会始终遵守输出格式",
                },
                {
                    "name": "流程检查：版本仍是草稿",
                    "passed": target.get("release_status") == "draft",
                    "scope": "workflow",
                    "does_not_prove": "不证明事实准确率",
                },
            ]
        content = target.get("content", {})
        return [
            {
                "name": "结构检查：知识ID存在",
                "passed": bool(content.get("id")),
                "scope": "structural",
                "does_not_prove": "不证明经验适用于所有设备或环境",
            },
            {
                "name": "结构检查：知识标题非空",
                "passed": bool(content.get("title")),
                "scope": "structural",
                "does_not_prove": "不证明内容事实正确",
            },
            {
                "name": "结构检查：知识领域非空",
                "passed": bool(content.get("domain")),
                "scope": "structural",
                "does_not_prove": "不证明现场步骤安全",
            },
        ]

    def prepare(self, release_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            current = connection.execute(
                "SELECT status FROM release_runs WHERE id = ?", (release_id,)
            ).fetchone()
            if current is None:
                raise ValueError("发布记录不存在")
            if current["status"] != "tested":
                raise ValueError("只有测试通过的版本可以准备上线")
            connection.execute(
                "UPDATE release_runs SET status = 'prepared', environment = 'production', updated_at = ? WHERE id = ?",
                (utc_now(), release_id),
            )
        result = self.get(release_id)
        assert result is not None
        return result

    def publish(self, release_id: str, confirmed_online: bool, actor: str) -> Dict[str, Any]:
        if not confirmed_online:
            raise ValueError("必须明确确认这是线上环境")
        now = utc_now()
        with self.store.connect() as connection:
            release = connection.execute(
                "SELECT * FROM release_runs WHERE id = ?", (release_id,)
            ).fetchone()
            if release is None:
                raise ValueError("发布记录不存在")
            if release["status"] != "prepared":
                raise ValueError("发布记录尚未完成准备上线")
            self._set_published(connection, release["asset_type"], release["asset_key"], release["version"], now)
            connection.execute(
                """
                UPDATE release_runs SET status = 'published', approved_by = ?,
                    updated_at = ? WHERE id = ?
                """,
                (actor, now, release_id),
            )
        result = self.get(release_id)
        assert result is not None
        return result

    def rollback(self, release_id: str, actor: str) -> Dict[str, Any]:
        now = utc_now()
        with self.store.connect() as connection:
            release = connection.execute(
                "SELECT * FROM release_runs WHERE id = ?", (release_id,)
            ).fetchone()
            if release is None:
                raise ValueError("发布记录不存在")
            if release["status"] != "published":
                raise ValueError("只有已发布版本可以回滚")
            previous = str(_load(release["diff_json"], {}).get("previous_version") or "")
            if not previous:
                raise ValueError("没有可回滚的上一版本")
            self._set_published(connection, release["asset_type"], release["asset_key"], previous, now)
            connection.execute(
                """
                UPDATE release_runs SET status = 'rolled_back', approved_by = ?,
                    updated_at = ? WHERE id = ?
                """,
                (actor, now, release_id),
            )
        result = self.get(release_id)
        assert result is not None
        return result

    @staticmethod
    def _set_published(connection: Any, asset_type: str, asset_key: str, version: str, now: str) -> None:
        if asset_type == "prompt":
            exists = connection.execute(
                "SELECT 1 FROM prompt_versions WHERE prompt_key = ? AND version = ?",
                (asset_key, version),
            ).fetchone()
            if exists is None:
                raise ValueError("提示词版本不存在")
            connection.execute(
                "UPDATE prompt_versions SET release_status = 'superseded' WHERE prompt_key = ? AND release_status = 'published'",
                (asset_key,),
            )
            connection.execute(
                "UPDATE prompt_versions SET release_status = 'published', published_at = ? WHERE prompt_key = ? AND version = ?",
                (now, asset_key, version),
            )
            connection.execute(
                "UPDATE prompt_definitions SET lifecycle_status = 'published', published_version = ?, updated_at = ? WHERE prompt_key = ?",
                (version, now, asset_key),
            )
            return
        exists = connection.execute(
            "SELECT 1 FROM knowledge_versions WHERE card_id = ? AND version = ?",
            (asset_key, version),
        ).fetchone()
        if exists is None:
            raise ValueError("知识版本不存在")
        connection.execute(
            "UPDATE knowledge_versions SET release_status = 'superseded' WHERE card_id = ? AND release_status = 'published'",
            (asset_key,),
        )
        connection.execute(
            "UPDATE knowledge_versions SET release_status = 'published', published_at = ? WHERE card_id = ? AND version = ?",
            (now, asset_key, version),
        )
        connection.execute(
            "UPDATE knowledge_cards SET lifecycle_status = 'published', published_version = ?, updated_at = ? WHERE card_id = ?",
            (version, now, asset_key),
        )

    def _current_version(self, asset_type: str, asset_key: str) -> str:
        table, key = (
            ("prompt_definitions", "prompt_key")
            if asset_type == "prompt"
            else ("knowledge_cards", "card_id")
        )
        with self.store.connect() as connection:
            row = connection.execute(
                f"SELECT published_version FROM {table} WHERE {key} = ?", (asset_key,)
            ).fetchone()
        if row is None:
            raise ValueError("资产不存在")
        return str(row["published_version"] or "")

    def _target(self, asset_type: str, asset_key: str, version: str) -> Optional[Dict[str, Any]]:
        if asset_type == "prompt":
            return self.assets.get_prompt_version(asset_key, version)
        card = self.assets.get_knowledge(asset_key)
        if card is None:
            return None
        for item in card["versions"]:
            if item["version"] == version:
                return item
        return None

    def get(self, release_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM release_runs WHERE id = ?", (release_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["test_summary"] = _load(result.pop("test_summary_json"), [])
        result["diff"] = _load(result.pop("diff_json"), {})
        return result

    def list(self) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM release_runs ORDER BY created_at DESC"
            ).fetchall()
        return [self.get(str(row["id"])) for row in rows]
