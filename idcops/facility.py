"""Deterministic facility criticality and CC assessment.

The model may extract event hints, but only this rule matrix can emit an
automatic CC reminder. Missing or conflicting identity is never guessed.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional, Tuple

from .models import NormalizedInput, RuleAnalysis, utc_now


ASSESSMENT_VERSION = "facility-cc-v1.0.0"
DECISION_RANK = {"not_required": 0, "needs_confirmation": 1, "required": 2}
VALID_CRITICALITY = {"core", "normal", "unknown"}
VALID_IMPACT = {"alarm_only", "redundancy_degraded", "partial_outage", "widespread_outage"}


def _text(event: NormalizedInput) -> str:
    return "\n".join(part for part in (event.summary, event.raw_text) if part)


def _normalized(value: Any, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else fallback


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是", "required"}


def _event_subtype(event: NormalizedInput, text: str) -> str:
    provided = str(event.labels.get("event_subtype", "")).strip().lower()
    if provided:
        return provided
    checks = (
        ("water_caused_core_device_failure", r"(?:漏水|进水|water leak).{0,80}(?:核心交换机|core switch).{0,30}(?:宕机|故障|离线|unreachable|down)"),
        ("dual_feed_loss", r"双路.{0,10}(?:掉电|断电|中断)|feed\s*A\s*lost.{0,40}feed\s*B\s*lost"),
        ("single_feed_loss", r"单路.{0,10}(?:掉电|断电|中断)|(?:utility\s+)?feed\s*[AB]\s*lost"),
        ("core_switch_outage", r"核心交换机.{0,24}(?:宕机|故障|离线|不可达)|core\s+switch.{0,24}(?:down|unreachable|offline|failed)"),
        ("water_leak", r"漏水|渗水|进水|积水|water\s+leak|leak\s+alarm"),
        ("smoke_alarm", r"烟雾|火灾|smoke\s+detector|fire\s+alarm"),
        ("temperature_rising", r"高温|温度.{0,10}(?:升高|过高|异常)|temperature.{0,12}(?:rising|high|critical)"),
        ("power_supply_failure", r"power\s+supply.{0,16}(?:failed|lost)|PSU\d*.{0,12}(?:failed|lost)"),
    )
    for subtype, pattern in checks:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            return subtype
    return "general_incident"


def _event_category(subtype: str, event: NormalizedInput, text: str) -> str:
    provided = str(event.labels.get("event_category", "")).strip().lower()
    if provided:
        return provided
    if subtype in {"single_feed_loss", "dual_feed_loss", "power_supply_failure"}:
        return "power"
    if subtype in {"water_leak", "water_caused_core_device_failure"}:
        return "water"
    if subtype == "smoke_alarm":
        return "fire_environment"
    if subtype == "temperature_rising":
        return "cooling"
    if subtype == "core_switch_outage" or event.device.device_type == "switch":
        return "network"
    if re.search(r"BGP|OSPF|LACP|MLAG|link\s+(?:is\s+)?down|interface.{0,16}down", text, re.IGNORECASE):
        return "network"
    return "general"


def _impact(event: NormalizedInput, text: str) -> str:
    provided = _normalized(event.labels.get("impact_level"), VALID_IMPACT, "")
    if provided:
        return provided
    if re.search(r"(?:48|大量|大面积|多台|多个|multiple).{0,30}(?:下联|downstream|设备|racks?).{0,20}(?:down|中断|宕机|不可达|without)", text, re.IGNORECASE):
        return "widespread_outage"
    if re.search(r"冗余.{0,12}(?:降低|丢失)|redundancy.{0,16}(?:lost|changed|degraded)|remains\s+up\s+with\s+1\s+of\s+2|feed\s+B\s+(?:carrying|healthy)", text, re.IGNORECASE):
        return "redundancy_degraded"
    if re.search(r"(?:设备|交换机|server|switch).{0,20}(?:宕机|离线|unreachable|offline|down)", text, re.IGNORECASE):
        return "partial_outage"
    return "alarm_only"


def _profile(event: NormalizedInput, stored: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    provided = _normalized(event.labels.get("facility_criticality"), VALID_CRITICALITY, "")
    stored_criticality = (
        _normalized(stored.get("criticality"), VALID_CRITICALITY, "unknown")
        if stored
        else ""
    )
    if provided in {"core", "normal"}:
        if stored_criticality in {"core", "normal"} and stored_criticality != provided:
            return {
                "site": event.site,
                "display_name": str(stored.get("display_name") or event.site),
                "criticality": "unknown",
                "source": "conflict",
                "reported_criticality": provided,
                "stored_criticality": stored_criticality,
            }
        return {
            "site": event.site,
            "display_name": str(event.labels.get("facility_name") or event.site),
            "criticality": provided,
            "source": str(event.labels.get("facility_criticality_source") or "event_input"),
        }
    if stored:
        return {
            "site": event.site,
            "display_name": str(stored.get("display_name") or event.site),
            "criticality": _normalized(stored.get("criticality"), VALID_CRITICALITY, "unknown"),
            "source": str(stored.get("source") or "local_config"),
        }
    if provided == "unknown":
        return {
            "site": event.site,
            "display_name": str(event.labels.get("facility_name") or event.site),
            "criticality": "unknown",
            "source": str(event.labels.get("facility_criticality_source") or "event_input"),
        }
    return {
        "site": event.site,
        "display_name": event.site,
        "criticality": "unknown",
        "source": "not_provided",
    }


def assess_facility_event(
    event: NormalizedInput,
    analysis: RuleAnalysis,
    stored_profile: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return an auditable three-state CC decision."""

    text = _text(event)
    profile = _profile(event, stored_profile)
    criticality = profile["criticality"]
    subtype = _event_subtype(event, text)
    category = _event_category(subtype, event, text)
    impact = _impact(event, text)
    asset_criticality = _normalized(
        event.labels.get("asset_criticality"), VALID_CRITICALITY, "unknown"
    )
    explicit = analysis.cc_required or _truthy(event.labels.get("sop_threshold_met"))

    decision = "not_required"
    rule_id = "CC-GENERAL-NOT-MATCHED"
    reason = "当前证据没有命中已配置的 CC 条件；故障仍需按普通流程调查和处置"
    missing = []

    if explicit:
        decision = "required"
        rule_id = "CC-EXPLICIT-CONFIRMATION"
        reason = "监控、现场、接口人或既有 SOP 已明确确认需要 CC 通报"
    elif profile.get("source") == "conflict":
        decision = "needs_confirmation"
        rule_id = "CC-FACILITY-CRITICALITY-CONFLICT"
        reason = "本次输入的机房等级与已有机房档案冲突，不能自动决定 CC"
        missing.append(
            "确认机房等级：本次输入为"
            f"{profile.get('reported_criticality')}，档案为{profile.get('stored_criticality')}"
        )
    elif criticality == "core" and subtype == "single_feed_loss":
        decision = "required"
        rule_id = "CC-CORE-SINGLE-FEED"
        reason = "核心机房发生单路掉电，命中已确认的 CC 规则"
    elif criticality == "core" and subtype == "dual_feed_loss":
        decision = "required"
        rule_id = "CC-CORE-DUAL-FEED"
        reason = "核心机房发生双路掉电，命中最高等级 CC 规则"
    elif subtype == "water_caused_core_device_failure" or (
        category == "water"
        and asset_criticality == "core"
        and impact in {"partial_outage", "widespread_outage"}
    ):
        decision = "required"
        rule_id = "CC-WATER-CORE-ASSET-OUTAGE"
        reason = "漏水已经导致核心设备故障"
    elif subtype == "core_switch_outage" and impact == "widespread_outage" and (
        criticality == "core" or asset_criticality == "core"
    ):
        decision = "required"
        rule_id = "CC-CORE-NETWORK-WIDESPREAD"
        reason = "核心交换机宕机并造成大范围下联中断"
    elif subtype in {"dual_feed_loss", "core_switch_outage"} and criticality == "unknown":
        decision = "needs_confirmation"
        rule_id = "CC-FACILITY-CRITICALITY-UNKNOWN"
        reason = "事件影响较大，但机房等级尚未确认"
        missing.append("机房等级")
    elif criticality == "normal" and subtype == "dual_feed_loss":
        decision = "needs_confirmation"
        rule_id = "CC-NORMAL-DUAL-FEED-CONFIRM"
        reason = "普通机房双路掉电需要按既有 SOP 或接口人意见确认是否 CC"
    elif subtype == "core_switch_outage" and impact != "widespread_outage":
        decision = "needs_confirmation"
        rule_id = "CC-CORE-NETWORK-IMPACT-CONFIRM"
        reason = "核心网络设备异常，但尚未确认大范围业务影响"
        missing.append("下联与业务影响范围")
    elif category == "fire_environment":
        decision = "needs_confirmation"
        rule_id = "CC-FIRE-SOP-CONFIRM"
        reason = "烟雾或消防事件按最高风险展示，CC 需由现有 SOP 或人工确认"
        missing.append("消防事件现场确认或适用 SOP")

    evidence = []
    if event.summary:
        evidence.append({"source": event.source, "text": event.summary[:360]})
    if event.raw_text and event.raw_text != event.summary:
        evidence.append({"source": event.source, "text": event.raw_text.splitlines()[0][:360]})

    return {
        "decision": decision,
        "reason": reason,
        "matched_rule_id": rule_id,
        "rule_version": ASSESSMENT_VERSION,
        "facility": profile,
        "event": {
            "category": category,
            "subtype": subtype,
            "impact_level": impact,
            "asset_criticality": asset_criticality,
        },
        "evidence": evidence,
        "missing_evidence": missing,
        "evaluated_at": utc_now(),
    }


def strongest_assessment(
    existing: Optional[Mapping[str, Any]], current: Mapping[str, Any]
) -> Dict[str, Any]:
    """Keep the strongest decision while preserving the latest impact details."""

    if not existing:
        return dict(current)
    old_rank = DECISION_RANK.get(str(existing.get("decision")), -1)
    new_rank = DECISION_RANK.get(str(current.get("decision")), -1)
    if old_rank > new_rank:
        return dict(existing)
    return dict(current)
