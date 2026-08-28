#!/usr/bin/env python3
"""Run the isolated 120-case acceptance suite and write a human-readable report."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from idcops.sandbox_validation import SandboxValidationService  # noqa: E402
from idcops.store import IncidentStore  # noqa: E402


METRIC_NAMES = {
    "parse_success": "接入解析成功率",
    "identity_correct": "设备身份关联准确率",
    "candidate_top3_hit": "候选范围覆盖率",
    "stop_or_escalate": "证据不足时正确停止率",
    "safe_next_step": "安全下一步合规率",
    "trace_complete": "证据与版本轨迹完整率",
}

GATE_NAMES = {
    "production_zero_pollution": "生产数据库零污染",
    "identity_not_invented": "没有猜测设备身份",
    "no_unfounded_confirmation": "没有无证据确认根因",
    "no_automatic_high_risk_action": "没有自动批准高风险动作",
    "evidence_and_version_trace": "证据与版本链完整",
    "agent_not_faked": "未把规则基线冒充真实 AI",
    "hidden_answer_not_leaked": "运行期没有答案泄漏",
    "suite_complete": "120题全部得到终态",
    "hidden_suite_unrevealed": "隐藏题包未被揭晓",
}


def percentage(value: Any) -> str:
    return f"{float(value or 0) * 100:.1f}%"


def markdown_report(report: Mapping[str, Any]) -> str:
    verdict = "达到生产试点门槛" if report.get("verdict") == "pilot_ready" else "暂不建议进入试点"
    progress = report.get("progress") or {}
    agent = (report.get("tracks") or {}).get("agent") or {}
    lines = [
        "# 生产近似沙盒盲测报告",
        "",
        f"- 运行编号：`{report.get('run_id', '')}`",
        f"- 题包版本：`{report.get('suite_version', '')}`",
        f"- 随机种子：`{report.get('seed', '')}`",
        f"- 结论：**{verdict}**",
        f"- 完成情况：{progress.get('completed', 0)} / {progress.get('total', 120)}",
        f"- 真实 AI：{'已完成' if agent.get('status') == 'completed' else '未配置，未运行'}",
        f"- 声明边界：{report.get('claim_boundary', '')}",
        "",
        "## 核心指标",
        "",
        "| 指标 | 实测 | 门槛 |",
        "| --- | ---: | ---: |",
    ]
    for key, label in METRIC_NAMES.items():
        lines.append(
            f"| {label} | {percentage((report.get('metrics') or {}).get(key))} | "
            f"{percentage((report.get('thresholds') or {}).get(key))} |"
        )
    lines.extend(["", "## 九项硬门禁", ""])
    for key, label in GATE_NAMES.items():
        lines.append(f"- {'通过' if (report.get('hard_gates') or {}).get(key) else '阻断'}：{label}")
    lines.extend(
        [
            "",
            "## 结论解释",
            "",
            "这次结果证明隔离、接入、关联、规则基线、评分和追溯链可以重复运行；不证明客户机房中的生产准确率。真实模型未配置，因此没有生成或评分任何伪造的 AI 结果。进入生产前仍需接入客户授权的只读数据，并用真实试点故障复核。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行120题生产近似沙盒盲测")
    parser.add_argument("--seed", type=int, default=20260827, help="可复现随机种子")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports",
        help="报告输出目录",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="idcai-sandbox-validation-") as temporary:
        temporary_root = Path(temporary)
        production_store = IncidentStore(str(temporary_root / "production-sentinel.db"))
        service = SandboxValidationService(
            production_store,
            sandbox_root=temporary_root / "sandbox",
            project_root=PROJECT_ROOT,
            ai_enabled=False,
        )
        run = service.create_run(
            {"seed": args.seed, "tracks": ["baseline", "agent"], "execute": True},
            actor="sandbox-validation-cli",
        )
        report = run["report"]

    json_path = output_dir / "sandbox-validation-latest.json"
    markdown_path = output_dir / "sandbox-validation-latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "run_id": report.get("run_id"),
                "verdict": report.get("verdict"),
                "completed": (report.get("progress") or {}).get("completed"),
                "agent_status": ((report.get("tracks") or {}).get("agent") or {}).get("status"),
                "production_unchanged": report.get("production_unchanged"),
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
