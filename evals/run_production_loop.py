#!/usr/bin/env python3
"""Exercise the minimum production loop with deterministic, auditable scenarios."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idcops.production import ProductionGovernance  # noqa: E402
from idcops.store import IncidentStore  # noqa: E402


class Scenario:
    def __init__(self, database: Path) -> None:
        self.incidents: List[Dict[str, Any]] = []

        def ingest(_source: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
            incident = {
                "id": f"INC-PL-{len(self.incidents) + 1:03d}",
                "status": "new",
                "summary": payload.get("summary", ""),
            }
            self.incidents.append(incident)
            return incident

        self.governance = ProductionGovernance(IncidentStore(str(database)), ingest)

    @staticmethod
    def alert(**overrides: Any) -> Dict[str, Any]:
        value = {
            "source_system": "network_nms",
            "source_event_id": "PL-EVENT-001",
            "site": "BJYZ",
            "entity": {"device_name": "HB-BJYZ-TOR-01", "device_type": "switch"},
            "signal_type": "link_down",
            "severity": "critical",
            "summary": "TOR上联端口中断",
            "raw_payload": {"message": "HundredGigE1/0/1 changed state to DOWN"},
        }
        value.update(overrides)
        return value


def check(name: str, actual: Any, expected: Any) -> Dict[str, Any]:
    return {"name": name, "actual": actual, "expected": expected, "passed": actual == expected}


def upstream_suppression(s: Scenario) -> List[Dict[str, Any]]:
    upstream = s.governance.ingest_alert(s.alert())
    downstream = s.governance.ingest_alert(
        s.alert(
            source_event_id="PL-EVENT-002",
            entity={"sn": "SERVER-001", "device_type": "server"},
            signal_type="host_unreachable",
            summary="服务器失联",
            upstream_entity_key="name:HB-BJYZ-TOR-01",
        )
    )
    return [
        check("下游生命周期", downstream["alert"]["lifecycle_status"], "suppressed"),
        check("下游不新建事故", downstream["incident_created"], False),
        check("复用上游事故", downstream["alert"]["incident_id"], upstream["alert"]["incident_id"]),
    ]


def flap_recovery(s: Scenario) -> List[Dict[str, Any]]:
    firing = s.governance.ingest_alert(s.alert())
    recovered = s.governance.ingest_alert(
        s.alert(source_event_id="PL-EVENT-REC", lifecycle_status="recovered", summary="端口恢复UP")
    )
    return [
        check("更新同一告警", recovered["alert"]["id"], firing["alert"]["id"]),
        check("恢复待验证", recovered["requires_service_validation"], True),
        check("事故未自动关闭", s.incidents[0]["status"], "new"),
    ]


def maintenance(s: Scenario) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    s.governance.create_maintenance_window(
        {
            "site": "BJYZ",
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
            "reason": "TOR版本升级",
        },
        "lead-a",
    )
    result = s.governance.ingest_alert(s.alert())
    return [
        check("维护静默", result["alert"]["lifecycle_status"], "silenced"),
        check("不创建事故", result["incident_created"], False),
        check("仍保存告警", len(s.governance.list_alerts()), 1),
    ]


def source_outage(s: Scenario) -> List[Dict[str, Any]]:
    health = s.governance.update_source_health(
        {
            "source_system": "otel_collector",
            "connection_status": "disconnected",
            "expected_entities": 1000,
            "reporting_entities": 0,
            "queue_depth": 240,
        }
    )
    return [
        check("识别采集链路问题", health["pipeline_problem"], True),
        check("覆盖率", health["coverage_percent"], 0.0),
        check("不伪造设备事故", len(s.incidents), 0),
    ]


def identity_conflict(s: Scenario, second_source: str = "onsite_scan", field_name: str = "rack_position") -> List[Dict[str, Any]]:
    first_value = "BJYZ-A-01-01" if field_name == "rack_position" else "SERVER-SN-001"
    second_value = "BJYZ-A-01-02" if field_name == "rack_position" else "SERVER-SN-009"
    s.governance.record_identity_assertion(
        {"entity_key": "asset:SERVER-001", "source_system": "oms_cmdb", "field_name": field_name, "field_value": first_value},
        "cmdb-sync",
    )
    result = s.governance.record_identity_assertion(
        {"entity_key": "asset:SERVER-001", "source_system": second_source, "field_name": field_name, "field_value": second_value},
        "source-sync",
    )
    conflicts = s.governance.list_identity_conflicts("open")
    return [
        check("生成冲突", len(conflicts), 1),
        check("保护字段阻止操作", result["operation_blocked"], True),
        check("不静默覆盖OMS值", conflicts[0]["authoritative_value"], first_value),
    ]


def change_candidate(s: Scenario) -> List[Dict[str, Any]]:
    result = s.governance.record_change(
        {"site": "BJYZ", "entity_key": "sn:SERVER-001", "change_type": "configuration", "summary": "调整网卡队列参数"},
        "sim-a",
    )
    return [
        check("变更已保存", len(s.governance.list_changes("BJYZ", "sn:SERVER-001")), 1),
        check("因果等级", result["causality"], "candidate_only"),
    ]


def environmental_coexistence(s: Scenario) -> List[Dict[str, Any]]:
    first = s.governance.ingest_alert(
        s.alert(source_system="dcim", entity={"asset_id": "ZONE-A", "device_type": "thermal_zone"}, signal_type="temperature_rising", summary="A区温度持续升高")
    )
    second = s.governance.ingest_alert(
        s.alert(source_system="dcim", source_event_id="PL-TEMP-2", entity={"asset_id": "SENSOR-99", "device_type": "temperature_sensor"}, signal_type="sensor_spike", summary="单点传感器瞬时高温")
    )
    return [
        check("不同实体不自动合并", first["alert"]["incident_id"] != second["alert"]["incident_id"], True),
        check("保留两个事故", len(s.incidents), 2),
    ]


def night_shift(s: Scenario) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    roster = s.governance.create_roster(
        {"site": "BJYZ", "team": "onsite", "person": "night-a", "shift_start": (now - timedelta(hours=1)).isoformat(), "shift_end": (now + timedelta(hours=7)).isoformat(), "escalation_person": "lead-on-call"},
        "lead-a",
    )
    incident = s.governance.ingest_alert(s.alert())["alert"]["incident_id"]
    assignment = s.governance.assign_incident(
        {"incident_id": incident, "assignee": "night-a", "team": "onsite", "priority": "p1"}, "sim-a"
    )
    acknowledged = s.governance.acknowledge_assignment(assignment["id"], "night-a")
    escalated = s.governance.escalate_assignment(assignment["id"], roster["escalation_person"], "lead-a")
    return [
        check("当前值班可查", len(s.governance.list_rosters(True)), 1),
        check("值班确认收到", acknowledged["status"], "acknowledged"),
        check("升级负责人", escalated["escalated_to"], "lead-on-call"),
    ]


def recurrence(s: Scenario) -> List[Dict[str, Any]]:
    first = s.governance.ingest_alert(s.alert())
    duplicate = s.governance.ingest_alert(s.alert(source_event_id="PL-DUP"))
    s.governance.ingest_alert(s.alert(source_event_id="PL-REC", lifecycle_status="recovered", summary="端口恢复"))
    recurrent = s.governance.ingest_alert(s.alert(source_event_id="PL-AGAIN", summary="端口再次中断"))
    return [
        check("持续期内去重", duplicate["alert"]["id"], first["alert"]["id"]),
        check("重复次数", duplicate["alert"]["occurrence_count"], 2),
        check("复发生成新告警", recurrent["alert"]["id"] != first["alert"]["id"], True),
        check("复发生成新事故", len(s.incidents), 2),
    ]


def recovery_validation(s: Scenario) -> List[Dict[str, Any]]:
    firing = s.governance.ingest_alert(s.alert())
    recovered = s.governance.ingest_alert(s.alert(source_event_id="PL-REC", lifecycle_status="recovered", summary="监控信号恢复"))
    return [
        check("要求业务验证", recovered["alert"]["requires_service_validation"], True),
        check("保留事故关联", recovered["alert"]["incident_id"], firing["alert"]["incident_id"]),
        check("事故仍未解决", s.incidents[0]["status"], "new"),
    ]


def independent_faults(s: Scenario) -> List[Dict[str, Any]]:
    network = s.governance.ingest_alert(s.alert())
    disk = s.governance.ingest_alert(
        s.alert(source_system="bmc_redfish", source_event_id="PL-DISK", entity={"sn": "SERVER-002", "device_type": "disk"}, signal_type="disk_failure", summary="物理磁盘故障")
    )
    return [
        check("事故ID不同", network["alert"]["incident_id"] != disk["alert"]["incident_id"], True),
        check("两个独立事故", len(s.incidents), 2),
    ]


HANDLERS: Dict[str, Callable[[Scenario], List[Dict[str, Any]]]] = {
    "PL-01": upstream_suppression,
    "PL-02": flap_recovery,
    "PL-03": maintenance,
    "PL-04": source_outage,
    "PL-05": identity_conflict,
    "PL-06": change_candidate,
    "PL-07": lambda scenario: identity_conflict(scenario, "bmc_redfish", "sn"),
    "PL-08": environmental_coexistence,
    "PL-09": night_shift,
    "PL-10": recurrence,
    "PL-11": recovery_validation,
    "PL-12": independent_faults,
}


def evaluate_case(case: Mapping[str, Any]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        scenario = Scenario(Path(directory) / "production-loop.db")
        checks = HANDLERS[str(case["id"])](scenario)
    return {**dict(case), "passed": all(item["passed"] for item in checks), "checks": checks}


def build_report(results: List[Dict[str, Any]], started_at: str) -> Tuple[Dict[str, Any], str]:
    passed = sum(1 for item in results if item["passed"])
    checks_total = sum(len(item["checks"]) for item in results)
    checks_passed = sum(1 for item in results for item_check in item["checks"] if item_check["passed"])
    report = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {"cases_passed": passed, "cases_total": len(results), "checks_passed": checks_passed, "checks_total": checks_total},
        "scope": "deterministic_governance_and_safety_baseline",
        "limitations": [
            "测试使用合成事件和临时数据库，不代表十万台真实生产负载。",
            "公开数据只能验证解析、去重和接口合同，不能证明客户现场定位准确率。",
            "需要真实 NMS、BMC、动环、OMS、日志与值班数据完成现场验收。",
        ],
        "results": results,
    }
    lines = [
        "# 最小生产闭环自测报告",
        "",
        f"- 场景通过：{passed}/{len(results)}",
        f"- 检查点通过：{checks_passed}/{checks_total}",
        "- 范围：告警治理、可信身份、责任链和安全边界基线",
        "- 重要限制：通过不等于已在十万台真实设备环境验证",
        "",
        "| 编号 | 场景 | 结果 | 验证重点 |",
        "|---|---|---|---|",
    ]
    for item in results:
        lines.append(f"| {item['id']} | {item['title']} | {'通过' if item['passed'] else '失败'} | {item['expected']} |")
    lines.extend(["", "## 逐项结果", ""])
    for item in results:
        lines.extend([f"### {item['id']} {item['title']}", ""])
        for item_check in item["checks"]:
            lines.append(f"- {'通过' if item_check['passed'] else '失败'}｜{item_check['name']}：实际 `{item_check['actual']}`，预期 `{item_check['expected']}`")
        lines.append("")
    return report, "\n".join(lines)


def main() -> int:
    source = json.loads((ROOT / "evals" / "production_loop_cases.json").read_text(encoding="utf-8"))
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    results = [evaluate_case(case) for case in source["cases"]]
    report, markdown = build_report(results, started_at)
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "production-loop-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (reports / "production-loop-report.md").write_text(markdown + "\n", encoding="utf-8")
    print("\n".join(markdown.splitlines()[:22]))
    return 0 if passed_all(results) else 1


def passed_all(results: List[Dict[str, Any]]) -> bool:
    return bool(results) and all(item["passed"] for item in results)


if __name__ == "__main__":
    raise SystemExit(main())
