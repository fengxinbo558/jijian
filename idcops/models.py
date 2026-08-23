"""Versioned data contracts used by every ingestion path."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping


def utc_now() -> str:
    """Return a compact, timezone-aware timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _mapping(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass
class Device:
    sn: str = ""
    name: str = ""
    rack_position: str = ""
    device_type: str = "unknown"
    ip: str = ""

    @classmethod
    def from_mapping(cls, value: Any) -> "Device":
        data = _mapping(value)
        return cls(
            sn=_text(data.get("sn")),
            name=_text(data.get("name")),
            rack_position=_text(data.get("rack_position")),
            device_type=_text(data.get("device_type")) or "unknown",
            ip=_text(data.get("ip")),
        )

    def identity_key(self) -> str:
        return self.sn or self.name or self.ip or self.rack_position

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperationContext:
    from_reinstall: str = "unknown"
    uid_status: str = "unknown"
    power_permission: str = "unknown"
    interface_person: str = ""
    interface_team: str = ""

    @classmethod
    def from_mapping(cls, value: Any) -> "OperationContext":
        data = _mapping(value)
        return cls(
            from_reinstall=_text(data.get("from_reinstall")) or "unknown",
            uid_status=_text(data.get("uid_status")) or "unknown",
            power_permission=_text(data.get("power_permission")) or "unknown",
            interface_person=_text(data.get("interface_person")),
            interface_team=_text(data.get("interface_team")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedInput:
    source: str
    event_time: str
    site: str
    severity: str
    device: Device
    summary: str
    raw_text: str
    labels: Dict[str, Any] = field(default_factory=dict)
    operation_context: OperationContext = field(default_factory=OperationContext)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NormalizedInput":
        source = _text(value.get("source")) or "unknown"
        if source not in {"monitor", "log", "onsite"}:
            raise ValueError("source must be monitor, log, or onsite")
        severity = _text(value.get("severity")).lower() or "unknown"
        if severity not in {"info", "warning", "critical", "unknown"}:
            severity = "unknown"
        summary = _text(value.get("summary"))
        raw_text = _text(value.get("raw_text"))
        if not summary and not raw_text:
            raise ValueError("summary or raw_text is required")
        return cls(
            source=source,
            event_time=_text(value.get("event_time")) or utc_now(),
            site=_text(value.get("site")).upper(),
            severity=severity,
            device=Device.from_mapping(value.get("device")),
            summary=summary or raw_text.splitlines()[0][:160],
            raw_text=raw_text or summary,
            labels=_mapping(value.get("labels")),
            operation_context=OperationContext.from_mapping(value.get("operation_context")),
        )

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        return value


@dataclass
class RuleAnalysis:
    category: str
    severity: str
    title: str
    requires_onsite: bool
    cc_required: bool
    cc_reason: str
    evidence: List[Dict[str, str]]
    candidate_causes: List[Dict[str, Any]]
    suggestions: List[str]
    missing_information: List[str]
    matched_rules: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

