"""Persist and expose each auditable retrieval/model pipeline run."""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any, Dict, Mapping, Optional

from .assets import AssetRegistry
from .constraints import ConstraintRegistry
from .models import utc_now
from .store import IncidentStore, _dump, _load


class RagTraceRecorder:
    def __init__(
        self,
        store: IncidentStore,
        assets: AssetRegistry,
        constraints: Optional[ConstraintRegistry] = None,
    ) -> None:
        self.store = store
        self.assets = assets
        self.constraints = constraints

    def record(self, incident_id: str, investigation: Mapping[str, Any]) -> str:
        run_id = "RAG-" + uuid.uuid4().hex[:12].upper()
        now = utc_now()
        retrieval = investigation.get("knowledge_retrieval", {})
        cards = list(retrieval.get("cards", []))
        knowledge_material = "|".join(
            f"{item.get('id')}@{item.get('version')}" for item in cards
        )
        knowledge_version = (
            hashlib.sha256(knowledge_material.encode("utf-8")).hexdigest()[:12]
            if knowledge_material
            else "none"
        )
        prompt = self.assets.get_published_prompt_version("hypothesis") or {}
        prompt_version = str(prompt.get("version") or "not-configured")
        model_trace = investigation.get("model_trace", {})
        model_provider = str(
            model_trace.get("model")
            or os.getenv("IDCAI_MODEL", "")
            or "not-enabled"
        )
        mode = str(investigation.get("mode") or "rules_only")
        steps = self._steps(investigation, prompt_version, model_provider)
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO rag_runs (
                    id, incident_id, mode, knowledge_version, prompt_version,
                    model_provider, status, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?)
                """,
                (
                    run_id,
                    incident_id,
                    mode,
                    knowledge_version,
                    prompt_version,
                    model_provider,
                    now,
                    now,
                ),
            )
            for index, step in enumerate(steps, start=1):
                connection.execute(
                    """
                    INSERT INTO rag_steps (
                        run_id, step_order, step_type, status, input_json,
                        output_json, message, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        index,
                        step["type"],
                        step["status"],
                        _dump(step.get("input", {})),
                        _dump(step.get("output", {})),
                        step.get("message", ""),
                        now,
                    ),
                )
            for rank, card in enumerate(cards, start=1):
                retrieval_details = dict(card.get("retrieval", {}))
                connection.execute(
                    """
                    INSERT INTO rag_hits (
                        run_id, card_id, card_version, rank, score,
                        reasons_json, retrieval_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(card.get("id", "")),
                        str(card.get("version", "")),
                        rank,
                        float(card.get("score", 0) or 0),
                        _dump(card.get("retrieval_reasons", [])),
                        _dump(retrieval_details),
                    ),
                )
            constraint_version = (
                self.constraints.published_version() if self.constraints is not None else "built-in"
            )
            connection.execute(
                """
                INSERT INTO ai_runtime_snapshots (
                    id, run_type, run_id, knowledge_version,
                    prompt_versions_json, constraint_versions_json,
                    model_json, capabilities_json, created_at
                ) VALUES (?, 'rag', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "SNAP-" + uuid.uuid4().hex[:12].upper(),
                    run_id,
                    knowledge_version,
                    _dump({"hypothesis": prompt_version}),
                    _dump({"investigation-policy": constraint_version}),
                    _dump({"provider": model_provider, "mode": mode}),
                    _dump(retrieval.get("capabilities", [])),
                    now,
                ),
            )
        return run_id

    @staticmethod
    def _steps(
        investigation: Mapping[str, Any], prompt_version: str, model_provider: str
    ) -> list:
        retrieval = investigation.get("knowledge_retrieval", {})
        model_trace = investigation.get("model_trace", {})
        model_ran = bool(model_trace)
        return [
            {
                "type": "raw_input",
                "status": "completed",
                "output": {"intake": investigation.get("intake", []), "evidence": investigation.get("evidence", [])},
                "message": "保存原始输入、来源和证据编号",
            },
            {
                "type": "facts",
                "status": "completed",
                "input": {"evidence_ids": [item.get("id") for item in investigation.get("evidence", [])]},
                "output": {"facts": investigation.get("extracted_facts", [])},
                "message": "从原文提取可回溯事实",
            },
            {
                "type": "retrieval_query",
                "status": "completed",
                "output": {
                    "query": retrieval.get("query", {}),
                    "capabilities": retrieval.get("capabilities", []),
                    "constraint_version": retrieval.get("constraint_version", "built-in"),
                    "policy": retrieval.get("policy", {}),
                },
                "message": "构造精确条件和本地向量检索内容",
            },
            {
                "type": "retrieval_hits",
                "status": "completed",
                "output": {"coverage": retrieval.get("coverage"), "cards": retrieval.get("cards", [])},
                "message": "合并规则、事实、关键词和向量命中",
            },
            {
                "type": "model_input",
                "status": "completed" if model_ran else "not_run",
                "output": {
                    "prompt_version": prompt_version,
                    "model_provider": model_provider,
                    "messages": model_trace.get("messages", []),
                },
                "message": "模型未启用" if not model_ran else "保存脱敏后的实际模型消息",
            },
            {
                "type": "model_output",
                "status": "completed" if model_ran else "not_run",
                "output": {"raw_response": model_trace.get("raw_response", "")},
                "message": "模型未启用" if not model_ran else "保存模型原始输出",
            },
            {
                "type": "validation",
                "status": "completed",
                "output": {
                    "model_validation": model_trace.get("validation", "not_run"),
                    "hard_guards": [
                        "evidence_id_exists",
                        "no_confirmed_without_tool",
                        "no_operation_permission",
                    ],
                },
                "message": "执行结构、证据引用和越权校验",
            },
            {
                "type": "final_result",
                "status": "completed",
                "output": {
                    "hypotheses": investigation.get("hypotheses", []),
                    "verification_plan": investigation.get("verification_plan", []),
                    "conclusion": investigation.get("conclusion", {}),
                },
                "message": "保存最终候选、反证和下一步验证",
            },
        ]

    def list(self, limit: int = 100) -> list:
        safe_limit = max(1, min(int(limit), 500))
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rag_runs ORDER BY created_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            run = connection.execute(
                "SELECT * FROM rag_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            steps = connection.execute(
                "SELECT * FROM rag_steps WHERE run_id = ? ORDER BY step_order", (run_id,)
            ).fetchall()
            hits = connection.execute(
                "SELECT * FROM rag_hits WHERE run_id = ? ORDER BY rank", (run_id,)
            ).fetchall()
            snapshot = connection.execute(
                "SELECT * FROM ai_runtime_snapshots WHERE run_id = ?", (run_id,)
            ).fetchone()
        result = dict(run)
        result["steps"] = [
            {
                "order": row["step_order"],
                "type": row["step_type"],
                "status": row["status"],
                "input": _load(row["input_json"], {}),
                "output": _load(row["output_json"], {}),
                "message": row["message"],
                "created_at": row["created_at"],
            }
            for row in steps
        ]
        result["hits"] = [
            {
                "card_id": row["card_id"],
                "card_version": row["card_version"],
                "rank": row["rank"],
                "score": row["score"],
                "reasons": _load(row["reasons_json"], []),
                "retrieval": _load(row["retrieval_json"], {}),
            }
            for row in hits
        ]
        if snapshot is not None:
            result["asset_snapshot"] = {
                **{
                    key: snapshot[key]
                    for key in snapshot.keys()
                    if key
                    not in {
                        "prompt_versions_json",
                        "constraint_versions_json",
                        "model_json",
                        "capabilities_json",
                    }
                },
                "prompt_versions": _load(snapshot["prompt_versions_json"], {}),
                "constraint_versions": _load(snapshot["constraint_versions_json"], {}),
                "model": _load(snapshot["model_json"], {}),
                "capabilities": _load(snapshot["capabilities_json"], []),
            }
        return result
