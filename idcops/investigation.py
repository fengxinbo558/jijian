"""Build an auditable investigation trace from normalized evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Pattern, Sequence, Tuple

from .knowledge import KnowledgeBase
from .models import NormalizedInput, RuleAnalysis, utc_now
from .rules import RULES


PROMPT_CONTRACTS = {
    "parser": "parser-v1.0.0",
    "hypothesis": "hypothesis-v1.0.0",
    "next_step": "next-step-v1.0.0",
    "communication": "communication-v1.0.0",
}


@dataclass(frozen=True)
class FactPattern:
    fact_type: str
    label: str
    pattern: Pattern[str]
    value_group: int = 0
    unit: str = ""


def _compile(pattern: str) -> Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


FACT_PATTERNS: Sequence[FactPattern] = (
    FactPattern("block_io_error", "块设备 I/O 错误", _compile(r"I/O error|Buffer I/O|blk_update_request|medium error")),
    FactPattern("block_device", "块设备对象", _compile(r"(?:/dev/|\bdev\s+)([a-z][a-z0-9_-]{1,20})"), 1),
    FactPattern("smart_failure", "SMART 健康失败", _compile(r"SMART.{0,24}(?:fail|critical)|Failure Predicted")),
    FactPattern("nvme_health", "NVMe 健康异常", _compile(r"NVMe.{0,30}(?:error|critical)|media_errors|critical_warning")),
    FactPattern("filesystem_read_only", "文件系统只读", _compile(r"read-only file system|remounting filesystem read-only")),
    FactPattern("space_exhausted", "存储空间耗尽", _compile(r"No space left on device")),
    FactPattern("raid_degraded", "RAID 降级", _compile(r"RAID.{0,20}degraded|degraded array|member disk failed")),
    FactPattern("storage_timeout", "存储命令超时或复位", _compile(r"command timeout|resetting link|controller reset")),
    FactPattern("memory_ue", "内存不可纠正错误", _compile(r"uncorrected memory error|uncorrectable.{0,12}(?:ECC|memory)|EDAC\s+.*\bUE\b")),
    FactPattern("memory_ce", "内存可纠正错误", _compile(r"corrected memory error|correctable.{0,12}(?:ECC|memory)|EDAC\s+.*\bCE\b")),
    FactPattern("machine_check", "Machine Check 事件", _compile(r"Machine Check|\bMCE\b")),
    FactPattern("memory_locator", "内存逻辑位置", _compile(r"(?:socket|channel|dimm)\s*[=:]\s*[a-z0-9_-]+")),
    FactPattern("pcie_aer", "PCIe AER 错误", _compile(r"AER:|PCIe Bus Error")),
    FactPattern("oom_kill", "OOM 终止进程", _compile(r"Out of memory|oom-killer|Killed process|memory pressure")),
    FactPattern("link_flap", "链路反复变化", _compile(r"link flap|flapping")),
    FactPattern("link_down", "接口运行状态下降", _compile(r"link (?:is )?down|interface.{0,20}down|carrier lost|ifOperStatus.{0,8}down")),
    FactPattern("network_interface", "网络接口对象", _compile(r"\b((?:Hundred|Forty|Ten)?Gig(?:abit)?Ethernet?[0-9/.-]+|eth\d+|ens\d+|eno\d+|bond\d+)\b"), 1),
    FactPattern("optical_power", "光功率读数或告警", _compile(r"(?:Rx|Tx)\s*Power|optical power|光功率")),
    FactPattern("crc_error", "接口 CRC 或输入错误", _compile(r"CRC errors?|input errors?")),
    FactPattern("bond_member_down", "Bond 成员异常", _compile(r"bond.{0,20}(?:slave|member).{0,12}down|active slave changed")),
    FactPattern("temperature", "温度读数或高温信号", _compile(r"(?:temperature|温度)[^\n]{0,20}?(-?\d+(?:\.\d+)?)\s*(?:°?C|℃)?"), 1, "°C"),
    FactPattern("fan_anomaly", "风扇异常", _compile(r"fan.{0,20}(?:full|high|fail|critical)|风扇.{0,12}(?:满转|高速|故障|异常)")),
    FactPattern("cooling_alarm", "制冷或空调告警", _compile(r"cooling.{0,20}(?:failure|alarm)|空调.{0,12}(?:故障|停机|异常)|制冷.{0,12}异常")),
    FactPattern("power_supply", "电源模块告警", _compile(r"power supply.{0,20}(?:fail|lost)|PSU.{0,16}(?:fail|lost)|redundancy lost")),
    FactPattern("time_discontinuity", "时间不连续", _compile(r"clock changed|timestamp jump|NTP.{0,16}(?:step|adjust)")),
    FactPattern("service_failed", "服务启动失败", _compile(r"Failed to start|Active:\s*failed|service failed|Result:\s*exit-code")),
    FactPattern("boot_failure", "系统启动失败", _compile(r"boot failed|emergency mode|无法启动")),
    FactPattern("kernel_panic", "Kernel panic", _compile(r"Kernel panic|panic - not syncing")),
    FactPattern("mount_failure", "挂载失败", _compile(r"Failed to mount|mount unit.{0,20}failed")),
    FactPattern("port_conflict", "监听端口冲突", _compile(r"(?:Address already in use|port.{0,12}already in use|端口.{0,8}占用)(?:[^\n]{0,40}?([0-9]{2,5}))?"), 1),
    FactPattern("process_crash", "进程崩溃", _compile(r"segfault|core dumped|signal 11")),
    FactPattern("dependency_connection", "依赖连接失败", _compile(r"Connection refused|connection timed out|dependency unavailable")),
    FactPattern("lock_contention", "锁竞争或死锁", _compile(r"deadlock|lock timeout|could not acquire lock")),
    FactPattern("restart_loop", "服务反复重启", _compile(r"Start request repeated too quickly|restart counter|crash loop")),
)


RULE_LIMITATIONS = {
    "facility_temperature": "温度或风扇信号只能说明环境/散热方向，不能单独确认空调故障或触发 CC。",
    "disk_io": "存储关键词只能确认存在存储路径异常信号，不能单独确认具体物理盘损坏。",
    "memory_machine_check": "ECC/MCE 需要解码与物理映射，不能直接等同于某根内存条故障。",
    "system_memory_pressure": "OOM 描述资源耗尽，不支持直接判断物理内存硬件故障。",
    "network_link": "单端 link down 或光功率信息不能单独区分本端、链路和对端。",
    "application_runtime": "运行时错误需要结合进程、依赖和变更，不能推断客户源代码原因。",
}


def _digest(prefix: str, value: str, length: int = 10) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:length].upper()


def _unique(items: Iterable[Mapping[str, Any]], key: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        marker = str(item.get(key, ""))
        if not marker or marker in seen:
            continue
        seen.add(marker)
        result.append(dict(item))
    return result


def _line_for(text: str, match: re.Match[str]) -> str:
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    if end < 0:
        end = len(text)
    return " ".join(text[start:end].strip().split())[:500]


def _source_declaration(event: NormalizedInput) -> Tuple[str, str, bool]:
    if event.labels.get("demo_id") or event.labels.get("is_demo"):
        return "内置测试场景", "simulation", True
    if event.labels.get("source_system") == "signoz" and event.labels.get("external_query"):
        return "SigNoz 自动查询", "external_tool", False
    if event.labels.get("source_system") == "signoz":
        return "SigNoz 告警 Webhook", "external_system", False
    if event.source == "monitor":
        return "监控 Webhook 提供", "external_system", False
    if event.source == "onsite":
        return "现场人员填写", "human_report", False
    return "用户粘贴或上传日志", "human_upload", False


def _evidence_for_event(event: NormalizedInput, input_id: str) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    raw = event.raw_text or event.summary
    for line in raw.splitlines() or [raw]:
        compact = " ".join(line.strip().split())
        if not compact:
            continue
        evidence_id = _digest("EV-", input_id + "|" + compact)
        result.append(
            {
                "id": evidence_id,
                "input_id": input_id,
                "source": event.source,
                "text": compact[:1000],
                "event_time": event.event_time,
            }
        )
    return result[:80]


def extract_facts(event: NormalizedInput, evidence: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Extract conservative facts with direct evidence references."""

    text = "\n".join(part for part in (event.summary, event.raw_text) if part)
    evidence_by_text = [(str(item.get("text", "")), str(item.get("id", ""))) for item in evidence]
    facts: List[Dict[str, Any]] = []
    seen = set()
    for definition in FACT_PATTERNS:
        for match in definition.pattern.finditer(text):
            excerpt = _line_for(text, match)
            value = match.group(0)
            if definition.value_group and match.lastindex and match.group(definition.value_group):
                value = match.group(definition.value_group)
            marker = (definition.fact_type, value.lower(), excerpt.lower())
            if marker in seen:
                continue
            seen.add(marker)
            evidence_id = ""
            for evidence_text, candidate_id in evidence_by_text:
                if excerpt and (excerpt in evidence_text or evidence_text in excerpt):
                    evidence_id = candidate_id
                    break
            if not evidence_id and evidence:
                evidence_id = str(evidence[0].get("id", ""))
            fact_id = _digest("F-", f"{definition.fact_type}|{value}|{evidence_id}")
            facts.append(
                {
                    "id": fact_id,
                    "type": definition.fact_type,
                    "label": definition.label,
                    "value": value[:200],
                    "unit": definition.unit,
                    "evidence_ids": [evidence_id] if evidence_id else [],
                    "excerpt": excerpt,
                    "parser": "deterministic-regex-v1",
                    "event_time": event.event_time,
                }
            )
    return facts[:80]


