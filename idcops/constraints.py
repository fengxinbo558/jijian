"""Versioned AI investigation policies with immutable operational guards."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .models import utc_now
from .store import IncidentStore, _dump, _load


DEFAULT_POLICY_KEY = "investigation-policy"
DEFAULT_POLICY_VERSION = "1.0.0"
ALLOWED_DOMAINS = {
    "application",
    "compute",
    "facility",
    "network",
    "storage",
    "system",
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "retrieval_top_k": 8,
    "vector_assist_enabled": True,
    "vector_only_min_similarity": 0.22,
    "evidence_excerpt_limit": 8,
    "no_evidence_mode": "insufficient",
    "allowed_domains": sorted(ALLOWED_DOMAINS),
}

HARD_GUARDS: List[Dict[str, Any]] = [
    {
        "key": "no_ai_operation_approval",
        "name": "AI 无现场操作批准权",
        "description": "断电、重启、拔盘、拔线和更换部件必须由有权人员确认。",
        "enforced_at": "权限服务与现场操作门禁",
        "editable": False,
    },
    {
        "key": "full_sn_required",
        "name": "完整 SN 必须逐字符核对",
        "description": "UID 灯、机架位和 SN 末位均不能替代完整设备身份。",
        "enforced_at": "现场操作门禁",
        "editable": False,
    },
    {
        "key": "permission_review_separated",
        "name": "操作许可与身份复核分离",
        "description": "确认设备正确不等于允许操作，两个责任结论分别保存。",
        "enforced_at": "现场操作状态机",
        "editable": False,
    },
    {
        "key": "raw_break_glass",
        "name": "原始记录受控访问",
        "description": "只有最高审计管理员填写原因并再次确认后才能临时查看指定原文。",
        "enforced_at": "角色权限与原始访问审计",
        "editable": False,
    },
    {
        "key": "evidence_references_required",
        "name": "结论必须引用可追查证据",
        "description": "缺少证据时只能给出候选方向或覆盖不足，不能写成已确认根因。",
        "enforced_at": "模型输出与调查结论校验",
        "editable": False,
    },
]


class ConstraintRegistry:
    """Store adjustable investigation settings without weakening hard guards."""

    def __init__(self, store: IncidentStore) -> None:
        self.store = store

    def ensure_seeded(self) -> None:
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO constraint_profiles (
                    policy_key, name, purpose, lifecycle_status,
                    published_version, created_at, updated_at
                ) VALUES (?, 'AI 调查与检索策略',
                    '控制可调整的检索参数，不包含现场安全门禁',
                    'published', ?, ?, ?)
                """,
                (DEFAULT_POLICY_KEY, DEFAULT_POLICY_VERSION, now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO constraint_versions (
                    policy_key, version, release_status, settings_json,
                    created_by, created_at, published_at
                ) VALUES (?, ?, 'published', ?, 'system_seed', ?, ?)
                """,
                (
                    DEFAULT_POLICY_KEY,
                    DEFAULT_POLICY_VERSION,
                    _dump(DEFAULT_SETTINGS),
                    now,
                    now,
                ),
            )

    @staticmethod
    def validate(settings: Mapping[str, Any]) -> Dict[str, Any]:
        merged = dict(DEFAULT_SETTINGS)
        merged.update(dict(settings))
        top_k = int(merged.get("retrieval_top_k", 0))
        if top_k < 1 or top_k > 20:
            raise ValueError("检索 Top-K 必须在 1 到 20 之间")
        similarity = float(merged.get("vector_only_min_similarity", -1))
        if similarity < 0 or similarity > 1:
            raise ValueError("纯向量最低相似度必须在 0 到 1 之间")
        excerpt_limit = int(merged.get("evidence_excerpt_limit", 0))
        if excerpt_limit < 1 or excerpt_limit > 50:
            raise ValueError("证据摘录数量必须在 1 到 50 之间")
        no_evidence_mode = str(merged.get("no_evidence_mode") or "")
        if no_evidence_mode not in {"insufficient", "candidate_only"}:
            raise ValueError("无证据输出模式只能是 insufficient 或 candidate_only")
        domains = merged.get("allowed_domains", [])
        if not isinstance(domains, list) or not domains:
            raise ValueError("允许参与检索的知识领域不能为空")
        normalized_domains = sorted({str(item) for item in domains})
        unknown = sorted(set(normalized_domains) - ALLOWED_DOMAINS)
        if unknown:
            raise ValueError("存在未知知识领域：" + "、".join(unknown))
        return {
            "retrieval_top_k": top_k,
            "vector_assist_enabled": bool(merged.get("vector_assist_enabled")),
            "vector_only_min_similarity": similarity,
            "evidence_excerpt_limit": excerpt_limit,
            "no_evidence_mode": no_evidence_mode,
            "allowed_domains": normalized_domains,
        }

    def list(self) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM constraint_profiles ORDER BY policy_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, policy_key: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            profile = connection.execute(
                "SELECT * FROM constraint_profiles WHERE policy_key = ?", (policy_key,)
            ).fetchone()
            if profile is None:
                return None
            versions = connection.execute(
                """
                SELECT version, release_status, settings_json, created_by,
                       created_at, published_at
                FROM constraint_versions WHERE policy_key = ? ORDER BY id DESC
                """,
                (policy_key,),
            ).fetchall()
        result = dict(profile)
        result["versions"] = [
            {
                "version": row["version"],
                "release_status": row["release_status"],
                "settings": _load(row["settings_json"], {}),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "published_at": row["published_at"],
            }
            for row in versions
        ]
        result["published"] = next(
            (
                item
                for item in result["versions"]
                if item["version"] == result["published_version"]
            ),
            None,
        )
        result["hard_guards"] = list(HARD_GUARDS)
        return result

    def get_version(self, policy_key: str, version: str) -> Optional[Dict[str, Any]]:
        policy = self.get(policy_key)
        if policy is None:
            return None
        return next((item for item in policy["versions"] if item["version"] == version), None)

    def published_settings(self, policy_key: str = DEFAULT_POLICY_KEY) -> Dict[str, Any]:
        policy = self.get(policy_key)
        if policy is None or policy.get("published") is None:
            return dict(DEFAULT_SETTINGS)
        return self.validate(policy["published"]["settings"])

    def published_version(self, policy_key: str = DEFAULT_POLICY_KEY) -> str:
        policy = self.get(policy_key)
        return str((policy or {}).get("published_version") or DEFAULT_POLICY_VERSION)

    def create_version(
        self, policy_key: str, payload: Mapping[str, Any], actor: str
    ) -> Dict[str, Any]:
        version = str(payload.get("version") or "").strip()
        if not version:
            raise ValueError("约束版本不能为空")
        settings = payload.get("settings", {})
        if not isinstance(settings, Mapping):
            raise ValueError("约束设置必须是对象")
        normalized = self.validate(settings)
        now = utc_now()
        with self.store.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM constraint_profiles WHERE policy_key = ?", (policy_key,)
            ).fetchone()
            if exists is None:
                raise ValueError("约束策略不存在")
            try:
                connection.execute(
                    """
                    INSERT INTO constraint_versions (
                        policy_key, version, release_status, settings_json,
                        created_by, created_at, published_at
                    ) VALUES (?, ?, 'draft', ?, ?, ?, '')
                    """,
                    (policy_key, version, _dump(normalized), actor, now),
                )
                connection.execute(
                    "UPDATE constraint_profiles SET updated_at = ? WHERE policy_key = ?",
                    (now, policy_key),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError("这个约束版本已经存在") from exc
                raise
        created = self.get_version(policy_key, version)
        assert created is not None
        return created
