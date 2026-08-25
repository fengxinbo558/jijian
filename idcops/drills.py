"""Interactive fault drills that drive the real platform and governance boundaries."""

from __future__ import annotations

import copy
import json
import secrets
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .models import utc_now
from .store import IncidentStore, _dump, _load


TERMINAL_STATUSES = {
    "resolved",
    "transferred",
    "evidence_insufficient",
    "operation_blocked",
    "false_positive",
    "terminated",
}
PLAYBACK_MODES = {"auto", "step", "next_human"}
DRILL_MODES = {"directed", "blind"}


def _run_id() -> str:
    return "DRL-" + uuid.uuid4().hex[:12].upper()


def _text(value: Any) -> str:
    return str(value or "").strip()


class DrillService:
    """Versioned, auditable drill runner with a separate hidden-answer table."""

    def __init__(
        self,
        store: IncidentStore,
        platform_ingestor: Callable[[Mapping[str, Any]], Dict[str, Any]],
        catalog_path: Optional[Path] = None,
        analysis_mode: str = "rules_only",
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        self.store = store
        self.platform_ingestor = platform_ingestor
        self.catalog_path = catalog_path or root / "data" / "drills" / "fault_catalog.json"
        self.analysis_mode = analysis_mode
        self._lock = threading.RLock()
        self._catalog_data = self._load_catalog()

    def _load_catalog(self) -> Dict[str, Any]:
        data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        scenarios = data.get("scenarios") if isinstance(data, Mapping) else None
        if not isinstance(scenarios, list):
            raise ValueError("故障演练目录缺少场景列表")
        ids = [_text(item.get("id")) for item in scenarios if isinstance(item, Mapping)]
        if len(ids) != 25 or len(set(ids)) != 25:
            raise ValueError("首批故障演练目录必须恰好包含25个唯一场景")
        return dict(data)

    def _scenario(self, scenario_id: str) -> Dict[str, Any]:
        for item in self._catalog_data["scenarios"]:
            if item.get("id") == scenario_id:
                return copy.deepcopy(dict(item))
        raise ValueError("故障演练场景不存在")

    def list_catalog(self, category: str = "") -> Dict[str, Any]:
        requested = _text(category)
        items = []
        for raw in self._catalog_data["scenarios"]:
            if requested and raw.get("category") != requested:
                continue
            items.append(
                {
                    key: copy.deepcopy(raw.get(key))
                    for key in (
                        "id",
                        "version",
                        "category",
                        "name",
                        "difficulty",
                        "needs_onsite",
                        "owner_team",
                        "severity",
                        "visible_symptom",
                        "source_platforms",
                    )
                }
            )
        return {
            "schema_version": self._catalog_data.get("schema_version"),
            "categories": copy.deepcopy(self._catalog_data.get("categories", [])),
            "items": items,
            "count": len(items),
        }

    def start(self, payload: Mapping[str, Any], actor: str, role: str) -> Dict[str, Any]:
        mode = _text(payload.get("mode") or "directed")
        if mode not in DRILL_MODES:
            raise ValueError("演练模式必须是 directed 或 blind")
        playback = _text(payload.get("playback_mode") or "auto")
        if playback not in PLAYBACK_MODES:
            raise ValueError("播放方式必须是 auto、step 或 next_human")
        category = _text(payload.get("category"))
        if mode == "directed":
            scenario = self._scenario(_text(payload.get("scenario_id")))
            category = str(scenario["category"])
        else:
            candidates = [
                item
                for item in self._catalog_data["scenarios"]
                if not category or item.get("category") == category
            ]
            if not candidates:
                raise ValueError("所选盲测范围没有故障场景")
            scenario = copy.deepcopy(dict(secrets.choice(candidates)))
            category = str(scenario["category"])

        run_id = _run_id()
        now = utc_now()
        display_name = (
            str(scenario["name"])
            if mode == "directed"
            else f"{self._category_name(category)}盲测"
        )
        first_step = self._first_step_id(scenario)
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO drill_runs (
                    id, mode, category, display_name, catalog_version,
                    playback_mode, analysis_mode, status, current_step_id,
                    logical_time, incident_ids_json, location_json,
                    impact_path_json, final_diagnosis, final_status, score_json,
                    started_by, started_role, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, 0, '[]', ?, ?, '', '',
                          '{}', ?, ?, ?, ?, '')
                """,
                (
                    run_id,
                    mode,
                    category,
                    display_name,
                    str(scenario.get("version") or "1.0.0"),
                    playback,
                    self.analysis_mode,
                    first_step,
                    _dump(scenario.get("location") or {}),
                    _dump(scenario.get("impact_path") or {}),
                    actor,
                    role,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO drill_run_secrets (run_id, scenario_id, truth_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(scenario["id"]),
                    _dump(scenario.get("hidden_truth") or {}),
                    now,
                ),
            )
        self._record_step(
            run_id,
            "run-start",
            "system",
            "completed",
            actor,
            "演练已创建，隐藏答案未进入分析链",
            {"mode": mode, "playback_mode": playback, "category": category},
        )
        if bool(payload.get("autostart", True)):
            command = "step" if playback == "step" else "next_human"
            return self.advance(run_id, command, actor)
        return self.get(run_id, reveal=False)

    def _category_name(self, category: str) -> str:
        for item in self._catalog_data.get("categories", []):
            if item.get("id") == category:
                return str(item.get("name") or category)
        return category

    def _secret(self, run_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM drill_run_secrets WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ValueError("演练隐藏记录不存在")
        value = dict(row)
        value["truth"] = _load(value.pop("truth_json", ""), {})
        return value

    def _scenario_for_run(self, run_id: str) -> Dict[str, Any]:
        return self._scenario(str(self._secret(run_id)["scenario_id"]))

    @staticmethod
    def _decode_run(row: Any) -> Dict[str, Any]:
        value = dict(row)
        for raw, public, fallback in (
            ("incident_ids_json", "incident_ids", []),
            ("location_json", "location", {}),
            ("impact_path_json", "impact_path", {}),
            ("score_json", "score", {}),
        ):
            value[public] = _load(value.pop(raw, ""), fallback)
        value["logical_time"] = int(value.get("logical_time") or 0)
        return value

    def _base_run(self, run_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM drill_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ValueError("故障演练运行不存在")
        return self._decode_run(row)

    def list_runs(self, limit: int = 100) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM drill_runs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [self._project_run(self._decode_run(row), reveal=False) for row in rows]

    def get(self, run_id: str, reveal: bool = False) -> Dict[str, Any]:
        return self._project_run(self._base_run(run_id), reveal=reveal)

    def _project_run(self, run: Mapping[str, Any], reveal: bool) -> Dict[str, Any]:
        value = copy.deepcopy(dict(run))
        value["steps"] = self._steps(str(value["id"]))
        scenario = self._scenario_for_run(str(value["id"]))
        if value.get("status") == "waiting_human":
            value["current_checkpoint"] = self._checkpoint(
                scenario, str(value.get("current_step_id") or "")
            )
        else:
            value["current_checkpoint"] = None
        terminal = str(value.get("status")) in TERMINAL_STATUSES
        if value.get("mode") == "directed":
            value["scenario"] = {
                "id": scenario["id"],
                "name": scenario["name"],
                "visible_symptom": scenario["visible_symptom"],
            }
        else:
            value["scenario"] = {
                "id": "",
                "name": "盲测场景（运行中隐藏）" if not terminal else "盲测已结束",
                "visible_symptom": scenario["visible_symptom"],
            }
            if not reveal:
                value["final_diagnosis"] = ""
                if isinstance(value.get("score"), dict):
                    value["score"].pop("actual_diagnosis", None)
                    value["score"].pop("correct_owner_team", None)
        value["truth_reveal_available"] = terminal
        if reveal and terminal:
            secret = self._secret(str(value["id"]))
            value["hidden_truth"] = secret["truth"]
            value["scenario"]["id"] = secret["scenario_id"]
            value["scenario"]["name"] = scenario["name"]
        return value

    def _steps(self, run_id: str) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM drill_steps WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["details"] = _load(value.pop("details_json", ""), {})
            result.append(value)
        return result

    def _record_step(
        self,
        run_id: str,
        step_id: str,
        step_type: str,
        status: str,
        actor: str,
        summary: str,
        details: Mapping[str, Any],
        incident_id: str = "",
    ) -> None:
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO drill_steps (
                    run_id, step_id, step_type, status, actor, summary,
                    details_json, incident_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    step_id,
                    step_type,
                    status,
                    actor,
                    summary,
                    _dump(dict(details)),
                    incident_id,
                    utc_now(),
                ),
            )

    def _first_step_id(self, _scenario: Mapping[str, Any]) -> str:
        return "signal-primary"

    def _signal_step_ids(self, scenario: Mapping[str, Any]) -> list:
        return ["signal-primary"] + [
            f"signal-support-{index}"
            for index, _item in enumerate(scenario.get("supporting_signals") or [], start=1)
        ]

    def _initial_checkpoint_id(self, scenario: Mapping[str, Any]) -> str:
        kind = str(scenario.get("workflow_kind") or "generic")
        if kind in {
            "network_optical_module",
            "network_fiber",
            "network_port_hardware",
            "network_config",
        }:
            return "network-check-config"
        return "checkpoint-action"

    def _next_after_signal(self, scenario: Mapping[str, Any], step_id: str) -> str:
        signal_ids = self._signal_step_ids(scenario)
        if step_id in signal_ids:
            index = signal_ids.index(step_id)
            if index + 1 < len(signal_ids):
                return signal_ids[index + 1]
            return self._initial_checkpoint_id(scenario)
        if step_id == "signal-recovery":
            return "checkpoint-verify"
        raise ValueError("未知信号步骤")

    def _checkpoint(self, scenario: Mapping[str, Any], step_id: str) -> Dict[str, Any]:
        action = scenario.get("operator_check") or {}
        definitions = {
            "checkpoint-action": {
                "title": str(action.get("action") or "执行建议检查"),
                "prompt": str(action.get("prompt") or "请执行检查并反馈。"),
                "actions": [
                    {"id": "perform_action", "label": str(action.get("action") or "执行建议检查")},
                    {"id": "cannot_execute", "label": "当前无法执行"},
                    {"id": "no_improvement", "label": "已执行但没有改善"},
                ],
            },
            "network-check-config": {
                "title": "先核对端口服务与配置",
                "prompt": "由网络组执行只读查询；不要直接假定是模块或线缆。",
                "actions": [
                    {"id": "query_config", "label": "查询本端/对端端口配置"},
                    {"id": "cannot_query", "label": "平台无权限或无法查询"},
                ],
            },
            "network-replace-module": {
                "title": "更换模块并观察",
                "prompt": "配置正常，完成身份与许可确认后更换模块。",
                "actions": [
                    {"id": "replace_module", "label": "更换模块并复测"},
                    {"id": "cannot_replace", "label": "当前无操作许可"},
                ],
            },
            "network-measure-optics": {
                "title": "测量本端与对端光功率",
                "prompt": "模块更换无效，继续用光功率事实缩小范围。",
                "actions": [
                    {"id": "measure_optics", "label": "测量本端、线路与对端"},
                    {"id": "cannot_measure", "label": "当前没有测量条件"},
                ],
            },
            "network-replace-cable": {
                "title": "更换或整理线路",
                "prompt": "测量结果指向线路衰耗，完成许可后处理线路。",
                "actions": [
                    {"id": "replace_cable", "label": "更换线路并复测"},
                    {"id": "cannot_replace_cable", "label": "当前无法更换线路"},
                ],
            },
            "network-migrate-port": {
                "title": "端口交叉验证",
                "prompt": "模块和线路未证明故障，在网络组许可下迁移备用端口。",
                "actions": [
                    {"id": "migrate_port", "label": "迁移备用端口并复测"},
                    {"id": "cannot_migrate", "label": "当前无法迁移端口"},
                ],
            },
            "checkpoint-verify": {
                "title": "恢复验证",
                "prompt": "监控恢复不等于业务恢复，请完成业务验证。",
                "actions": [
                    {"id": "business_ok", "label": "监控与业务均已恢复"},
                    {"id": "business_not_ok", "label": "监控恢复但业务仍异常"},
                    {"id": "cannot_verify", "label": "暂时无法完成业务验证"},
                ],
            },
        }
        if step_id not in definitions:
            raise ValueError("当前演练没有可反馈的人工节点")
        return {"step_id": step_id, **definitions[step_id]}

    def advance(self, run_id: str, command: str, actor: str) -> Dict[str, Any]:
        normalized = _text(command or "step")
        if normalized not in {"step", "auto", "next_human"}:
            raise ValueError("推进命令必须是 step、auto 或 next_human")
        with self._lock:
            run = self._base_run(run_id)
            if run["status"] in TERMINAL_STATUSES:
                return self.get(run_id, reveal=False)
            if run["status"] == "waiting_human":
                return self.get(run_id, reveal=False)
            scenario = self._scenario_for_run(run_id)
            max_steps = 1 if normalized == "step" else 50
            for _index in range(max_steps):
                run = self._base_run(run_id)
                step_id = str(run["current_step_id"])
                if step_id.startswith("signal-"):
                    self._emit_signal(run, scenario, step_id, actor)
                    next_id = self._next_after_signal(scenario, step_id)
                    next_status = "waiting_human" if next_id.startswith("checkpoint-") or next_id.startswith("network-") else "running"
                    self._update_run(run_id, status=next_status, current_step_id=next_id, logical_increment=10)
                    if normalized == "step" or next_status == "waiting_human":
                        break
                    continue
                self._update_run(run_id, status="waiting_human")
                break
            return self.get(run_id, reveal=False)

    def _signal_definition(
        self, scenario: Mapping[str, Any], step_id: str
    ) -> Dict[str, Any]:
        if step_id == "signal-primary":
            return copy.deepcopy(dict(scenario["primary_signal"]))
        if step_id.startswith("signal-support-"):
            index = int(step_id.rsplit("-", 1)[1]) - 1
            return copy.deepcopy(dict((scenario.get("supporting_signals") or [])[index]))
        if step_id == "signal-recovery":
            primary = copy.deepcopy(dict(scenario["primary_signal"]))
            primary["severity"] = "info"
            primary["summary"] = f"恢复信号：{scenario['visible_symptom']}"
            primary["message"] = "monitoring signal returned to normal; service validation required"
            primary["lifecycle_status"] = "recovered"
            return primary
        raise ValueError("未知信号步骤")

    def _emit_signal(
        self,
        run: Mapping[str, Any],
        scenario: Mapping[str, Any],
        step_id: str,
        actor: str,
    ) -> None:
        definition = self._signal_definition(scenario, step_id)
        entity = dict(definition.get("entity") or {})
        event = {
            "source_system": definition["source_system"],
            "source_event_id": f"{run['id']}-{step_id}",
            "occurred_at": utc_now(),
            "site": scenario.get("location", {}).get("site") or "DRILL",
            "incident_key": f"DRILL-INCIDENT-{run['id']}",
            "entity": entity,
            "signal_type": definition["signal_type"],
            "severity": definition.get("severity") or scenario.get("severity") or "warning",
            "summary": definition["summary"],
            "raw_payload": {
                "message": definition.get("message") or definition["summary"],
                "drill_run_id": run["id"],
                "lifecycle_status": definition.get("lifecycle_status") or "firing",
            },
            "scenario_id": f"drill-run-{run['id']}",
            "drill_run_id": run["id"],
            "lifecycle_status": definition.get("lifecycle_status") or "firing",
        }
        result = self.platform_ingestor(event)
        incident = result.get("incident") or {}
        incident_id = _text(incident.get("id"))
        alert = result.get("governance", {}).get("alert", {}) if isinstance(result.get("governance"), Mapping) else {}
        self._append_incident(str(run["id"]), incident_id)
        self._record_step(
            str(run["id"]),
            step_id,
            "platform_signal",
            "completed",
            actor,
            str(definition["summary"]),
            {
                "source_system": definition["source_system"],
                "source_event_id": event["source_event_id"],
                "signal_type": definition["signal_type"],
                "integration_event_id": (result.get("event") or {}).get("id"),
                "governance_decision": result.get("governance", {}).get("decision") if isinstance(result.get("governance"), Mapping) else "",
                "alert_id": alert.get("id"),
                "analysis_mode": run["analysis_mode"],
            },
            incident_id,
        )

    def _append_incident(self, run_id: str, incident_id: str) -> None:
        if not incident_id:
            return
        run = self._base_run(run_id)
        values = list(run.get("incident_ids") or [])
        if incident_id not in values:
            values.append(incident_id)
            with self.store.connect() as connection:
                connection.execute(
                    "UPDATE drill_runs SET incident_ids_json = ?, updated_at = ? WHERE id = ?",
                    (_dump(values), utc_now(), run_id),
                )

    def feedback(
        self, run_id: str, action_id: str, notes: str, actor: str
    ) -> Dict[str, Any]:
        with self._lock:
            run = self._base_run(run_id)
            if run["status"] != "waiting_human":
                raise ValueError("当前演练没有等待人工反馈")
            scenario = self._scenario_for_run(run_id)
            step_id = str(run["current_step_id"])
            checkpoint = self._checkpoint(scenario, step_id)
            allowed = {item["id"] for item in checkpoint["actions"]}
            if action_id not in allowed:
                raise ValueError("当前人工节点不支持该动作")
            outcome = self._action_outcome(scenario, step_id, action_id)
            self._emit_feedback_event(run, scenario, step_id, action_id, outcome, notes, actor)
            self._record_step(
                run_id,
                step_id,
                "human_action",
                "completed",
                actor,
                outcome["summary"],
                {
                    "action_id": action_id,
                    "notes": _text(notes),
                    "simulated_observation": outcome["observation"],
                    "next_step_id": outcome.get("next_step_id") or "",
                    "responsibility_boundary": True,
                },
            )
            final_status = _text(outcome.get("final_status"))
            if final_status:
                self._finish(
                    run_id,
                    final_status,
                    _text(outcome.get("diagnosis")),
                    actor,
                )
                return self.get(run_id, reveal=False)
            self._update_run(
                run_id,
                status="running",
                current_step_id=str(outcome["next_step_id"]),
                logical_increment=10,
            )
            playback = str(run.get("playback_mode") or "auto")
            if playback in {"auto", "next_human"}:
                return self.advance(run_id, "next_human", actor)
            return self.get(run_id, reveal=False)

    def _emit_feedback_event(
        self,
        run: Mapping[str, Any],
        scenario: Mapping[str, Any],
        step_id: str,
        action_id: str,
        outcome: Mapping[str, Any],
        notes: str,
        actor: str,
    ) -> None:
        source = "network_nms" if action_id in {"query_config", "measure_optics"} else "onsite_feedback"
        location = scenario.get("location") or {}
        primary_entity = scenario.get("primary_signal", {}).get("entity") or {}
        event = {
            "source_system": source,
            "source_event_id": f"{run['id']}-{step_id}-{action_id}",
            "occurred_at": utc_now(),
            "site": location.get("site") or "DRILL",
            "incident_key": f"DRILL-INCIDENT-{run['id']}",
            "entity": copy.deepcopy(dict(primary_entity)),
            "signal_type": f"drill_observation_{action_id}",
            "severity": "info",
            "summary": str(outcome["summary"]),
            "raw_payload": {
                "observation": outcome["observation"],
                "operator_notes": _text(notes),
                "actor": actor,
                "drill_run_id": run["id"],
            },
            "scenario_id": f"drill-run-{run['id']}",
            "drill_run_id": run["id"],
        }
        self.platform_ingestor(event)

    def _action_outcome(
        self, scenario: Mapping[str, Any], step_id: str, action_id: str
    ) -> Dict[str, Any]:
        truth = scenario.get("hidden_truth") or {}
        diagnosis = str(truth.get("diagnosis") or "")
        if action_id.startswith("cannot_"):
            return {
                "summary": "当前无法完成该人工步骤，演练以证据不足结束",
                "observation": "缺少权限、人员、工具或操作条件",
                "final_status": "evidence_insufficient",
                "diagnosis": "",
            }
        if action_id == "no_improvement":
            return {
                "summary": "已完成建议动作但没有改善，转交专业组继续调查",
                "observation": "监控和业务状态均未恢复",
                "final_status": "transferred",
                "diagnosis": "",
            }
        if step_id == "checkpoint-action" and action_id == "perform_action":
            return {
                "summary": "建议动作已完成，监控侧出现恢复信号",
                "observation": "对应检查或处置完成，等待监控与业务双重验证",
                "next_step_id": "signal-recovery",
                "diagnosis": diagnosis,
            }
        if step_id == "network-check-config" and action_id == "query_config":
            if diagnosis == "network_configuration_mismatch":
                return {
                    "summary": "发现本端与对端聚合配置不一致，网络组已修正",
                    "observation": "partner key mismatch；修正后LACP成员恢复",
                    "next_step_id": "signal-recovery",
                    "diagnosis": diagnosis,
                }
            return {
                "summary": "端口服务和配置一致，继续检查物理路径",
                "observation": "本端/对端配置一致，协议状态无阻断项",
                "next_step_id": "network-replace-module",
            }
        if step_id == "network-replace-module" and action_id == "replace_module":
            if diagnosis == "optical_module_failure":
                return {
                    "summary": "更换模块后链路稳定，进入恢复验证",
                    "observation": "抖动停止，CRC不再增长",
                    "next_step_id": "signal-recovery",
                    "diagnosis": diagnosis,
                }
            return {
                "summary": "更换模块后仍然抖动，模块故障候选被削弱",
                "observation": "新模块下故障现象保持不变",
                "next_step_id": "network-measure-optics",
            }
        if step_id == "network-measure-optics" and action_id == "measure_optics":
            if diagnosis == "fiber_attenuation":
                return {
                    "summary": "测得线路衰耗异常，继续处理光纤",
                    "observation": "本端模块发光正常，对端接收功率低于阈值",
                    "next_step_id": "network-replace-cable",
                }
            return {
                "summary": "本端、线路和对端光功率正常，继续验证端口硬件",
                "observation": "各测量点均处于场景正常范围",
                "next_step_id": "network-migrate-port",
            }
        if step_id == "network-replace-cable" and action_id == "replace_cable":
            if diagnosis == "fiber_attenuation":
                return {
                    "summary": "更换线路后链路稳定，进入恢复验证",
                    "observation": "光功率恢复且端口不再抖动",
                    "next_step_id": "signal-recovery",
                    "diagnosis": diagnosis,
                }
            return {
                "summary": "更换线路没有改善，继续验证交换机端口",
                "observation": "新线路下CRC和抖动仍持续",
                "next_step_id": "network-migrate-port",
            }
        if step_id == "network-migrate-port" and action_id == "migrate_port":
            if diagnosis == "switch_port_hardware_failure":
                return {
                    "summary": "迁移备用端口后恢复，原端口硬件故障得到支持",
                    "observation": "相同模块和线路在备用端口稳定",
                    "next_step_id": "signal-recovery",
                    "diagnosis": diagnosis,
                }
            return {
                "summary": "物理路径交叉验证仍无法解释故障，升级网络组深入处理",
                "observation": "模块、线路和备用端口均未使业务恢复",
                "final_status": "transferred",
                "diagnosis": "",
            }
        if step_id == "checkpoint-verify":
            if action_id == "business_ok":
                return {
                    "summary": "监控和业务均已验证恢复",
                    "observation": "平台恢复信号与人工业务验证一致",
                    "final_status": "resolved",
                    "diagnosis": diagnosis,
                }
            if action_id == "business_not_ok":
                return {
                    "summary": "监控恢复但业务仍异常，转专业组继续调查",
                    "observation": "监控指标正常，业务验证失败",
                    "final_status": "transferred",
                    "diagnosis": diagnosis,
                }
            if action_id == "cannot_verify":
                return {
                    "summary": "暂时无法完成业务验证，事故不能关闭",
                    "observation": "缺少业务负责人确认或验证条件",
                    "final_status": "evidence_insufficient",
                    "diagnosis": diagnosis,
                }
        raise ValueError("当前动作没有定义演练结果")

    def _update_run(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        current_step_id: Optional[str] = None,
        logical_increment: int = 0,
    ) -> None:
        run = self._base_run(run_id)
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE drill_runs SET status = ?, current_step_id = ?,
                    logical_time = ?, updated_at = ? WHERE id = ?
                """,
                (
                    status or run["status"],
                    current_step_id if current_step_id is not None else run["current_step_id"],
                    int(run["logical_time"]) + int(logical_increment),
                    utc_now(),
                    run_id,
                ),
            )

    def _finish(
        self, run_id: str, final_status: str, diagnosis: str, actor: str
    ) -> None:
        if final_status not in TERMINAL_STATUSES:
            raise ValueError("不支持的演练结束状态")
        score = self._score(run_id, final_status, diagnosis)
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE drill_runs SET status = ?, current_step_id = '',
                    final_status = ?, final_diagnosis = ?, score_json = ?,
                    completed_at = ?, updated_at = ? WHERE id = ?
                """,
                (
                    final_status,
                    final_status,
                    diagnosis,
                    _dump(score),
                    now,
                    now,
                    run_id,
                ),
            )
        self._record_step(
            run_id,
            "run-finish",
            "system",
            final_status,
            actor,
            f"演练结束：{final_status}",
            {"score": score, "answer_was_isolated_until_finish": True},
        )

    def _score(self, run_id: str, final_status: str, diagnosis: str) -> Dict[str, Any]:
        secret = self._secret(run_id)
        truth = secret["truth"]
        acceptable = set(truth.get("acceptable_diagnoses") or [])
        steps = self._steps(run_id)
        incident_ids = self._base_run(run_id).get("incident_ids") or []
        return {
            "diagnosis_match": bool(diagnosis and diagnosis in acceptable),
            "actual_diagnosis": diagnosis,
            "final_status": final_status,
            "correct_owner_team": truth.get("owner_team"),
            "platform_signal_count": sum(1 for item in steps if item["step_type"] == "platform_signal"),
            "human_action_count": sum(1 for item in steps if item["step_type"] == "human_action"),
            "incident_count": len(incident_ids),
            "unsafe_action_count": 0,
            "answer_leak_count": 0,
            "diagnosis_basis": "deterministic_signals_plus_recorded_human_feedback",
            "production_accuracy_claimed": False,
        }

    def terminate(self, run_id: str, reason: str, actor: str) -> Dict[str, Any]:
        run = self._base_run(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return self.get(run_id, reveal=False)
        if len(_text(reason)) < 3:
            raise ValueError("终止演练必须填写原因")
        self._record_step(
            run_id,
            "run-terminated",
            "human_action",
            "terminated",
            actor,
            "演练被人工终止",
            {"reason": _text(reason)},
        )
        self._finish(run_id, "terminated", "", actor)
        return self.get(run_id, reveal=False)