def _field_provenance(event: NormalizedInput, input_id: str, source_label: str) -> List[Dict[str, Any]]:
    fields = (
        ("site", "机房", event.site),
        ("device.sn", "完整 SN", event.device.sn),
        ("device.name", "设备名", event.device.name),
        ("device.rack_position", "机架位", event.device.rack_position),
        ("device.ip", "IP", event.device.ip),
        ("device.device_type", "设备类型", event.device.device_type),
        ("operation.uid_status", "UID 状态", event.operation_context.uid_status),
        ("operation.from_reinstall", "是否从重装发起", event.operation_context.from_reinstall),
        ("operation.power_permission", "操作/断电许可", event.operation_context.power_permission),
    )
    result = []
    for field, label, raw_value in fields:
        value = str(raw_value or "").strip()
        known = bool(value and value.lower() != "unknown")
        result.append(
            {
                "field": field,
                "label": label,
                "value": value if known else "",
                "input_id": input_id,
                "method": "provided" if known else "unknown",
                "source_label": source_label if known else "未提供",
                "reliability": "reported" if known else "unknown",
                "verification": "系统未独立核验" if known else "需要补充",
            }
        )
    return result


def _rule_matches(analysis: RuleAnalysis, facts: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    definitions = {rule.name: rule for rule in RULES}
    evidence_ids = sorted(
        {
            str(evidence_id)
            for fact in facts
            for evidence_id in fact.get("evidence_ids", [])
            if evidence_id
        }
    )
    result: List[Dict[str, Any]] = []
    for name in analysis.matched_rules:
        rule = definitions.get(name)
        if not rule:
            continue
        result.append(
            {
                "id": "RULE-" + name,
                "name": name,
                "title": rule.title,
                "category": rule.category,
                "patterns": list(rule.patterns),
                "evidence_ids": evidence_ids,
                "scope": "关键词与正则模式识别",
                "limitation": RULE_LIMITATIONS.get(name, "规则命中只产生调查方向。"),
            }
        )
    return result


def _knowledge_view(matches: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in matches:
        card = item["card"]
        result.append(
            {
                "id": card["id"],
                "version": card["version"],
                "title": card["title"],
                "domain": card["domain"],
                "retrieval_reasons": list(item.get("reasons", [])),
                "prohibited_inferences": list(card.get("prohibited_inferences", [])),
                "stop_conditions": list(card.get("stop_conditions", [])),
                "sources": list(item.get("source_details", [])),
            }
        )
    return result


def _hypotheses(
    matches: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    fallback_causes: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    fact_types = {str(item.get("type", "")) for item in facts if item.get("type")}
    evidence_ids = sorted(
        {
            str(reference)
            for item in facts
            for reference in item.get("evidence_ids", [])
            if reference
        }
    )
    result: List[Dict[str, Any]] = []
    seen = set()
    diagnostic_fact_types = fact_types - {"block_device", "network_interface", "memory_locator"}
    high_likelihood_assigned = False
    for match in matches:
        card = match["card"]
        matched_fact_types = fact_types & {
            str(value).lower() for value in card.get("match", {}).get("fact_types", [])
        }
        for cause_index, cause in enumerate(card.get("competing_causes", [])[:3]):
            status = (
                "high_likelihood"
                if cause_index == 0
                and not high_likelihood_assigned
                and len(diagnostic_fact_types) >= 2
                and matched_fact_types
                and int(match["score"]) >= 9
                else "candidate"
            )
            if status == "high_likelihood":
                high_likelihood_assigned = True
            title = str(cause)
            marker = title.lower()
            if marker in seen:
                continue
            seen.add(marker)
            hypothesis_id = _digest("H-", marker)
            result.append(
                {
                    "id": hypothesis_id,
                    "title": title,
                    "object": "事件涉及设备或路径",
                    "status": status,
                    "supporting_evidence_ids": evidence_ids,
                    "counter_evidence_ids": [],
                    "known_counter_conditions": list(card.get("counter_signals", [])),
                    "missing_evidence": list(card.get("required_context", [])),
                    "confirm_checks": list(card.get("verification_steps", [])),
                    "falsify_checks": list(card.get("counter_signals", [])),
                    "knowledge_card_id": card["id"],
                    "knowledge_card_version": card["version"],
                    "generated_by": "knowledge_rule",
                    "basis": "由已提取事实召回知识卡后形成，尚未执行验证工具",
                }
            )
            if len(result) >= 8:
                return result
    if not result:
        for cause in fallback_causes[:4]:
            title = str(cause.get("title", "当前证据不足，原因待确认"))
            result.append(
                {
                    "id": _digest("H-", title.lower()),
                    "title": title,
                    "object": "待确认",
                    "status": "candidate",
                    "supporting_evidence_ids": evidence_ids,
                    "counter_evidence_ids": [],
                    "known_counter_conditions": [],
                    "missing_evidence": ["缺少适用知识或进一步验证结果"],
                    "confirm_checks": [],
                    "falsify_checks": [],
                    "knowledge_card_id": "",
                    "knowledge_card_version": "",
                    "generated_by": "rule_fallback",
                    "basis": "规则候选；知识覆盖不足",
                }
            )
    return result


def _verification_plan(
    matches: Sequence[Mapping[str, Any]], hypotheses: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    by_card: Dict[str, List[str]] = {}
    for item in hypotheses:
        card_id = str(item.get("knowledge_card_id", ""))
        if card_id:
            by_card.setdefault(card_id, []).append(str(item["id"]))
    result: List[Dict[str, Any]] = []
    seen = set()
    for match in matches[:5]:
        card = match["card"]
        for index, step in enumerate(card.get("verification_steps", [])[:2], start=1):
            method = str(step)
            if method in seen:
                continue
            seen.add(method)
            result.append(
                {
                    "id": _digest("V-", card["id"] + "|" + method),
                    "priority": len(result) + 1,
                    "purpose": f"区分与“{card['title']}”相关的竞争原因",
                    "method": method,
                    "permission": "只读系统/设备查询；若当前未接入则由接口人或现场按 SOP 执行",
                    "risk": "read_only",
                    "status": "waiting_external",
                    "tool": "not_connected",
                    "expected_effects": list(card.get("branch_conditions", [])),
                    "affects_hypothesis_ids": by_card.get(card["id"], []),
                    "knowledge_card_id": card["id"],
                }
            )
            if len(result) >= 6:
                return result
    if not result:
        result.append(
            {
                "id": "V-CONTEXT",
                "priority": 1,
                "purpose": "取得能够形成可靠候选的最小上下文",
                "method": "补充完整设备身份、异常时间和相邻日志",
                "permission": "人工补充或只读查询",
                "risk": "read_only",
                "status": "waiting_external",
                "tool": "not_connected",
                "expected_effects": ["可能召回适用知识卡并形成竞争假设"],
                "affects_hypothesis_ids": [str(item["id"]) for item in hypotheses],
                "knowledge_card_id": "",
            }
        )
    return result


def _correlation(event: NormalizedInput, key: str) -> Dict[str, Any]:
    explicit = str(event.labels.get("incident_key", "")).strip()
    if explicit:
        return {
            "key": key,
            "level": "explicit",
            "reason": "外部输入提供了相同 incident_key；系统没有独立证明这些设备具有共同根因。",
            "inputs_used": ["labels.incident_key"],
            "limitations": ["共同事件号不等于共同根因"],
        }
    identity = event.device.identity_key()
    if identity:
        return {
            "key": key,
            "level": "deterministic",
            "reason": "按机房、完整设备身份和故障类别进行确定性关联。",
            "inputs_used": ["site", "device.identity", "category"],
            "limitations": ["没有拓扑证据时不扩展到其他设备"],
        }
    return {
        "key": key,
        "level": "none",
        "reason": "缺少稳定设备身份，未执行自动合并。",
        "inputs_used": [],
        "limitations": ["需要补充完整 SN、设备名、IP 或机架位"],
    }


def _trace(
    intake: Mapping[str, Any],
    provenance: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    knowledge: Sequence[Mapping[str, Any]],
    hypotheses: Sequence[Mapping[str, Any]],
    verification: Sequence[Mapping[str, Any]],
    correlation: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    now = utc_now()
    stages = (
        ("received", "接收原始数据", "confirmed", f"保存 1 条输入，来源：{intake['source_label']}", [intake["id"]], "原文未被模型覆盖"),
        ("normalized", "归一化与字段溯源", "reported", f"记录 {len(provenance)} 个关键字段来源", [item["field"] for item in provenance], "外部提供字段尚未独立核验"),
        ("extracted", "提取可核对事实", "inferred", f"从原文提取 {len(facts)} 项事实", [item["id"] for item in facts], "正则解析只识别明确模式"),
        ("matched", "规则与知识匹配", "inferred", f"命中 {len(rules)} 条规则，召回 {len(knowledge)} 张知识卡", [item["id"] for item in rules] + [item["id"] for item in knowledge], "匹配只形成调查方向"),
        ("correlated", "事件关联", "reported" if correlation["level"] == "explicit" else "inferred", correlation["reason"], [correlation["key"]], "关联不等于根因确认"),
        ("hypothesized", "形成竞争假设", "inferred", f"保留 {len(hypotheses)} 个候选原因", [item["id"] for item in hypotheses], "候选仍需反证和工具验证"),
        ("planned", "选择下一步验证", "waiting", f"生成 {len(verification)} 项只读优先检查", [item["id"] for item in verification], "当前未接入的工具不会伪造结果"),
    )
    return [
        {
            "id": f"TRACE-{index}",
            "stage": stage,
            "title": title,
            "state": state,
            "summary": summary,
            "output_ids": output_ids,
            "limitation": limitation,
            "created_at": now,
        }
        for index, (stage, title, state, summary, output_ids, limitation) in enumerate(stages, start=1)
    ]


def build_investigation(
    event: NormalizedInput,
    analysis: RuleAnalysis,
    correlation_key: str,
    knowledge_base: KnowledgeBase,
    *,
    model_enriched: bool = False,
) -> Dict[str, Any]:
    source_label, source_kind, simulation = _source_declaration(event)
    input_material = f"{event.source}|{event.event_time}|{event.site}|{event.device.identity_key()}|{event.raw_text}"
    input_id = _digest("IN-", input_material)
    evidence = _evidence_for_event(event, input_id)
    facts = extract_facts(event, evidence)
    provenance = _field_provenance(event, input_id, source_label)
    rule_matches = _rule_matches(analysis, facts)
    raw_text = "\n".join(part for part in (event.summary, event.raw_text) if part)
    matches = knowledge_base.search(
        rule_names=analysis.matched_rules,
        fact_types=[str(item["type"]) for item in facts],
        text=raw_text,
        device_type=event.device.device_type,
    )
    knowledge_view = _knowledge_view(matches)
    hypotheses = _hypotheses(matches, facts, analysis.candidate_causes)
    verification = _verification_plan(matches, hypotheses)
    correlation = _correlation(event, correlation_key)
    intake = {
        "id": input_id,
        "source": event.source,
        "source_label": source_label,
        "source_kind": source_kind,
        "simulation": simulation,
        "event_time": event.event_time,
        "received_at": utc_now(),
        "summary": event.summary,
        "raw_text": event.raw_text,
        "redacted_for_external_model": False,
        "provided_device_fields": [
            name
            for name, value in event.device.to_dict().items()
            if str(value).strip() and str(value).lower() != "unknown"
        ],
    }
    leading = hypotheses[0] if hypotheses else None
    next_step = verification[0] if verification else None
    capability_notice = (
        "当前使用本地规则、结构化知识和可选模型增强；未连接自动日志采集器或设备查询工具。"
        if model_enriched
        else "当前使用本地规则与结构化知识调查；大模型、自动日志采集器和设备查询工具均未启用。"
    )
    conclusion = {
        "grade": str(leading.get("status", "candidate")) if leading else "insufficient",
        "confirmed_facts": [str(item["label"]) + "：" + str(item["value"]) for item in facts[:6]],
        "leading_hypothesis_id": str(leading.get("id", "")) if leading else "",
        "leading_hypothesis": str(leading.get("title", "")) if leading else "当前证据不足",
        "uncertainty": "候选原因尚未通过真实工具或人工检查确认。",
        "next_step_id": str(next_step.get("id", "")) if next_step else "",
        "next_step": str(next_step.get("method", "补充更多证据")) if next_step else "补充更多证据",
        "generated_by": "model_enhanced" if model_enriched else "rules_and_knowledge",
        "updated_at": utc_now(),
    }
    return {
        "schema_version": "1.0.0",
        "mode": "ai_enriched" if model_enriched else "rules_only",
        "capability_notice": capability_notice,
        "simulation": simulation,
        "prompt_contracts": dict(PROMPT_CONTRACTS),
        "intake": [intake],
        "evidence": evidence,
        "field_provenance": provenance,
        "extracted_facts": facts,
        "rule_matches": rule_matches,
        "knowledge_retrieval": {
            "mode": "deterministic",
            "coverage": "matched" if knowledge_view else "insufficient",
            "cards": knowledge_view,
        },
        "correlation": correlation,
        "hypotheses": hypotheses,
        "verification_plan": verification,
        "conclusion": conclusion,
        "trace": _trace(
            intake,
            provenance,
            facts,
            rule_matches,
            knowledge_view,
            hypotheses,
            verification,
            correlation,
        ),
    }


def merge_investigations(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge two traces without pretending to rerun unavailable historical tools."""

    if not existing or existing.get("legacy"):
        return dict(incoming)
    result = dict(incoming)
    result["simulation"] = bool(existing.get("simulation") and incoming.get("simulation"))
    result["intake"] = _unique(
        list(existing.get("intake", [])) + list(incoming.get("intake", [])), "id"
    )
    result["evidence"] = _unique(
        list(existing.get("evidence", [])) + list(incoming.get("evidence", [])), "id"
    )
    result["extracted_facts"] = _unique(
        list(existing.get("extracted_facts", [])) + list(incoming.get("extracted_facts", [])), "id"
    )
    result["rule_matches"] = _unique(
        list(existing.get("rule_matches", [])) + list(incoming.get("rule_matches", [])), "id"
    )
    result["hypotheses"] = _unique(
        list(existing.get("hypotheses", [])) + list(incoming.get("hypotheses", [])), "id"
    )[:12]
    result["verification_plan"] = _unique(
        list(existing.get("verification_plan", [])) + list(incoming.get("verification_plan", [])), "id"
    )[:10]
    existing_cards = existing.get("knowledge_retrieval", {}).get("cards", [])
    incoming_cards = incoming.get("knowledge_retrieval", {}).get("cards", [])
    cards = _unique(list(existing_cards) + list(incoming_cards), "id")
    result["knowledge_retrieval"] = {
        "mode": "deterministic",
        "coverage": "matched" if cards else "insufficient",
        "cards": cards,
    }
    provenance_keyed: Dict[str, Dict[str, Any]] = {}
    for item in list(existing.get("field_provenance", [])) + list(incoming.get("field_provenance", [])):
        field = str(item.get("field", ""))
        if not field:
            continue
        current = provenance_keyed.get(field)
        if current is None or (not current.get("value") and item.get("value")):
            provenance_keyed[field] = dict(item)
    result["field_provenance"] = list(provenance_keyed.values())
    result["trace"] = list(incoming.get("trace", []))
    for item in result["trace"]:
        if item.get("stage") == "received":
            item["summary"] = f"已累计保存 {len(result['intake'])} 条输入；最新来源：{result['intake'][-1]['source_label']}"
        elif item.get("stage") == "extracted":
            item["summary"] = f"累计提取 {len(result['extracted_facts'])} 项可回溯事实"
        elif item.get("stage") == "hypothesized":
            item["summary"] = f"累计保留 {len(result['hypotheses'])} 个竞争候选"
    return result


def apply_model_enrichment(
    investigation: Mapping[str, Any], enriched: Mapping[str, Any]
) -> Dict[str, Any]:
    """Attach validated model hypotheses without overwriting deterministic facts."""

    result = dict(investigation)
    result["mode"] = "ai_enriched"
    result["capability_notice"] = (
        "当前使用本地规则、结构化知识和大模型增强；自动日志采集器和设备查询工具未连接。"
    )
    for intake in result.get("intake", []):
        intake["redacted_for_external_model"] = True
    valid_evidence = {
        str(item.get("id", "")) for item in result.get("evidence", []) if item.get("id")
    }
    hypotheses = list(result.get("hypotheses", []))
    for item in enriched.get("candidate_causes", [])[:5]:
        if not isinstance(item, Mapping):
            continue
        references = [
            str(reference)
            for reference in item.get("evidence_ids", [])
            if str(reference) in valid_evidence
        ]
        if not references:
            continue
        title = str(item.get("title", "待确认原因"))[:300]
        hypotheses.append(
            {
                "id": _digest("H-AI-", title.lower()),
                "title": title,
                "object": "事件涉及设备或路径",
                "status": str(item.get("status", "candidate")),
                "supporting_evidence_ids": references,
                "counter_evidence_ids": [],
                "known_counter_conditions": [str(item.get("counter_evidence", "需继续验证"))],
                "missing_evidence": [str(value) for value in enriched.get("missing_information", [])[:5]],
                "confirm_checks": [],
                "falsify_checks": [str(item.get("counter_evidence", "需寻找反证"))],
                "knowledge_card_id": "",
                "knowledge_card_version": "",
                "generated_by": "model_enhanced",
                "basis": "模型基于已提取事实和召回知识形成；引用已经过程序校验",
            }
        )
    result["hypotheses"] = _unique(hypotheses, "id")[:12]
    result["model_observation"] = {
        "status": "accepted",
        "impact_summary": str(enriched.get("impact_summary", ""))[:600],
        "prompt_contract": PROMPT_CONTRACTS["hypothesis"],
        "accepted_hypothesis_count": len(result["hypotheses"]) - len(investigation.get("hypotheses", [])),
        "limitations": ["模型未执行真实查询", "模型不能覆盖身份、许可和确定性事实"],
    }
    result["conclusion"] = dict(result.get("conclusion", {}))
    accepted_model_hypotheses = [
        item for item in result["hypotheses"] if item.get("generated_by") == "model_enhanced"
    ]
    stronger_model = next(
        (item for item in accepted_model_hypotheses if item.get("status") == "high_likelihood"),
        None,
    )
    if stronger_model and result["conclusion"].get("grade") in {"candidate", "insufficient"}:
        result["conclusion"]["grade"] = "high_likelihood"
        result["conclusion"]["leading_hypothesis_id"] = stronger_model["id"]
        result["conclusion"]["leading_hypothesis"] = stronger_model["title"]
        result["conclusion"]["uncertainty"] = (
            "大模型基于现有证据提高了该候选优先级，但尚未通过真实工具或人工检查确认。"
        )
    result["conclusion"]["generated_by"] = "rules_knowledge_and_model"
    result["conclusion"]["updated_at"] = utc_now()
    result["trace"] = list(result.get("trace", []))
    result["trace"].insert(
        max(0, len(result["trace"]) - 1),
        {
            "id": "TRACE-AI",
            "stage": "model_enriched",
            "title": "大模型增强候选",
            "state": "inferred",
            "summary": f"模型新增 {result['model_observation']['accepted_hypothesis_count']} 个有证据引用的候选",
            "output_ids": [
                item["id"] for item in result["hypotheses"] if item.get("generated_by") == "model_enhanced"
            ],
            "limitation": "模型未执行工具检查，不能把候选提升为已确认",
            "created_at": utc_now(),
        },
    )
    return result


def legacy_investigation(incident: Mapping[str, Any]) -> Dict[str, Any]:
    """Expose old events honestly instead of fabricating historical reasoning."""

    return {
        "schema_version": "1.0.0",
        "mode": "legacy_untraced",
        "legacy": True,
        "capability_notice": "该事件创建于调查追踪上线前；系统无法还原当时未保存的中间步骤。",
        "simulation": False,
        "prompt_contracts": {},
        "intake": [],
        "evidence": [],
        "field_provenance": [],
        "extracted_facts": [],
        "rule_matches": [],
        "knowledge_retrieval": {"mode": "none", "coverage": "unknown", "cards": []},
        "correlation": {
            "key": str(incident.get("correlation_key", "")),
            "level": "unknown",
            "reason": "历史事件未保存关联推导。",
            "inputs_used": [],
            "limitations": ["不能根据当前结果反推历史分析过程"],
        },
        "hypotheses": [],
        "verification_plan": [],
        "conclusion": {
            "grade": "insufficient",
            "confirmed_facts": [],
            "leading_hypothesis_id": "",
            "leading_hypothesis": "历史调查过程不可用",
            "uncertainty": "只能查看旧版结果，不能审计其推导过程。",
            "next_step_id": "",
            "next_step": "如需继续处理，请补充一条新的日志、告警或现场观察。",
            "generated_by": "legacy",
            "updated_at": str(incident.get("updated_at", "")),
        },
        "trace": [],
    }
