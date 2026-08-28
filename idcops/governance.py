"""Govern versioned AI assets without turning operational evidence into editable content."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .assets import AssetRegistry
from .constraints import ConstraintRegistry
from .knowledge import REQUIRED_FIELDS
from .models import utc_now
from .store import IncidentStore, _dump, _load


ASSET_TYPES = {"knowledge", "prompt", "constraint", "test_case"}
RELATION_TYPES = {
    "supersedes",
    "merged_into",
    "equivalent",
    "related",
    "competes_with",
    "requires",
}
FEEDBACK_OUTCOMES = {
    "adopted",
    "rejected",
    "helped_resolve",
    "not_resolved",
    "unverified",
}
RESOLUTION_ACTIONS = {
    "merge",
    "keep_separate",
    "relate",
    "supplement_conditions",
    "ignore",
}
VOLATILE_KEYS = {
    "id",
    "version",
    "status",
    "review",
    "created_at",
    "updated_at",
    "published_at",
}
HIGH_RISK_SHORTCUTS = (
    r"无需.{0,8}(确认|许可|审批)",
    r"直接断电",
    r"自动批准",
    r"跳过.{0,8}(核对|复核|确认)",
    r"只核对.{0,8}(末位|后几位)",
)


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _stable_content(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_content(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_stable_content(item) for item in value]
    if isinstance(value, str):
        return _normalized_text(value)
    return value


def content_fingerprint(value: Mapping[str, Any]) -> str:
    material = json.dumps(
        _stable_content(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _tokens(values: Iterable[Any]) -> set:
    result = set()
    for value in values:
        text = _normalized_text(value)
        if not text:
            continue
        result.add(text)
        result.update(re.findall(r"[a-z0-9_./:-]+", text))
    return result


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _bigrams(value: str) -> set:
    compact = re.sub(r"\s+", "", _normalized_text(value))
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


class AssetGovernanceService:
    """Catalog, inspect, stage and audit AI assets around their existing truth tables."""

    def __init__(
        self,
        store: IncidentStore,
        assets: AssetRegistry,
        constraints: ConstraintRegistry,
    ) -> None:
        self.store = store
        self.assets = assets
        self.constraints = constraints

    def ensure_seeded(self) -> Dict[str, int]:
        now = utc_now()
        with self.store.connect() as connection:
            sources = connection.execute(
                "SELECT source_key, content_json FROM knowledge_sources"
            ).fetchall()
            for row in sources:
                content = _load(row["content_json"], {})
                version = str(content.get("version") or "unversioned")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO ai_source_versions (
                        source_key, version, content_json, content_fingerprint,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, 'available', ?)
                    """,
                    (
                        row["source_key"],
                        version,
                        _dump(content),
                        content_fingerprint(content),
                        now,
                    ),
                )

            for row in connection.execute("SELECT * FROM knowledge_cards").fetchall():
                versions = connection.execute(
                    "SELECT * FROM knowledge_versions WHERE card_id = ?",
                    (row["card_id"],),
                ).fetchall()
                published = next(
                    (item for item in versions if item["version"] == row["published_version"]),
                    versions[0] if versions else None,
                )
                content = _load(published["content_json"], {}) if published else {}
                review = content.get("review") if isinstance(content.get("review"), Mapping) else {}
                self._insert_metadata(
                    connection,
                    "knowledge",
                    str(row["card_id"]),
                    str(row["title"]),
                    str(row["domain"]),
                    str(row["card_id"]).split("-", 1)[0].lower(),
                    str(review.get("owner") or "待补充"),
                    "unclassified",
                    str(row["lifecycle_status"]),
                    str(review.get("reviewed_at") or ""),
                    "",
                    "system_seed",
                    now,
                )
                for version_row in versions:
                    version_content = _load(version_row["content_json"], {})
                    self._insert_version_metadata(
                        connection,
                        "knowledge",
                        str(row["card_id"]),
                        str(version_row["version"]),
                        version_content,
                        now,
                    )

            for row in connection.execute("SELECT * FROM prompt_definitions").fetchall():
                self._insert_metadata(
                    connection,
                    "prompt",
                    str(row["prompt_key"]),
                    str(row["name"]),
                    "ai",
                    "prompt",
                    "system_seed",
                    "controlled",
                    str(row["lifecycle_status"]),
                    "",
                    "",
                    "system_seed",
                    now,
                )
                for version_row in connection.execute(
                    "SELECT * FROM prompt_versions WHERE prompt_key = ?",
                    (row["prompt_key"],),
                ).fetchall():
                    content = self._prompt_version_content(version_row)
                    self._insert_version_metadata(
                        connection,
                        "prompt",
                        str(row["prompt_key"]),
                        str(version_row["version"]),
                        content,
                        now,
                    )

            for row in connection.execute("SELECT * FROM constraint_profiles").fetchall():
                self._insert_metadata(
                    connection,
                    "constraint",
                    str(row["policy_key"]),
                    str(row["name"]),
                    "safety",
                    "policy",
                    "system_seed",
                    "critical",
                    str(row["lifecycle_status"]),
                    "",
                    "",
                    "system_seed",
                    now,
                )
                for version_row in connection.execute(
                    "SELECT * FROM constraint_versions WHERE policy_key = ?",
                    (row["policy_key"],),
                ).fetchall():
                    content = {"settings": _load(version_row["settings_json"], {})}
                    self._insert_version_metadata(
                        connection,
                        "constraint",
                        str(row["policy_key"]),
                        str(version_row["version"]),
                        content,
                        now,
                    )

            for row in connection.execute("SELECT * FROM ai_test_case_definitions").fetchall():
                self._insert_metadata(
                    connection,
                    "test_case",
                    str(row["case_key"]),
                    str(row["name"]),
                    str(row["domain"]),
                    "regression",
                    "待补充",
                    "controlled",
                    str(row["lifecycle_status"]),
                    "",
                    "",
                    "system_seed",
                    now,
                )
        return self.counts()

    def counts(self) -> Dict[str, int]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT asset_type, COUNT(*) AS count FROM ai_asset_metadata GROUP BY asset_type"
            ).fetchall()
        result = {asset_type: 0 for asset_type in ASSET_TYPES}
        result.update({str(row["asset_type"]): int(row["count"]) for row in rows})
        return result

    @staticmethod
    def _insert_metadata(
        connection: Any,
        asset_type: str,
        asset_key: str,
        title: str,
        domain: str,
        fault_family: str,
        owner: str,
        risk_level: str,
        status: str,
        reviewed_at: str,
        review_due_at: str,
        actor: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO ai_asset_metadata (
                asset_type, asset_key, title, domain, fault_family, owner,
                risk_level, tags_json, catalog_status, reviewed_at,
                review_due_at, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_type,
                asset_key,
                title,
                domain,
                fault_family,
                owner,
                risk_level,
                status,
                reviewed_at,
                review_due_at,
                actor,
                now,
                now,
            ),
        )

    @staticmethod
    def _insert_version_metadata(
        connection: Any,
        asset_type: str,
        asset_key: str,
        version: str,
        content: Mapping[str, Any],
        now: str,
    ) -> None:
        review = content.get("review") if isinstance(content.get("review"), Mapping) else {}
        connection.execute(
            """
            INSERT OR IGNORE INTO ai_asset_version_metadata (
                asset_type, asset_key, version, applies_to_json,
                source_refs_json, content_fingerprint, risk_level,
                reviewed_by, review_method, effective_at, governance_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                asset_type,
                asset_key,
                version,
                _dump(_string_list(content.get("applies_to"))),
                _dump(_string_list(content.get("sources"))),
                content_fingerprint(content),
                str(content.get("risk_level") or "unclassified"),
                str(review.get("owner") or ""),
                str(review.get("review_method") or ""),
                str(review.get("reviewed_at") or ""),
                now,
            ),
        )

    @staticmethod
    def _prompt_version_content(row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "system_content": row["system_content"],
            "user_template": row["user_template"],
            "variables": _load(row["variables_json"], []),
            "output_schema": _load(row["output_schema_json"], []),
            "settings": _load(row["settings_json"], {}),
            "content": _load(row["content_json"], {}),
        }

    def ensure_asset_metadata(self, asset_type: str, asset_key: str) -> Dict[str, Any]:
        if asset_type not in ASSET_TYPES:
            raise ValueError("不支持的资产类型")
        self.ensure_seeded()
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_asset_metadata WHERE asset_type = ? AND asset_key = ?",
                (asset_type, asset_key),
            ).fetchone()
        if row is None:
            raise ValueError("资产不存在")
        return self._decode_metadata(row)

    @staticmethod
    def _decode_metadata(row: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        result["tags"] = _load(result.pop("tags_json"), [])
        return result

    def _asset_state(self, connection: Any, asset_type: str, asset_key: str) -> Dict[str, Any]:
        if asset_type == "knowledge":
            row = connection.execute(
                "SELECT lifecycle_status, published_version, updated_at FROM knowledge_cards WHERE card_id = ?",
                (asset_key,),
            ).fetchone()
            version_table, key_column = "knowledge_versions", "card_id"
        elif asset_type == "prompt":
            row = connection.execute(
                "SELECT lifecycle_status, published_version, updated_at FROM prompt_definitions WHERE prompt_key = ?",
                (asset_key,),
            ).fetchone()
            version_table, key_column = "prompt_versions", "prompt_key"
        elif asset_type == "constraint":
            row = connection.execute(
                "SELECT lifecycle_status, published_version, updated_at FROM constraint_profiles WHERE policy_key = ?",
                (asset_key,),
            ).fetchone()
            version_table, key_column = "constraint_versions", "policy_key"
        else:
            row = connection.execute(
                "SELECT lifecycle_status, published_version, updated_at FROM ai_test_case_definitions WHERE case_key = ?",
                (asset_key,),
            ).fetchone()
            version_table, key_column = "ai_test_case_versions", "case_key"
        if row is None:
            return {"lifecycle_status": "missing", "published_version": "", "updated_at": "", "draft_count": 0}
        drafts = connection.execute(
            f"SELECT COUNT(*) AS count FROM {version_table} WHERE {key_column} = ? AND release_status = 'draft'",
            (asset_key,),
        ).fetchone()["count"]
        return {**dict(row), "draft_count": int(drafts)}

    @staticmethod
    def _is_due(value: str) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return False
        try:
            due = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            return due <= datetime.now(timezone.utc)
        except ValueError:
            return False

    def list_assets(self, filters: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        self.ensure_seeded()
        values = dict(filters or {})
        asset_type = str(values.get("asset_type") or "").strip()
        domain = str(values.get("domain") or "").strip()
        status = str(values.get("status") or "").strip()
        owner = str(values.get("owner") or "").strip()
        risk = str(values.get("risk") or "").strip()
        review = str(values.get("review") or "").strip()
        query = _normalized_text(values.get("query") or values.get("q") or "")
        try:
            page = max(1, int(values.get("page") or 1))
            page_size = max(1, min(100, int(values.get("page_size") or 50)))
        except (TypeError, ValueError):
            raise ValueError("分页参数必须是整数") from None
        if asset_type and asset_type not in ASSET_TYPES:
            raise ValueError("不支持的资产类型")
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_asset_metadata ORDER BY updated_at DESC, asset_type, asset_key"
            ).fetchall()
            items: List[Dict[str, Any]] = []
            for row in rows:
                metadata = self._decode_metadata(row)
                if asset_type and metadata["asset_type"] != asset_type:
                    continue
                if domain and metadata["domain"] != domain:
                    continue
                if owner and metadata["owner"] != owner:
                    continue
                if risk and metadata["risk_level"] != risk:
                    continue
                if query and query not in _normalized_text(
                    " ".join(
                        [
                            metadata["asset_key"],
                            metadata["title"],
                            metadata["domain"],
                            metadata["fault_family"],
                            metadata["owner"],
                            " ".join(metadata["tags"]),
                        ]
                    )
                ):
                    continue
                state = self._asset_state(connection, metadata["asset_type"], metadata["asset_key"])
                unresolved = connection.execute(
                    """
                    SELECT severity, COUNT(*) AS count FROM ai_governance_issues
                    WHERE primary_type = ? AND primary_key = ? AND status IN ('open', 'confirmed')
                    GROUP BY severity
                    """,
                    (metadata["asset_type"], metadata["asset_key"]),
                ).fetchall()
                issue_counts = {str(item["severity"]): int(item["count"]) for item in unresolved}
                if issue_counts.get("blocking"):
                    health = "blocked"
                elif issue_counts:
                    health = "needs_attention"
                elif self._is_due(metadata["review_due_at"]):
                    health = "review_due"
                elif metadata["owner"] in {"", "待补充"}:
                    health = "missing_metadata"
                else:
                    health = "healthy"
                if status and state["lifecycle_status"] != status and health != status:
                    continue
                if review == "due" and health != "review_due":
                    continue
                items.append(
                    {
                        **metadata,
                        **state,
                        "health": health,
                        "issue_counts": issue_counts,
                    }
                )
        total = len(items)
        start = (page - 1) * page_size
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "items": items[start : start + page_size],
        }

    def get_asset(self, asset_type: str, asset_key: str) -> Dict[str, Any]:
        metadata = self.ensure_asset_metadata(asset_type, asset_key)
        if asset_type == "knowledge":
            content = self.assets.get_knowledge(asset_key)
        elif asset_type == "prompt":
            content = self.assets.get_prompt(asset_key)
        elif asset_type == "constraint":
            content = self.constraints.get(asset_key)
        else:
            content = self.get_test_case(asset_key)
        if content is None:
            raise ValueError("资产不存在")
        with self.store.connect() as connection:
            version_rows = connection.execute(
                """
                SELECT * FROM ai_asset_version_metadata
                WHERE asset_type = ? AND asset_key = ? ORDER BY created_at DESC
                """,
                (asset_type, asset_key),
            ).fetchall()
            issue_rows = connection.execute(
                """
                SELECT * FROM ai_governance_issues
                WHERE (primary_type = ? AND primary_key = ?)
                   OR (related_type = ? AND related_key = ?)
                ORDER BY created_at DESC
                """,
                (asset_type, asset_key, asset_type, asset_key),
            ).fetchall()
            relation_rows = connection.execute(
                """
                SELECT * FROM ai_asset_relations
                WHERE (source_type = ? AND source_key = ?)
                   OR (target_type = ? AND target_key = ?)
                ORDER BY created_at DESC
                """,
                (asset_type, asset_key, asset_type, asset_key),
            ).fetchall()
            feedback_rows = connection.execute(
                """
                SELECT * FROM ai_asset_feedback
                WHERE asset_type = ? AND asset_key = ? ORDER BY created_at DESC
                """,
                (asset_type, asset_key),
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT * FROM ai_governance_audit
                WHERE asset_type = ? AND asset_key = ? ORDER BY created_at DESC LIMIT 50
                """,
                (asset_type, asset_key),
            ).fetchall()
            hit_count = 0
            incident_count = 0
            if asset_type == "knowledge":
                hit_row = connection.execute(
                    """
                    SELECT COUNT(*) AS hits, COUNT(DISTINCT rr.incident_id) AS incidents
                    FROM rag_hits rh JOIN rag_runs rr ON rr.id = rh.run_id
                    WHERE rh.card_id = ?
                    """,
                    (asset_key,),
                ).fetchone()
                hit_count = int(hit_row["hits"])
                incident_count = int(hit_row["incidents"])
        effect_counter = Counter(str(row["outcome"]) for row in feedback_rows)
        effect = dict(effect_counter)
        effect.update({"rag_hits": hit_count, "incidents": incident_count, "sample_size": len(feedback_rows)})
        return {
            "metadata": metadata,
            "content": content,
            "versions": [self._decode_version_metadata(row) for row in version_rows],
            "issues": [self._decode_issue(row) for row in issue_rows],
            "relations": [self._decode_relation(row) for row in relation_rows],
            "feedback": [dict(row) for row in feedback_rows],
            "effect": effect,
            "audit": [self._decode_audit(row) for row in audit_rows],
        }

    @staticmethod
    def _decode_version_metadata(row: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        result["applies_to"] = _load(result.pop("applies_to_json"), [])
        result["source_refs"] = _load(result.pop("source_refs_json"), [])
        result["governance"] = _load(result.pop("governance_json"), {})
        return result

    def update_metadata(
        self,
        asset_type: str,
        asset_key: str,
        payload: Mapping[str, Any],
        actor: str,
    ) -> Dict[str, Any]:
        current = self.ensure_asset_metadata(asset_type, asset_key)
        allowed = {
            "owner",
            "domain",
            "fault_family",
            "risk_level",
            "tags",
            "reviewed_at",
            "review_due_at",
            "catalog_status",
        }
        updates = {key: payload[key] for key in allowed if key in payload}
        if "tags" in updates and not isinstance(updates["tags"], list):
            raise ValueError("标签必须是列表")
        now = utc_now()
        values = {
            key: (str(value).strip() if key != "tags" else _dump(_string_list(value)))
            for key, value in updates.items()
        }
        if "tags" in values:
            values["tags_json"] = values.pop("tags")
        if not values:
            return current
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.store.connect() as connection:
            connection.execute(
                f"UPDATE ai_asset_metadata SET {assignments}, updated_at = ? WHERE asset_type = ? AND asset_key = ?",
                (*values.values(), now, asset_type, asset_key),
            )
            self._audit(
                connection,
                "asset_metadata_updated",
                actor,
                asset_type,
                asset_key,
                {"before": current, "changes": updates},
                now,
            )
        return self.ensure_asset_metadata(asset_type, asset_key)

    @staticmethod
    def _knowledge_structure(content: Mapping[str, Any]) -> set:
        values: List[Any] = [content.get("domain", "")]
        for field in (
            "applies_to",
            "symptoms",
            "supporting_signals",
            "verification_steps",
            "stop_conditions",
            "safe_actions",
            "prohibited_inferences",
        ):
            values.extend(_string_list(content.get(field)))
        match = content.get("match") if isinstance(content.get("match"), Mapping) else {}
        values.extend(_string_list(match.get("rule_names")))
        values.extend(_string_list(match.get("fact_types")))
        values.extend(_string_list(match.get("terms")))
        return _tokens(values)

    @staticmethod
    def _knowledge_text(content: Mapping[str, Any]) -> str:
        values: List[str] = [str(content.get("title") or "")]
        for field in (
            "symptoms",
            "supporting_signals",
            "competing_causes",
            "verification_steps",
            "branch_conditions",
            "stop_conditions",
        ):
            values.extend(_string_list(content.get(field)))
        return " ".join(values)

    @staticmethod
    def _unsafe_conflicts(content: Mapping[str, Any]) -> List[Dict[str, Any]]:
        checked: List[Tuple[str, str]] = []
        for field in ("safe_actions", "verification_steps", "branch_conditions"):
            checked.extend((field, item) for item in _string_list(content.get(field)))
        found = []
        for field, text in checked:
            for pattern in HIGH_RISK_SHORTCUTS:
                if re.search(pattern, text):
                    found.append(
                        {
                            "severity": "blocking",
                            "issue_type": "unsafe_conflict",
                            "field": field,
                            "text": text,
                            "rule": pattern,
                            "reason": "内容可能绕过人工许可、完整身份核对或高风险操作门禁",
                        }
                    )
        return found

    def scan_knowledge_candidate(
        self,
        candidate: Mapping[str, Any],
        actor: str,
        *,
        persist: bool = False,
    ) -> Dict[str, Any]:
        content = dict(candidate)
        fingerprint = content_fingerprint(content)
        candidate_key = str(content.get("id") or "UNASSIGNED")
        candidate_version = str(content.get("version") or "draft")
        candidate_domain = str(content.get("domain") or "")
        candidate_structure = self._knowledge_structure(content)
        candidate_text = _bigrams(self._knowledge_text(content))
        matches: List[Dict[str, Any]] = []
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT kv.card_id, kv.version, kv.content_json, avm.content_fingerprint
                FROM knowledge_versions kv
                LEFT JOIN ai_asset_version_metadata avm
                  ON avm.asset_type = 'knowledge'
                 AND avm.asset_key = kv.card_id AND avm.version = kv.version
                """
            ).fetchall()
        for row in rows:
            existing = _load(row["content_json"], {})
            existing_fingerprint = str(row["content_fingerprint"] or content_fingerprint(existing))
            if existing_fingerprint == fingerprint:
                matches.append(
                    {
                        "asset_type": "knowledge",
                        "asset_key": row["card_id"],
                        "version": row["version"],
                        "match_type": "exact",
                        "score": 1.0,
                        "reasons": ["标准化内容指纹完全相同"],
                    }
                )
                continue
            if candidate_domain and str(existing.get("domain") or "") != candidate_domain:
                continue
            structure_score = _jaccard(candidate_structure, self._knowledge_structure(existing))
            text_score = _jaccard(candidate_text, _bigrams(self._knowledge_text(existing)))
            score = round(structure_score * 0.65 + text_score * 0.35, 4)
            if score >= 0.82:
                reasons = [f"结构字段重叠 {structure_score:.2f}", f"本地文本特征相似 {text_score:.2f}"]
                matches.append(
                    {
                        "asset_type": "knowledge",
                        "asset_key": row["card_id"],
                        "version": row["version"],
                        "match_type": "near",
                        "score": score,
                        "reasons": reasons,
                    }
                )
        matches.sort(key=lambda item: (-float(item["score"]), item["asset_key"], item["version"]))
        exact = [item for item in matches if item["match_type"] == "exact"]
        unsafe = self._unsafe_conflicts(content)
        issues: List[Dict[str, Any]] = list(unsafe)
        classification = "ready"
        if unsafe:
            classification = "conflict"
        elif exact:
            classification = "exact_duplicate"
        elif matches:
            classification = "near_duplicate"
        if persist:
            if unsafe:
                issues = []
                for item in unsafe:
                    issues.append(
                        self.create_issue(
                            item["issue_type"],
                            item["severity"],
                            "knowledge",
                            candidate_key,
                            candidate_version,
                            item,
                            "deterministic_guard",
                        )
                    )
            elif exact:
                issue = self.create_issue(
                    "exact_duplicate",
                    "info",
                    "knowledge",
                    candidate_key,
                    candidate_version,
                    {"matches": exact},
                    "content_fingerprint",
                    related=("knowledge", exact[0]["asset_key"], exact[0]["version"]),
                )
                issues.append(issue)
            elif matches:
                issue = self.create_issue(
                    "near_duplicate",
                    "review",
                    "knowledge",
                    candidate_key,
                    candidate_version,
                    {"matches": matches[:5]},
                    "structure_and_local_features",
                    related=("knowledge", matches[0]["asset_key"], matches[0]["version"]),
                )
                issues.append(issue)
        return {
            "classification": classification,
            "content_fingerprint": fingerprint,
            "matches": matches[:10],
            "issues": issues,
            "capabilities": ["sha256_content_fingerprint", "structured_overlap", "local_text_features"],
            "ai_confirmed": False,
        }

    def create_issue(
        self,
        issue_type: str,
        severity: str,
        primary_type: str,
        primary_key: str,
        primary_version: str,
        evidence: Mapping[str, Any],
        detection_method: str,
        related: Tuple[str, str, str] = ("", "", ""),
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.store.connect() as connection:
            duplicate = connection.execute(
                """
                SELECT * FROM ai_governance_issues
                WHERE issue_type = ? AND primary_type = ? AND primary_key = ?
                  AND primary_version = ? AND related_type = ? AND related_key = ?
                  AND related_version = ? AND status IN ('open', 'confirmed')
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    issue_type,
                    primary_type,
                    primary_key,
                    primary_version,
                    related[0],
                    related[1],
                    related[2],
                ),
            ).fetchone()
            if duplicate is not None:
                return self._decode_issue(duplicate)
            issue_id = "GOV-" + uuid.uuid4().hex[:12].upper()
            connection.execute(
                """
                INSERT INTO ai_governance_issues (
                    id, issue_type, severity, status, primary_type, primary_key,
                    primary_version, related_type, related_key, related_version,
                    detection_method, evidence_json, resolution_json, created_at,
                    resolved_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, '')
                """,
                (
                    issue_id,
                    issue_type,
                    severity,
                    primary_type,
                    primary_key,
                    primary_version,
                    related[0],
                    related[1],
                    related[2],
                    detection_method,
                    _dump(dict(evidence)),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM ai_governance_issues WHERE id = ?", (issue_id,)
            ).fetchone()
        return self._decode_issue(row)

    @staticmethod
    def _decode_issue(row: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        result["evidence"] = _load(result.pop("evidence_json"), {})
        result["resolution"] = _load(result.pop("resolution_json"), {})
        return result

    def list_issues(self, filters: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        values = dict(filters or {})
        clauses = []
        parameters: List[Any] = []
        for key, column in (
            ("status", "status"),
            ("issue_type", "issue_type"),
            ("severity", "severity"),
            ("asset_type", "primary_type"),
        ):
            value = str(values.get(key) or "").strip()
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM ai_governance_issues{where} ORDER BY created_at DESC",
                tuple(parameters),
            ).fetchall()
        return {"total": len(rows), "items": [self._decode_issue(row) for row in rows]}

    def list_relations(self, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_asset_relations ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._decode_relation(row) for row in rows]

    def list_feedback(self, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_asset_feedback ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_blocking_issues(self, asset_type: str, asset_key: str, version: str) -> bool:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM ai_governance_issues
                WHERE primary_type = ? AND primary_key = ? AND primary_version = ?
                  AND severity = 'blocking' AND status IN ('open', 'confirmed')
                LIMIT 1
                """,
                (asset_type, asset_key, version),
            ).fetchone()
        return row is not None

    def resolve_issue(self, issue_id: str, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        action = str(payload.get("action") or "").strip()
        if action not in RESOLUTION_ACTIONS:
            raise ValueError("请选择有效的治理处理方式")
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_governance_issues WHERE id = ?", (issue_id,)
            ).fetchone()
            if row is None:
                raise ValueError("治理问题不存在")
            if row["status"] == "resolved":
                return self._decode_issue(row)
            now = utc_now()
            resolution = {
                "action": action,
                "note": str(payload.get("note") or ""),
                "actor": actor,
            }
            connection.execute(
                """
                UPDATE ai_governance_issues
                SET status = 'resolved', resolution_json = ?, resolved_at = ?
                WHERE id = ?
                """,
                (_dump(resolution), now, issue_id),
            )
            self._audit(
                connection,
                "governance_issue_resolved",
                actor,
                str(row["primary_type"]),
                str(row["primary_key"]),
                {"issue_id": issue_id, **resolution},
                now,
            )
        relation_type = str(payload.get("relation_type") or "").strip()
        if action in {"relate", "merge"} and relation_type:
            self.create_relation(
                {
                    "source_type": row["primary_type"],
                    "source_key": row["primary_key"],
                    "source_version": row["primary_version"],
                    "relation_type": relation_type,
                    "target_type": row["related_type"],
                    "target_key": row["related_key"],
                    "target_version": row["related_version"],
                    "basis": {"issue_id": issue_id, "note": payload.get("note", "")},
                },
                actor,
            )
        return self.get_issue(issue_id)

    def get_issue(self, issue_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_governance_issues WHERE id = ?", (issue_id,)
            ).fetchone()
        if row is None:
            raise ValueError("治理问题不存在")
        return self._decode_issue(row)

    def create_relation(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        relation_type = str(payload.get("relation_type") or "").strip()
        if relation_type not in RELATION_TYPES:
            raise ValueError("不支持的资产关系")
        source_type = str(payload.get("source_type") or "").strip()
        target_type = str(payload.get("target_type") or "").strip()
        if source_type not in ASSET_TYPES or target_type not in ASSET_TYPES:
            raise ValueError("资产关系端点类型无效")
        source_key = str(payload.get("source_key") or "").strip()
        target_key = str(payload.get("target_key") or "").strip()
        if not source_key or not target_key:
            raise ValueError("资产关系必须包含两端编号")
        self.ensure_asset_metadata(source_type, source_key)
        self.ensure_asset_metadata(target_type, target_key)
        now = utc_now()
        relation_id = "RELN-" + uuid.uuid4().hex[:12].upper()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_asset_relations (
                    id, source_type, source_key, source_version, relation_type,
                    target_type, target_key, target_version, status, basis_json,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?)
                """,
                (
                    relation_id,
                    source_type,
                    source_key,
                    str(payload.get("source_version") or ""),
                    relation_type,
                    target_type,
                    target_key,
                    str(payload.get("target_version") or ""),
                    _dump(dict(payload.get("basis") or {})),
                    actor,
                    now,
                ),
            )
            self._audit(
                connection,
                "asset_relation_created",
                actor,
                source_type,
                source_key,
                {"relation_id": relation_id, "relation_type": relation_type, "target": f"{target_type}:{target_key}"},
                now,
            )
            row = connection.execute(
                "SELECT * FROM ai_asset_relations WHERE id = ?", (relation_id,)
            ).fetchone()
        return self._decode_relation(row)

    @staticmethod
    def _decode_relation(row: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        result["basis"] = _load(result.pop("basis_json"), {})
        return result

    def add_feedback(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        asset_type = str(payload.get("asset_type") or "").strip()
        asset_key = str(payload.get("asset_key") or "").strip()
        version = str(payload.get("version") or "").strip()
        outcome = str(payload.get("outcome") or "").strip()
        if asset_type not in ASSET_TYPES or not asset_key or not version:
            raise ValueError("反馈必须指向具体资产版本")
        if outcome not in FEEDBACK_OUTCOMES:
            raise ValueError("反馈结果无效")
        feedback_id = "FDB-" + uuid.uuid4().hex[:12].upper()
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_asset_feedback (
                    id, asset_type, asset_key, version, incident_id, outcome,
                    note, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    asset_type,
                    asset_key,
                    version,
                    str(payload.get("incident_id") or ""),
                    outcome,
                    str(payload.get("note") or ""),
                    actor,
                    now,
                ),
            )
            self._audit(
                connection,
                "asset_feedback_added",
                actor,
                asset_type,
                asset_key,
                {"feedback_id": feedback_id, "version": version, "outcome": outcome},
                now,
            )
            row = connection.execute(
                "SELECT * FROM ai_asset_feedback WHERE id = ?", (feedback_id,)
            ).fetchone()
        return dict(row)

    def _parse_import(self, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        if isinstance(payload.get("items"), list):
            return [dict(item) for item in payload["items"] if isinstance(item, Mapping)]
        format_name = str(payload.get("format") or "json").strip().lower()
        content = str(payload.get("content") or "").strip()
        if not content:
            raise ValueError("请输入要导入的内容")
        if format_name in {"json", "text"}:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON 解析失败：第 {exc.lineno} 行附近") from exc
            if isinstance(parsed, Mapping) and isinstance(parsed.get("cards"), list):
                parsed = parsed["cards"]
            elif isinstance(parsed, Mapping):
                parsed = [parsed]
            if not isinstance(parsed, list):
                raise ValueError("导入内容必须是一条知识或知识数组")
            return [dict(item) for item in parsed if isinstance(item, Mapping)]
        if format_name == "csv":
            rows = []
            for row in csv.DictReader(io.StringIO(content)):
                item: Dict[str, Any] = dict(row)
                for key, value in list(item.items()):
                    text = str(value or "").strip()
                    if text.startswith("[") or text.startswith("{"):
                        try:
                            item[key] = json.loads(text)
                        except json.JSONDecodeError:
                            item[key] = value
                rows.append(item)
            return rows
        raise ValueError("只支持 JSON、CSV 或结构化粘贴")

    @staticmethod
    def _validate_candidate(content: Mapping[str, Any]) -> str:
        missing = sorted(REQUIRED_FIELDS - set(content))
        if missing:
            return "缺少字段：" + "、".join(missing)
        if not str(content.get("id") or "").strip() or not str(content.get("version") or "").strip():
            return "知识编号和版本不能为空"
        for field in REQUIRED_FIELDS & {
            "applies_to",
            "symptoms",
            "supporting_signals",
            "competing_causes",
            "counter_signals",
            "required_context",
            "verification_steps",
            "branch_conditions",
            "stop_conditions",
            "safe_actions",
            "prohibited_inferences",
            "sources",
        }:
            if not isinstance(content.get(field), list):
                return f"字段 {field} 必须是列表"
        return ""

    def create_import_batch(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        items = self._parse_import(payload)
        if not items:
            raise ValueError("没有解析到可导入知识")
        if len(items) > 500:
            raise ValueError("单次最多导入 500 条知识")
        batch_id = "IMP-" + uuid.uuid4().hex[:12].upper()
        now = utc_now()
        staged = []
        summary = Counter()
        for index, candidate in enumerate(items, start=1):
            error = self._validate_candidate(candidate)
            if error:
                status = "invalid"
                scan = {"content_fingerprint": content_fingerprint(candidate), "matches": [], "issues": []}
            else:
                scan = self.scan_knowledge_candidate(candidate, actor, persist=False)
                status = str(scan["classification"])
            summary[status] += 1
            first_match = (scan.get("matches") or [{}])[0]
            staged.append((index, status, candidate, scan, error, first_match))
        summary_payload = {
            key: int(summary.get(key, 0))
            for key in ("ready", "exact_duplicate", "near_duplicate", "conflict", "invalid")
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_import_batches (
                    id, source_type, source_label, format, status, summary_json,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'scanned', ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    str(payload.get("source_type") or "manual"),
                    str(payload.get("source_label") or "手工导入"),
                    str(payload.get("format") or "json"),
                    _dump(summary_payload),
                    actor,
                    now,
                    now,
                ),
            )
            for index, status, candidate, scan, error, first_match in staged:
                connection.execute(
                    """
                    INSERT INTO ai_import_items (
                        batch_id, item_index, status, candidate_json,
                        content_fingerprint, matched_asset_type, matched_asset_key,
                        matched_version, issues_json, error_message, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        index,
                        status,
                        _dump(candidate),
                        str(scan.get("content_fingerprint") or content_fingerprint(candidate)),
                        str(first_match.get("asset_type") or ""),
                        str(first_match.get("asset_key") or ""),
                        str(first_match.get("version") or ""),
                        _dump(scan.get("issues") or scan.get("matches") or []),
                        error,
                        now,
                    ),
                )
            self._audit(
                connection,
                "import_batch_scanned",
                actor,
                "import_batch",
                batch_id,
                {"summary": summary_payload, "source_label": payload.get("source_label", "")},
                now,
            )
        return self.get_import_batch(batch_id)

    def get_import_batch(self, batch_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM ai_import_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise ValueError("导入批次不存在")
            items = connection.execute(
                "SELECT * FROM ai_import_items WHERE batch_id = ? ORDER BY item_index",
                (batch_id,),
            ).fetchall()
        result = dict(batch)
        result["summary"] = _load(result.pop("summary_json"), {})
        result["items"] = [self._decode_import_item(row) for row in items]
        return result

    @staticmethod
    def _decode_import_item(row: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        result["candidate"] = _load(result.pop("candidate_json"), {})
        result["issues"] = _load(result.pop("issues_json"), [])
        return result

    def list_import_batches(self, limit: int = 50) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(200, int(limit)))
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM ai_import_batches ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self.get_import_batch(str(row["id"])) for row in rows]

    def confirm_import_batch(self, batch_id: str, actor: str) -> Dict[str, Any]:
        now = utc_now()
        with self.store.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM ai_import_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise ValueError("导入批次不存在")
            if batch["status"] != "scanned":
                raise ValueError("只有已扫描批次可以确认")
            items = connection.execute(
                "SELECT * FROM ai_import_items WHERE batch_id = ? ORDER BY item_index",
                (batch_id,),
            ).fetchall()
            if any(row["status"] in {"near_duplicate", "conflict", "invalid"} for row in items):
                raise ValueError("批次仍有疑似重复、冲突或格式错误，请先处理")
            created = 0
            linked = 0
            for row in items:
                if row["status"] == "exact_duplicate":
                    linked += 1
                    connection.execute(
                        "UPDATE ai_import_items SET status = 'linked_existing' WHERE id = ?",
                        (row["id"],),
                    )
                    continue
                candidate = _load(row["candidate_json"], {})
                self._insert_knowledge_draft(connection, candidate, actor, now)
                connection.execute(
                    "UPDATE ai_import_items SET status = 'created_draft' WHERE id = ?",
                    (row["id"],),
                )
                created += 1
            final_summary = _load(batch["summary_json"], {})
            final_summary.update({"created_drafts": created, "linked_existing": linked})
            connection.execute(
                """
                UPDATE ai_import_batches SET status = 'completed', summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (_dump(final_summary), now, batch_id),
            )
            self._audit(
                connection,
                "import_batch_confirmed",
                actor,
                "import_batch",
                batch_id,
                final_summary,
                now,
            )
        return self.get_import_batch(batch_id)

    def _insert_knowledge_draft(
        self, connection: Any, candidate: Mapping[str, Any], actor: str, now: str
    ) -> None:
        card_id = str(candidate["id"])
        version = str(candidate["version"])
        exists = connection.execute(
            "SELECT 1 FROM knowledge_cards WHERE card_id = ?", (card_id,)
        ).fetchone()
        if exists is None:
            connection.execute(
                """
                INSERT INTO knowledge_cards (
                    card_id, domain, title, lifecycle_status, published_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', '', ?, ?)
                """,
                (card_id, str(candidate["domain"]), str(candidate["title"]), now, now),
            )
        connection.execute(
            """
            INSERT INTO knowledge_versions (
                card_id, version, release_status, content_json, created_by,
                created_at, published_at
            ) VALUES (?, ?, 'draft', ?, ?, ?, '')
            """,
            (card_id, version, _dump(dict(candidate)), actor, now),
        )
        review = candidate.get("review") if isinstance(candidate.get("review"), Mapping) else {}
        self._insert_metadata(
            connection,
            "knowledge",
            card_id,
            str(candidate["title"]),
            str(candidate["domain"]),
            card_id.split("-", 1)[0].lower(),
            str(review.get("owner") or "待补充"),
            "unclassified",
            "draft",
            str(review.get("reviewed_at") or ""),
            "",
            actor,
            now,
        )
        self._insert_version_metadata(
            connection, "knowledge", card_id, version, candidate, now
        )

    def cancel_import_batch(self, batch_id: str, actor: str) -> Dict[str, Any]:
        now = utc_now()
        with self.store.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM ai_import_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise ValueError("导入批次不存在")
            if batch["status"] != "scanned":
                raise ValueError("只有尚未确认的批次可以撤销")
            connection.execute(
                "UPDATE ai_import_batches SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, batch_id),
            )
            self._audit(
                connection,
                "import_batch_cancelled",
                actor,
                "import_batch",
                batch_id,
                {},
                now,
            )
        return self.get_import_batch(batch_id)

    def create_test_case(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        case_key = str(payload.get("case_key") or "").strip()
        version = str(payload.get("version") or "").strip()
        name = str(payload.get("name") or "").strip()
        domain = str(payload.get("domain") or "").strip()
        test_input = payload.get("input")
        expected = payload.get("expected")
        if not case_key or not version or not name or not domain:
            raise ValueError("测试用例编号、名称、领域和版本不能为空")
        if not isinstance(test_input, Mapping) or not isinstance(expected, Mapping):
            raise ValueError("测试输入和预期结果必须是对象")
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_test_case_definitions (
                    case_key, name, domain, lifecycle_status, published_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', '', ?, ?)
                """,
                (case_key, name, domain, now, now),
            )
            connection.execute(
                """
                INSERT INTO ai_test_case_versions (
                    case_key, version, release_status, input_json, expected_json,
                    created_by, created_at, published_at
                ) VALUES (?, ?, 'draft', ?, ?, ?, ?, '')
                """,
                (case_key, version, _dump(dict(test_input)), _dump(dict(expected)), actor, now),
            )
            self._insert_metadata(
                connection,
                "test_case",
                case_key,
                name,
                domain,
                "regression",
                actor,
                "controlled",
                "draft",
                "",
                "",
                actor,
                now,
            )
            self._insert_version_metadata(
                connection,
                "test_case",
                case_key,
                version,
                {"input": dict(test_input), "expected": dict(expected)},
                now,
            )
            self._audit(
                connection,
                "test_case_draft_created",
                actor,
                "test_case",
                case_key,
                {"version": version},
                now,
            )
        result = self.get_test_case(case_key)
        assert result is not None
        return result

    def get_test_case(self, case_key: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            definition = connection.execute(
                "SELECT * FROM ai_test_case_definitions WHERE case_key = ?", (case_key,)
            ).fetchone()
            if definition is None:
                return None
            versions = connection.execute(
                "SELECT * FROM ai_test_case_versions WHERE case_key = ? ORDER BY id DESC",
                (case_key,),
            ).fetchall()
        result = dict(definition)
        result["versions"] = [
            {
                **{key: row[key] for key in row.keys() if key not in {"input_json", "expected_json"}},
                "input": _load(row["input_json"], {}),
                "expected": _load(row["expected_json"], {}),
            }
            for row in versions
        ]
        return result

    def list_test_cases(self) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT case_key FROM ai_test_case_definitions ORDER BY updated_at DESC"
            ).fetchall()
        return [self.get_test_case(str(row["case_key"])) for row in rows]

    def summary(self) -> Dict[str, Any]:
        catalog = self.list_assets({"page_size": 100})
        all_assets = list(catalog["items"])
        for page in range(2, int(catalog["pages"]) + 1):
            all_assets.extend(
                self.list_assets({"page": page, "page_size": 100})["items"]
            )
        with self.store.connect() as connection:
            issue_rows = connection.execute(
                """
                SELECT issue_type, COUNT(*) AS count FROM ai_governance_issues
                WHERE status IN ('open', 'confirmed') GROUP BY issue_type
                """
            ).fetchall()
            imports = int(
                connection.execute("SELECT COUNT(*) AS count FROM ai_import_batches").fetchone()["count"]
            )
            sources = int(
                connection.execute("SELECT COUNT(*) AS count FROM ai_source_versions").fetchone()["count"]
            )
        health = Counter(item["health"] for item in all_assets)
        return {
            "asset_counts": self.counts(),
            "total_assets": catalog["total"],
            "issue_counts": {str(row["issue_type"]): int(row["count"]) for row in issue_rows},
            "health_counts": dict(health),
            "source_versions": sources,
            "import_batches": imports,
        }

    def lineage(self, asset_type: str, asset_key: str, version: str = "") -> Dict[str, Any]:
        detail = self.get_asset(asset_type, asset_key)
        target_version = version or str(
            detail["content"].get("published_version")
            if isinstance(detail["content"], Mapping)
            else ""
        )
        version_meta = next(
            (item for item in detail["versions"] if item["version"] == target_version),
            detail["versions"][0] if detail["versions"] else {},
        )
        source_keys = list(version_meta.get("source_refs") or [])
        with self.store.connect() as connection:
            sources = []
            for source_key in source_keys:
                row = connection.execute(
                    """
                    SELECT * FROM ai_source_versions WHERE source_key = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (source_key,),
                ).fetchone()
                if row is not None:
                    item = dict(row)
                    item["content"] = _load(item.pop("content_json"), {})
                    sources.append(item)
            runs = []
            if asset_type == "knowledge":
                run_rows = connection.execute(
                    """
                    SELECT rh.run_id, rr.incident_id, rr.mode, rr.created_at,
                           rh.rank, rh.score, rh.reasons_json
                    FROM rag_hits rh JOIN rag_runs rr ON rr.id = rh.run_id
                    WHERE rh.card_id = ? AND (? = '' OR rh.card_version = ?)
                    ORDER BY rr.created_at DESC LIMIT 100
                    """,
                    (asset_key, target_version, target_version),
                ).fetchall()
                runs = [
                    {**dict(row), "reasons": _load(row["reasons_json"], [])}
                    for row in run_rows
                ]
                for item in runs:
                    item.pop("reasons_json", None)
            feedback_rows = connection.execute(
                """
                SELECT * FROM ai_asset_feedback
                WHERE asset_type = ? AND asset_key = ? AND (? = '' OR version = ?)
                ORDER BY created_at DESC
                """,
                (asset_type, asset_key, target_version, target_version),
            ).fetchall()
        return {
            "asset": {"asset_type": asset_type, "asset_key": asset_key, "version": target_version},
            "sources": sources,
            "relations": detail["relations"],
            "runs": runs,
            "feedback": [dict(row) for row in feedback_rows],
            "runtime_snapshot_available": bool(runs),
        }

    def list_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM ai_governance_audit ORDER BY created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [self._decode_audit(row) for row in rows]

    @staticmethod
    def _decode_audit(row: Mapping[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        result["details"] = _load(result.pop("details_json"), {})
        return result

    @staticmethod
    def _audit(
        connection: Any,
        action: str,
        actor: str,
        asset_type: str,
        asset_key: str,
        details: Mapping[str, Any],
        now: Optional[str] = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO ai_governance_audit (
                action, actor, asset_type, asset_key, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (action, actor, asset_type, asset_key, _dump(dict(details)), now or utc_now()),
        )
