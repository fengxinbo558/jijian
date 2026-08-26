"""Audited admin-only retrieval tests that never create incidents."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Dict, Mapping

from .constraints import ConstraintRegistry
from .knowledge import KnowledgeBase
from .models import utc_now
from .store import IncidentStore, _dump, _load


class RetrievalTestService:
    def __init__(
        self,
        store: IncidentStore,
        knowledge: KnowledgeBase,
        constraints: ConstraintRegistry,
    ) -> None:
        self.store = store
        self.knowledge = knowledge
        self.constraints = constraints

    def index_status(self) -> Dict[str, Any]:
        self.knowledge._load()
        material = "|".join(
            f"{card.get('id')}@{card.get('version')}" for card in self.knowledge.cards
        )
        policy = self.constraints.published_settings()
        return {
            "published_cards": len(self.knowledge.cards),
            "knowledge_version": hashlib.sha256(material.encode("utf-8")).hexdigest()[:12],
            "domains": self.knowledge.summary()["domains"],
            "vector_provider": self.knowledge.embedding_provider.provider_key,
            "vector_capability": self.knowledge.embedding_provider.capability,
            "vector_dimensions": self.knowledge.embedding_provider.dimensions,
            "vector_enabled": bool(policy["vector_assist_enabled"]),
            "index_mode": "runtime_rebuildable",
            "pretrained_semantic_model": False,
            "constraint_version": self.constraints.published_version(),
        }

    @staticmethod
    def _list(value: Any) -> list:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def run(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        rule_names = self._list(payload.get("rule_names"))
        fact_types = self._list(payload.get("fact_types"))
        domain = str(payload.get("domain") or "").strip()
        device_type = str(payload.get("device_type") or "unknown").strip()
        if not text and not rule_names and not fact_types:
            raise ValueError("请输入脱敏日志、规则或事实后再测试")
        policy = self.constraints.published_settings()
        matches = self.knowledge.search(
            rule_names=rule_names,
            fact_types=fact_types,
            text=text,
            device_type=device_type,
            limit=int(policy["retrieval_top_k"]),
        )
        if domain:
            matches = [item for item in matches if item["card"].get("domain") == domain]
        hits = [
            {
                "card_id": item["card"]["id"],
                "version": item["card"]["version"],
                "title": item["card"]["title"],
                "domain": item["card"]["domain"],
                "score": item["score"],
                "reasons": item["reasons"],
                "retrieval": item["retrieval"],
            }
            for item in matches
        ]
        index = self.index_status()
        run_id = "RET-" + uuid.uuid4().hex[:12].upper()
        now = utc_now()
        query = {
            "text_excerpt": text[:2000],
            "rule_names": rule_names,
            "fact_types": fact_types,
            "domain": domain,
            "device_type": device_type,
        }
        result = {
            "id": run_id,
            "coverage": "matched" if hits else "insufficient",
            "query": query,
            "hits": hits,
            "knowledge_version": index["knowledge_version"],
            "constraint_version": index["constraint_version"],
            "capabilities": [
                "rules",
                "facts",
                "terms",
                *(["local_feature_vector"] if policy["vector_assist_enabled"] else []),
            ],
            "production_incident_created": False,
            "created_at": now,
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO retrieval_test_runs (
                    id, actor, query_json, result_json, knowledge_version,
                    constraint_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    actor,
                    _dump(query),
                    _dump(result),
                    index["knowledge_version"],
                    index["constraint_version"],
                    now,
                ),
            )
        return result

    def list(self, limit: int = 100) -> list:
        safe_limit = max(1, min(int(limit), 500))
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM retrieval_test_runs
                ORDER BY created_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "actor": row["actor"],
                "query": _load(row["query_json"], {}),
                "result": _load(row["result_json"], {}),
                "knowledge_version": row["knowledge_version"],
                "constraint_version": row["constraint_version"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
