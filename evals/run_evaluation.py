#!/usr/bin/env python3
"""Replay the frozen evaluation questions and write JSON/Markdown reports."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idcops.service import IncidentService  # noqa: E402
from idcops.store import IncidentStore  # noqa: E402


def _contains(values: List[Any], expected: str) -> bool:
    return expected.lower() in " ".join(str(value) for value in values).lower()


def evaluate_case(question: Mapping[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as directory:
        service = IncidentService(IncidentStore(str(Path(directory) / "evaluation.db")))
        outputs = []
        for item in question["inputs"]:
            outputs.append(service.ingest(item["source"], item["payload"]))
        incident_ids = {item["id"] for item in outputs}
        incident = service.get_incident(outputs[-1]["id"])
        assert incident is not None
        expected = question["expected"]

        def check(name: str, actual: Any, wanted: Any) -> None:
            checks.append({"name": name, "actual": actual, "expected": wanted, "passed": actual == wanted})

        if "category" in expected:
            check("故障分类", incident["category"], expected["category"])
        if "requires_onsite" in expected:
            check("需要现场介入", incident["onsite_card"]["required"], expected["requires_onsite"])
        if "cc_required" in expected:
            check("CC提醒", incident["cc_reminder"].get("required", False), expected["cc_required"])
        if "cc_message_exact" in expected:
            check("CC提醒文本", incident["cc_reminder"].get("message"), expected["cc_message_exact"])
        if "gate" in expected:
            check("现场安全门", incident["onsite_card"]["power"]["gate"], expected["gate"])
        if "sn_exact" in expected:
            check("完整SN", incident["onsite_card"]["device"]["sn"], expected["sn_exact"])
        if "identity_complete" in expected:
            check("身份完整", incident["onsite_card"]["identity_complete"], expected["identity_complete"])
        if "affected_count" in expected:
            check("影响设备数", incident["affected_count"], expected["affected_count"])
        if "incident_count" in expected:
            check("事件数量", len(incident_ids), expected["incident_count"])
        if "input_count" in expected:
            check("输入证据数", len(incident["inputs"]), expected["input_count"])
        if "candidate_contains" in expected:
            actual = [item["title"] for item in incident["analysis"].get("candidate_causes", [])]
            check("根因候选关键词", _contains(actual, expected["candidate_contains"]), True)
        if "suggestion_contains" in expected:
            actual = incident["analysis"].get("suggestions", [])
            check("建议关键词", _contains(actual, expected["suggestion_contains"]), True)
        if "evidence_contains" in expected:
            actual = [item["text"] for item in incident.get("evidence", [])]
            check("证据关键词", _contains(actual, expected["evidence_contains"]), True)
        if "missing_contains" in expected:
            missing = incident["onsite_card"].get("missing_information", [])
            check("缺失信息", all(item in missing for item in expected["missing_contains"]), True)
        if "candidate_status" in expected:
            statuses = [item.get("status") for item in incident["analysis"].get("candidate_causes", [])]
            check("候选状态", expected["candidate_status"] in statuses, True)
        if "max_confidence" in expected:
            confidences = [
                float(item.get("confidence", 0))
                for item in incident["analysis"].get("candidate_causes", [])
            ]
            actual = max(confidences or [0]) <= float(expected["max_confidence"])
            check("最大置信度限制", actual, True)

        passed = all(item["passed"] for item in checks)
        return {
            "id": question["id"],
            "title": question["title"],
            "passed": passed,
            "checks": checks,
            "incident_ids": sorted(incident_ids),
        }


def build_report(results: List[Dict[str, Any]], started_at: str) -> Tuple[Dict[str, Any], str]:
    passed = sum(1 for item in results if item["passed"])
    total_checks = sum(len(item["checks"]) for item in results)
    passed_checks = sum(
        1 for item in results for check in item["checks"] if check["passed"]
    )
    report = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {
            "questions_passed": passed,
            "questions_total": len(results),
            "question_pass_rate": passed / len(results) if results else 0,
            "checks_passed": passed_checks,
            "checks_total": total_checks,
            "check_pass_rate": passed_checks / total_checks if total_checks else 0,
        },
        "results": results,
    }
    lines = [
        "# 首批独立测试集自测报告",
        "",
        f"- 题目通过：{passed}/{len(results)}",
        f"- 检查点通过：{passed_checks}/{total_checks}",
        f"- 题目通过率：{report['summary']['question_pass_rate']:.1%}",
        "",
        "| 题号 | 场景 | 结果 | 失败检查点 |",
        "|---|---|---|---|",
    ]
    for item in results:
        failures = [check["name"] for check in item["checks"] if not check["passed"]]
        lines.append(
            f"| {item['id']} | {item['title']} | {'通过' if item['passed'] else '失败'} | "
            f"{'、'.join(failures) if failures else '-'} |"
        )
    lines.extend(["", "## 逐题检查", ""])
    for item in results:
        lines.append(f"### {item['id']} {item['title']}")
        lines.append("")
        for check in item["checks"]:
            mark = "通过" if check["passed"] else "失败"
            lines.append(
                f"- {mark}｜{check['name']}：实际 `{check['actual']}`，预期 `{check['expected']}`"
            )
        lines.append("")
    return report, "\n".join(lines)


def main() -> int:
    source = json.loads((ROOT / "evals" / "test_cases.json").read_text(encoding="utf-8"))
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    results = [evaluate_case(question) for question in source["questions"]]
    report, markdown = build_report(results, started_at)
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "evaluation-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (reports / "evaluation-report.md").write_text(markdown + "\n", encoding="utf-8")
    print(markdown.split("## 逐题检查", 1)[0].strip())
    return 0 if report["summary"]["questions_passed"] == report["summary"]["questions_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

