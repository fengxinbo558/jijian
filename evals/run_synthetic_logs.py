#!/usr/bin/env python3
"""Run generated network/system/facility logs through the real incident service."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idcops.service import IncidentService  # noqa: E402
from idcops.store import IncidentStore  # noqa: E402


def _contains(values: Iterable[Any], expected: str) -> bool:
    return expected.lower() in " ".join(str(value) for value in values).lower()


def evaluate_case(case: Mapping[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as directory:
        service = IncidentService(IncidentStore(str(Path(directory) / "synthetic.db")))
        outputs: List[Dict[str, Any]] = []
        for item in case["inputs"]:
            payload = copy.deepcopy(item["payload"])
            payload["is_demo"] = True
            payload["demo_id"] = case["id"]
            outputs.append(service.ingest(item["source"], payload))
        incident_ids = {item["id"] for item in outputs}
        incident = service.get_incident(outputs[-1]["id"])
        assert incident is not None
        expected = case["expected"]

        def check(name: str, actual: Any, wanted: Any) -> None:
            checks.append(
                {"name": name, "actual": actual, "expected": wanted, "passed": actual == wanted}
            )

        if "category" in expected:
            check("故障分类", incident["category"], expected["category"])
        if "requires_onsite" in expected:
            check("需要现场介入", incident["onsite_card"]["required"], expected["requires_onsite"])
        if "cc_required" in expected:
            check(
                "CC提醒",
                incident["cc_reminder"].get("required", False),
                expected["cc_required"],
            )
        if "cc_decision" in expected:
            decision = (
                incident.get("analysis", {})
                .get("facility_assessment", {})
                .get("decision", "not_available")
            )
            check("重大事件判断", decision, expected["cc_decision"])
        if "incident_count" in expected:
            check("事件数量", len(incident_ids), expected["incident_count"])
        if "input_count" in expected:
            check("输入数量", len(incident.get("inputs", [])), expected["input_count"])
        if "affected_count" in expected:
            check("影响对象数", incident.get("affected_count"), expected["affected_count"])

        investigation = incident.get("investigation", {})
        if "fact_types_contains" in expected:
            fact_types = {item.get("type") for item in investigation.get("extracted_facts", [])}
            check(
                "事实类型",
                sorted(value for value in fact_types if value),
                expected["fact_types_contains"],
            )
            checks[-1]["passed"] = all(
                item in fact_types for item in expected["fact_types_contains"]
            )
        if "matched_rules_contains" in expected:
            rule_names = {
                item.get("name") for item in investigation.get("rule_matches", []) if item.get("name")
            }
            check("命中规则", sorted(rule_names), expected["matched_rules_contains"])
            checks[-1]["passed"] = all(
                item in rule_names for item in expected["matched_rules_contains"]
            )
        if "matched_rules_excludes" in expected:
            rule_names = {
                item.get("name") for item in investigation.get("rule_matches", []) if item.get("name")
            }
            check("不得误命中规则", sorted(rule_names), expected["matched_rules_excludes"])
            checks[-1]["passed"] = all(
                item not in rule_names for item in expected["matched_rules_excludes"]
            )
        if "candidate_contains" in expected:
            titles = [item.get("title", "") for item in investigation.get("hypotheses", [])]
            check("候选原因", _contains(titles, expected["candidate_contains"]), True)
        if "suggestion_contains" in expected:
            suggestions = incident.get("analysis", {}).get("suggestions", [])
            check("建议内容", _contains(suggestions, expected["suggestion_contains"]), True)
        if expected.get("conclusion_not_confirmed"):
            check(
                "不得越级确认",
                investigation.get("conclusion", {}).get("grade") != "confirmed",
                True,
            )
        check("模拟数据标识", bool(investigation.get("simulation")), True)

        return {
            "id": case["id"],
            "domain": case["domain"],
            "title": case["title"],
            "origin": case["origin"],
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
            "incident_ids": sorted(incident_ids),
        }


def build_report(results: List[Dict[str, Any]], started_at: str) -> Tuple[Dict[str, Any], str]:
    passed_cases = sum(1 for item in results if item["passed"])
    total_checks = sum(len(item["checks"]) for item in results)
    passed_checks = sum(
        1 for item in results for check in item["checks"] if check["passed"]
    )
    domain_totals = Counter(item["domain"] for item in results)
    domain_passed = Counter(item["domain"] for item in results if item["passed"])
    report = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {
            "cases_passed": passed_cases,
            "cases_total": len(results),
            "checks_passed": passed_checks,
            "checks_total": total_checks,
            "evaluation_mode": "generated_logs_rules_and_knowledge_baseline",
            "model_status": "not_required_for_baseline",
        },
        "domains": {
            domain: {"passed": domain_passed[domain], "total": total}
            for domain, total in sorted(domain_totals.items())
        },
        "limitations": [
            "所有日志均为合成数据，不能代替真实厂商设备和客户环境验证。",
            "本评测验证规则、知识召回、CC边界和审计结构，不代表生产定位准确率。",
            "真实接入仍需要客户授权的journald、syslog、NMS、BMC、动环和资产接口。",
        ],
        "results": results,
    }
    lines = [
        "# 网络、系统与机房合成日志自测报告",
        "",
        f"- 场景通过：{passed_cases}/{len(results)}",
        f"- 检查点通过：{passed_checks}/{total_checks}",
        "- 测试模式：合成日志＋规则＋知识库基线",
        "- 限制：结果不能证明真实生产环境准确率",
        "",
        "## 分领域结果",
        "",
        "| 领域 | 通过 | 总数 |",
        "|---|---:|---:|",
    ]
    for domain, total in sorted(domain_totals.items()):
        lines.append(f"| {domain} | {domain_passed[domain]} | {total} |")
    lines.extend(
        [
            "",
            "## 场景结果",
            "",
            "| 编号 | 场景 | 来源 | 结果 | 失败检查点 |",
            "|---|---|---|---|---|",
        ]
    )
    for item in results:
        failures = [check["name"] for check in item["checks"] if not check["passed"]]
        lines.append(
            f"| {item['id']} | {item['title']} | {item['origin']} | "
            f"{'通过' if item['passed'] else '失败'} | {'、'.join(failures) if failures else '-'} |"
        )
    lines.extend(["", "## 逐条检查", ""])
    for item in results:
        lines.extend([f"### {item['id']} {item['title']}", ""])
        for check in item["checks"]:
            mark = "通过" if check["passed"] else "失败"
            lines.append(
                f"- {mark}｜{check['name']}：实际 `{check['actual']}`，预期 `{check['expected']}`"
            )
        lines.append("")
    return report, "\n".join(lines)


def main() -> int:
    dataset = json.loads((ROOT / "evals" / "synthetic_log_cases.json").read_text(encoding="utf-8"))
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    results = [evaluate_case(case) for case in dataset["cases"]]
    report, markdown = build_report(results, started_at)
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "synthetic-log-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (reports / "synthetic-log-report.md").write_text(markdown + "\n", encoding="utf-8")
    print(markdown.split("## 逐条检查", 1)[0].strip())
    return 0 if report["summary"]["cases_passed"] == report["summary"]["cases_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
