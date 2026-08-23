"""Explainable first-pass incident rules.

The rules produce evidence-backed hypotheses. They do not execute actions and
they never infer that a CC call is required from temperature alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from .models import NormalizedInput, RuleAnalysis


@dataclass(frozen=True)
class Rule:
    name: str
    category: str
    title: str
    patterns: Sequence[str]
    causes: Sequence[str]
    suggestions: Sequence[str]
    requires_onsite: bool
    severity: str = "warning"


RULES: Sequence[Rule] = (
    Rule(
        name="facility_temperature",
        category="facility",
        title="机房温度或散热异常",
        patterns=(
            r"高温|温度.{0,8}(升高|过高|异常)|temperature.{0,8}(high|rising|critical)",
            r"风扇.{0,8}(满转|高速|异常)|fan.{0,8}(high|full|critical)",
            r"空调.{0,8}(故障|停机|异常)|cooling.{0,8}(failure|alarm)",
        ),
        causes=("制冷或动环异常", "局部冷热通道异常", "设备负载或进风异常"),
        suggestions=(
            "联系动环人员确认空调、温度和基础设施状态",
            "确认受影响机柜、服务器数量和温度变化趋势",
            "同步系统组与网络组确认是否已有设备或链路异常",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="disk_io",
        category="hardware",
        title="磁盘或文件系统异常",
        patterns=(
            r"I/O error|blk_update_request|Buffer I/O|medium error|uncorrectable",
            r"SMART.{0,12}(fail|critical)|NVMe.{0,12}(error|critical)|media_errors",
            r"read-only file system|remounting filesystem read-only|No space left on device",
            r"磁盘.{0,8}(故障|异常|报错)|硬盘.{0,8}(故障|异常|掉盘)",
        ),
        causes=("磁盘介质或控制器异常", "文件系统保护性只读", "容量或 inode 耗尽"),
        suggestions=(
            "确认完整 SN、机架位和故障盘位后再进行现场操作",
            "核对 SMART/NVMe 健康信息和最近 I/O 错误",
            "确认业务与电源操作权限；支持热插拔也不等于可以盲目拔盘",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="memory_machine_check",
        category="hardware",
        title="内存或主板相关异常",
        patterns=(
            r"\bECC\b|EDAC|corrected memory error|uncorrected memory error",
            r"Machine Check|\bMCE\b|memory failure|DIMM",
            r"内存.{0,8}(故障|异常|报错)|内存条",
        ),
        causes=("内存条或内存通道异常", "主板/CPU 内存控制器异常", "系统内存压力导致 OOM"),
        suggestions=(
            "核对日志中的 CPU、通道和 DIMM 槽位，但保留主板故障候选",
            "确认完整 SN、机架位、UID 和业务状态后再操作",
            "更换内存后仍无法启动时停止重复换件并重新评估主板候选",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="system_memory_pressure",
        category="system",
        title="系统内存压力或 OOM",
        patterns=(
            r"Out of memory|oom-killer|Killed process",
            r"memory pressure|内存不足|内存耗尽|OOM",
        ),
        causes=("进程内存使用超过可用资源", "内存泄漏或异常负载", "系统容量或限制配置不足"),
        suggestions=(
            "确认被终止进程、内存使用趋势和当时系统负载",
            "检查服务内存限制、重启历史和近期业务变化",
            "优先由系统组远程确认，不把 OOM 直接判断为内存条故障",
        ),
        requires_onsite=False,
        severity="critical",
    ),
    Rule(
        name="network_link",
        category="network",
        title="网络链路、模块或端口异常",
        patterns=(
            r"link (is )?down|interface.{0,12}down|carrier lost|link flap",
            r"SFP|QSFP|optical power|light power|Rx Power|Tx Power",
            r"端口.{0,8}(故障|down|异常)|链路.{0,8}(中断|异常)|光功率|模块.{0,8}故障",
        ),
        causes=("本端模块或端口异常", "线缆/光纤异常", "对端模块或端口异常"),
        suggestions=(
            "由接口人提供或确认光功率正常范围，现场回报实测值",
            "按本端模块、线缆/光纤、对端模块和端口顺序保留证据",
            "任何拔线操作前核对设备、完整端口和对端信息",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="application_runtime",
        category="application",
        title="服务运行或程序冲突",
        patterns=(
            r"Address already in use|port.{0,8}already in use|端口.{0,8}占用",
            r"Failed to start|service failed|启动失败|启动异常",
            r"segfault|core dumped|Traceback|Unhandled exception|panic",
            r"deadlock|lock timeout|资源冲突|程序.{0,8}冲突",
        ),
        causes=("端口、锁或运行资源冲突", "进程崩溃或依赖异常", "启动顺序或配置冲突"),
        suggestions=(
            "检查失败服务、占用端口、相关进程和最近运行变更",
            "保留错误堆栈与服务状态，避免在没有授权时读取源代码",
            "由系统组确认远程处理方案；需要物理操作时再转现场",
        ),
        requires_onsite=False,
        severity="warning",
    ),
)


_SEVERITY_RANK = {"unknown": 0, "info": 1, "warning": 2, "critical": 3}


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "required"}


def _matched_lines(text: str, patterns: Iterable[str], limit: int = 6) -> List[str]:
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    found: List[str] = []
    for line in text.splitlines():
        compact = " ".join(line.strip().split())
        if not compact:
            continue
        if any(pattern.search(compact) for pattern in compiled):
            clipped = compact[:360]
            if clipped not in found:
                found.append(clipped)
        if len(found) >= limit:
            break
    return found


def _explicit_cc(event: NormalizedInput, text: str) -> bool:
    if _truthy(event.labels.get("cc_required")):
        return True
    return bool(
        re.search(
            r"(?i)(需要|立即|已经|触发|请).{0,8}\bCC\b.{0,8}(通报|电话|拨打)"
            r"|\bCC\b.{0,8}(通报|电话|拨打)",
            text,
        )
    )


def analyze_rules(event: NormalizedInput) -> RuleAnalysis:
    """Analyze a normalized input and return evidence-backed rule output."""

    text = "\n".join(part for part in (event.summary, event.raw_text) if part)
    matches: List[Dict[str, Any]] = []
    for rule in RULES:
        lines = _matched_lines(text, rule.patterns)
        if lines:
            matches.append({"rule": rule, "lines": lines})

    if matches:
        primary = max(
            matches,
            key=lambda item: (
                _SEVERITY_RANK[item["rule"].severity],
                len(item["lines"]),
            ),
        )
        rule = primary["rule"]
        evidence_lines: List[str] = []
        for match in matches:
            for line in match["lines"]:
                if line not in evidence_lines:
                    evidence_lines.append(line)
        evidence = [
            {"id": f"E{index}", "source": event.source, "text": line}
            for index, line in enumerate(evidence_lines[:8], start=1)
        ]
        causes = [
            {
                "title": cause,
                "confidence": max(0.45, 0.82 - (index * 0.14)),
                "evidence_ids": [item["id"] for item in evidence[:3]],
                "counter_evidence": "当前输入未提供足够反证，需继续验证",
                "status": "候选",
            }
            for index, cause in enumerate(rule.causes)
        ]
        severity = (
            event.severity
            if _SEVERITY_RANK[event.severity] >= _SEVERITY_RANK[rule.severity]
            else rule.severity
        )
        category = rule.category
        title = rule.title
        requires_onsite = rule.requires_onsite
        suggestions = list(rule.suggestions)
    else:
        evidence_text = (event.summary or event.raw_text)[:360]
        evidence = [{"id": "E1", "source": event.source, "text": evidence_text}]
        causes = [
            {
                "title": "当前证据不足，故障类型待确认",
                "confidence": 0.2,
                "evidence_ids": ["E1"],
                "counter_evidence": "缺少可识别的日志模式或结构化告警",
                "status": "待确认",
            }
        ]
        severity = event.severity
        category = "unknown"
        title = "待调查异常"
        requires_onsite = event.source == "onsite"
        suggestions = ["补充完整设备身份、异常时间和相关日志", "由接口人确认影响与处理范围"]

    missing: List[str] = []
    if requires_onsite and event.device.device_type in {"server", "switch", "unknown"}:
        if not event.device.sn:
            missing.append("完整 SN")
        if not event.device.rack_position:
            missing.append("机架位")
        if event.operation_context.uid_status == "unknown":
            missing.append("UID 状态")
        if event.operation_context.power_permission in {"unknown", "confirm"}:
            missing.append("操作/断电许可")

    cc_required = _explicit_cc(event, text)
    cc_reason = "输入已明确标记需要 CC 通报" if cc_required else ""
    return RuleAnalysis(
        category=category,
        severity=severity,
        title=title,
        requires_onsite=requires_onsite,
        cc_required=cc_required,
        cc_reason=cc_reason,
        evidence=evidence,
        candidate_causes=causes,
        suggestions=suggestions,
        missing_information=missing,
        matched_rules=[item["rule"].name for item in matches],
    )
