"""Append-only record of active Agent investigation rounds."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Mapping, Optional

from .models import utc_now
from .store import IncidentStore, _dump, _load


class AgentTraceRecorder:
    def __init__(self, store: IncidentStore) -> None:
        self.store = store

    def start(
        self,
        incident_id: str,
        mode: str,
        model_provider: str,
        model_name: str,
        prompt_version: str,
        max_rounds: int,
    ) -> str:
        run_id = "AGT-" + uuid.uuid4().hex[:12].upper()
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    id, incident_id, mode, status, model_provider, model_name,
                    prompt_version, max_rounds, stop_reason, summary_json,
                    started_at, completed_at
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, '', '{}', ?, '')
                """,
                (
                    run_id,
                    incident_id,
                    mode,
                    model_provider,
                    model_name,
                    prompt_version,
                    max_rounds,
                    now,
                ),
            )
        return run_id

    def add_step(self, run_id: str, round_no: int, step: Mapping[str, Any]) -> None:
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_steps (
                    run_id, round_no, step_type, status, rationale, input_json,
                    tool_name, tool_args_json, tool_output_json, evidence_ids_json,
                    hypotheses_before_json, hypotheses_after_json, validation_json,
                    model_output_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    round_no,
                    str(step.get("step_type") or "investigation"),
                    str(step.get("status") or "completed"),
                    str(step.get("rationale") or ""),
                    _dump(step.get("input", {})),
                    str(step.get("tool_name") or ""),
                    _dump(step.get("tool_args", {})),
                    _dump(step.get("tool_output", {})),
                    _dump(step.get("evidence_ids", [])),
                    _dump(step.get("hypotheses_before", [])),
                    _dump(step.get("hypotheses_after", [])),
                    _dump(step.get("validation", {})),
                    _dump(step.get("model_output", {})),
                    utc_now(),
                ),
            )

    def finish(
        self, run_id: str, status: str, stop_reason: str, summary: Mapping[str, Any]
    ) -> Dict[str, Any]:
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE agent_runs SET status = ?, stop_reason = ?, summary_json = ?,
                    completed_at = ? WHERE id = ?
                """,
                (status, stop_reason, _dump(dict(summary)), utc_now(), run_id),
            )
        result = self.get(run_id)
        assert result is not None
        return result

    def list(self, incident_id: str = "", limit: int = 100) -> list:
        clauses = []
        parameters: list = []
        if incident_id:
            clauses.append("incident_id = ?")
            parameters.append(incident_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM agent_runs{where} ORDER BY started_at DESC LIMIT ?",
                (*parameters, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._decode_run(row) for row in rows]

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            run = connection.execute(
                "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            steps = connection.execute(
                "SELECT * FROM agent_steps WHERE run_id = ? ORDER BY round_no", (run_id,)
            ).fetchall()
        result = self._decode_run(run)
        result["steps"] = [self._decode_step(row) for row in steps]
        return result

    @staticmethod
    def _decode_run(row: Any) -> Dict[str, Any]:
        value = dict(row)
        value["summary"] = _load(value.pop("summary_json", ""), {})
        return value

    @staticmethod
    def _decode_step(row: Any) -> Dict[str, Any]:
        value = dict(row)
        for raw, public, fallback in (
            ("input_json", "input", {}),
            ("tool_args_json", "tool_args", {}),
            ("tool_output_json", "tool_output", {}),
            ("evidence_ids_json", "evidence_ids", []),
            ("hypotheses_before_json", "hypotheses_before", []),
            ("hypotheses_after_json", "hypotheses_after", []),
            ("validation_json", "validation", {}),
            ("model_output_json", "model_output", {}),
        ):
            value[public] = _load(value.pop(raw), fallback)
        return value
