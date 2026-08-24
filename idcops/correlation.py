"""Deterministic correlation for platform events; no model-created identity."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping


FAMILY_TERMS = {
    "network_path": (
        "link",
        "port",
        "crc",
        "optical",
        "carrier",
        "network",
        "connection_timeout",
        "connection_lost",
        "unreachable",
        "nic_",
    ),
    "thermal_cooling": (
        "temperature",
        "thermal",
        "cooling",
        "fan_",
        "overheat",
        "throttle",
    ),
    "power_path": ("power", "psu", "feed_", "outage", "voltage", "shutdown"),
    "storage_path": ("disk", "nvme", "smart", "storage", "io_error", "raid"),
}


def signal_family(signal_type: str) -> str:
    lowered = str(signal_type or "").strip().lower()
    for family, terms in FAMILY_TERMS.items():
        if any(term in lowered for term in terms):
            return family
    return "unknown"


def identity_candidates(entity: Mapping[str, Any]) -> list:
    candidates = []
    for prefix, field in (
        ("sn", "sn"),
        ("asset", "asset_id"),
        ("name", "name"),
        ("ip", "ip"),
    ):
        value = str(entity.get(field) or "").strip()
        if value:
            candidates.append(f"{prefix}:{value}")
    name = str(entity.get("name") or "").strip()
    interface = str(entity.get("interface") or "").strip()
    if name and interface:
        candidates.insert(0, f"interface:{name}|{interface}")
    return candidates


def time_bucket(value: str, minutes: int = 10) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    seconds = int(parsed.timestamp())
    width = max(1, minutes) * 60
    return str(seconds // width)


def stable_incident_key(parts: Iterable[str]) -> str:
    material = "|".join(str(item) for item in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16].upper()
    return "LAB-AUTO-" + digest


def correlation_result(
    event: Mapping[str, Any], matched_entities: list, component: list
) -> Dict[str, Any]:
    explicit = str(event.get("explicit_incident_key") or "").strip()
    if explicit:
        return {
            "incident_key": explicit,
            "level": "explicit_incident_key",
            "deterministic": True,
            "reason": "来源平台提供了相同事故编号；只表示属于同一外部事故，不等于已确认共同根因。",
            "identity_path": [],
            "signal_family": signal_family(str(event.get("signal_type") or "")),
        }
    family = signal_family(str(event.get("signal_type") or ""))
    bucket = time_bucket(str(event.get("occurred_at") or ""))
    if family == "unknown":
        return {
            "incident_key": "",
            "level": "insufficient",
            "deterministic": False,
            "reason": "没有事故编号，且信号类型不属于可确定关联的信号族；保留为独立事件。",
            "identity_path": matched_entities,
            "signal_family": family,
        }
    if component:
        root = sorted(component)[0]
        return {
            "incident_key": stable_incident_key(
                [str(event.get("site") or ""), family, root, bucket]
            ),
            "level": "topology_time_window",
            "deterministic": True,
            "reason": "精确实体命中资产拓扑，并与同一信号族处于10分钟窗口；按拓扑组件建立事故关联。",
            "identity_path": component,
            "signal_family": family,
        }
    candidates = identity_candidates(event.get("entity", {}))
    if candidates:
        return {
            "incident_key": stable_incident_key(
                [str(event.get("site") or ""), family, candidates[0], bucket]
            ),
            "level": "exact_identity_time_window",
            "deterministic": True,
            "reason": "来源提供精确设备身份，并与同一信号族处于10分钟窗口；按设备建立事故关联。",
            "identity_path": candidates[:1],
            "signal_family": family,
        }
    return {
        "incident_key": "",
        "level": "insufficient",
        "deterministic": False,
        "reason": "缺少事故编号、精确设备身份和拓扑关系，不能自动关联。",
        "identity_path": [],
        "signal_family": family,
    }
