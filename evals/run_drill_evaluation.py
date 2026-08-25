#!/usr/bin/env python3
"""Run all interactive drill cases through the real simulated ingestion boundary."""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idcops.service import IncidentService  # noqa: E402
from idcops.store import IncidentStore  # noqa: E402


def check(name: str, actual: Any, expected: Any) -> Dict[str, Any]:
    return {"name": name, "actual": actual, "expected": expected, "passed": actual == expected}


def evaluate_case(case: Mapping[str, Any], global_expected: Mapping[str, Any]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        store = IncidentStore(str(Path(directory) / "drill-eval.db"))
        drills = IncidentService(store).drills
        run = drills.start(
            {
                "mode": "directed",
                "scenario_id": case["id"],
                "playback_mode": "auto",
            },
            "evaluation-admin",
            "ai_admin",
        )
        initial_json = json.dumps(run, ensure_ascii=False)
        checkpoints: List[str] = []
        guard = 0
        while run["status"] not in {
            "resolved", "transferred", "evidence_insufficient", "operation_blocked",
            "false_positive", "terminated",
        }:
            guard += 1
            if guard > 20:
                raise RuntimeError(f"{case['id']} 超过最大推进次数")
            if run["status"] == "running":
                run = drills.advance(run["id"], "next_human", "evaluation-admin")
                continue
            checkpoint = run.get("current_checkpoint") or {}
            checkpoints.append(str(checkpoint.get("step_id") or ""))
            actions = checkpoint.get("actions") or []
            if not actions:
                raise RuntimeError(f"{case['id']} 的人工节点没有可执行动作")
            run = drills.feedback(
                run["id"],
                str(actions[0]["id"]),
                "自动评测选择场景首选安全动作",
                "evaluation-operator",
            )

        revealed = drills.get(run["id"], reveal=True)
        platform_steps = [item for item in run["steps"] if item["step_type"] == "platform_signal"]
        checks = [
            check("故障分类", run["category"], case["category"]),
            check("结束状态", run["status"], global_expected["final_status"]),
            check("动态人工路径", checkpoints, case["expected_checkpoints"]),
            check("诊断命中", bool(run["score"].get("diagnosis_match")), True),
            check("危险自动动作", run["score"].get("unsafe_action_count"), global_expected["unsafe_action_count"]),
            check("答案泄露", run["score"].get("answer_leak_count"), global_expected["answer_leak_count"]),
            check("不声称生产准确率", run["score"].get("production_accuracy_claimed"), global_expected["production_accuracy_claimed"]),
            check("运行中无隐藏答案", "hidden_truth" in initial_json, False),
            check("完成后可揭示答案", bool(revealed.get("hidden_truth")), True),
            check("接入记录完整", all(item.get("details", {}).get("integration_event_id") for item in platform_steps), True),
            check("至少一个平台信号", len(platform_steps) >= int(global_expected["minimum_platform_signals"]), True),
            check("至少形成一个事故", len(run.get("incident_ids") or []) >= int(global_expected["minimum_incidents"]), True),
        ]
        return {
            "id": case["id"],
            "category": case["category"],
            "passed": all(item["passed"] for item in checks),
            "analysis_mode": run["analysis_mode"],
            "platform_signal_count": len(platform_steps),
            "human_action_count": run["score"].get("human_action_count", 0),
            "checkpoints": checkpoints,
            "checks": checks,
        }


def evaluate_blind_isolation(category: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        drills = IncidentService(IncidentStore(str(Path(directory) / "blind.db"))).drills
        run = drills.start(
            {"mode": "blind", "category": category, "autostart": False},
            "blind-admin",
            "ai_admin",
        )
        secret = drills._secret(run["id"])
        serialized = json.dumps(run, ensure_ascii=False)
        ended = drills.terminate(run["id"], "盲测隔离评测", "blind-admin")
        ended_serialized = json.dumps(ended, ensure_ascii=False)
        checks = [
            check("运行中场景ID隐藏", run["scenario"]["id"], ""),
            check("运行中无隐藏真相", "hidden_truth" in run, False),
            check("运行中不含真实场景ID", str(secret["scenario_id"]) in serialized, False),
            check("结束但未揭晓仍不含真实场景ID", str(secret["scenario_id"]) in ended_serialized, False),
            check("结束后允许授权揭晓", ended["truth_reveal_available"], True),
        ]
        return {"category": category, "passed": all(item["passed"] for item in checks), "checks": checks}


def write_report(results: List[Dict[str, Any]], blind_results: List[Dict[str, Any]]) -> Path:
    report = ROOT / "reports" / "drill-evaluation-report.md"
    passed = sum(1 for item in results if item["passed"])
    checks = [check for item in results for check in item["checks"]]
    check_passed = sum(1 for item in checks if item["passed"])
    modes = Counter(item["analysis_mode"] for item in results)
    lines = [
        "# 交互式故障演练评测报告",
        "",
        f"生成时间：{datetime.now(timezone.utc).isoformat()}",
        "",
        "## 结论",
        "",
        f"- 场景通过：{passed}/{len(results)}",
        f"- 检查点通过：{check_passed}/{len(checks)}",
        f"- 五类盲测隔离：{sum(1 for item in blind_results if item['passed'])}/{len(blind_results)}",
        f"- 分析模式：{', '.join(f'{key}={value}' for key, value in sorted(modes.items()))}",
        "- 本报告只证明模拟闭环、分支和安全边界按预期运行，不代表真实生产定位准确率。",
        "",
        "## 逐场景结果",
        "",
        "| 场景 | 分类 | 结果 | 平台信号 | 人工节点 | 实际路径 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in results:
        lines.append(
            f"| `{item['id']}` | {item['category']} | {'通过' if item['passed'] else '失败'} | "
            f"{item['platform_signal_count']} | {item['human_action_count']} | {' → '.join(item['checkpoints'])} |"
        )
    failures = [
        (item["id"], check_item)
        for item in results
        for check_item in item["checks"]
        if not check_item["passed"]
    ]
    lines.extend(["", "## 失败明细", ""])
    if not failures:
        lines.append("无。")
    else:
        for case_id, item in failures:
            lines.append(f"- `{case_id}` {item['name']}：实际 `{item['actual']}`，期望 `{item['expected']}`")
    lines.extend(["", "## 盲测隔离", ""])
    for item in blind_results:
        lines.append(f"- {item['category']}：{'通过' if item['passed'] else '失败'}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    spec = json.loads((ROOT / "evals" / "drill_cases.json").read_text(encoding="utf-8"))
    results = [evaluate_case(item, spec["global_expectations"]) for item in spec["cases"]]
    blind_results = [evaluate_blind_isolation(item["id"]) for item in json.loads((ROOT / "data" / "drills" / "fault_catalog.json").read_text(encoding="utf-8"))["categories"]]
    report = write_report(results, blind_results)
    passed = all(item["passed"] for item in results + blind_results)
    print(json.dumps({"passed": passed, "scenarios": len(results), "blind_categories": len(blind_results), "report": str(report)}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
