"""Shared event contract for simulated and future real platform adapters."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping

from .models import utc_now


PLATFORM_DEFINITIONS = (
    {
        "platform_key": "facility_dcim",
        "display_name": "动环 / DCIM",
        "platform_type": "facility",
        "ingest_source": "monitor",
        "description": "温度、供电、漏水、烟雾、空调与机房区域告警",
    },
    {
        "platform_key": "network_nms",
        "display_name": "网络 NMS / syslog",
        "platform_type": "network",
        "ingest_source": "monitor",
        "description": "交换机、端口、模块、链路、光功率与协议告警",
    },
    {
        "platform_key": "bmc_redfish",
        "display_name": "BMC / Redfish",
        "platform_type": "hardware",
        "ingest_source": "monitor",
        "description": "服务器电源、SEL、风扇、温度、磁盘、内存与网卡状态",
    },
    {
        "platform_key": "linux_app",
        "display_name": "Linux / 应用监控",
        "platform_type": "system",
        "ingest_source": "log",
        "description": "journald、内核、服务、应用日志、指标与 Trace 摘要",
    },
    {
        "platform_key": "oms_cmdb",
        "display_name": "OMS / CMDB",
        "platform_type": "asset",
        "ingest_source": "monitor",
        "description": "工单、完整 SN、机架位、业务状态、许可与资产拓扑",
    },
    {
        "platform_key": "onsite_feedback",
        "display_name": "现场反馈",
        "platform_type": "onsite",
        "ingest_source": "onsite",
        "description": "现场观察、完整 SN、UID、线缆、光功率与操作结果",
    },
)

PLATFORMS = {item["platform_key"]: item for item in PLATFORM_DEFINITIONS}
SEVERITIES = {"info", "warning", "critical", "unknown"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _mapping(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _raw_text(raw_payload: Any, summary: str) -> str:
    if isinstance(raw_payload, str):
        return raw_payload.strip() or summary
    if isinstance(raw_payload, Mapping):
        for key in ("message", "log_text", "observation", "description", "text"):
            value = _text(raw_payload.get(key))
            if value:
                return value
    if raw_payload not in (None, {}, []):
        return json.dumps(raw_payload, ensure_ascii=False, separators=(",", ":"))
    return summary


def normalize_platform_event(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one platform event and produce the existing incident-ingest payload."""

    platform_key = _text(payload.get("source_system") or payload.get("platform_key"))
    if platform_key not in PLATFORMS:
        raise ValueError("来源平台必须是已登记的六类平台之一")
    source_event_id = _text(payload.get("source_event_id"))
    if not source_event_id:
        raise ValueError("来源事件 ID 不能为空")
    site = _text(payload.get("site")).upper()
    if not site:
        raise ValueError("机房编码不能为空")
    signal_type = _text(payload.get("signal_type"))
    if not signal_type:
        raise ValueError("信号类型不能为空")
    summary = _text(payload.get("summary"))
    if not summary:
        raise ValueError("事件摘要不能为空")
    severity = _text(payload.get("severity")).lower() or "unknown"
    if severity not in SEVERITIES:
        raise ValueError("严重级别必须是 info、warning、critical 或 unknown")

    entity = _mapping(payload.get("entity"))
    normalized_entity = {
        "sn": _text(entity.get("sn")),
        "name": _text(entity.get("device_name") or entity.get("name")),
        "ip": _text(entity.get("management_ip") or entity.get("ip")),
        "rack_position": _text(entity.get("rack_position")),
        "interface": _text(entity.get("interface")),
        "device_type": _text(entity.get("device_type")) or PLATFORMS[platform_key]["platform_type"],
        "asset_id": _text(entity.get("asset_id")),
    }
    raw_payload = payload.get("raw_payload")
    if raw_payload is None:
        raw_payload = {}
    provenance = {
        key: {
            "source": "platform_provided" if value else "unknown",
            "platform": platform_key,
        }
        for key, value in normalized_entity.items()
    }
    incident_key = _text(payload.get("incident_key"))
    occurred_at = _text(payload.get("occurred_at")) or utc_now()
    ingest_payload = {
        "event_time": occurred_at,
        "site": site,
        "severity": severity,
        "sn": normalized_entity["sn"],
        "device_name": normalized_entity["name"],
        "ip": normalized_entity["ip"],
        "rack_position": normalized_entity["rack_position"],
        "device_type": normalized_entity["device_type"],
        "summary": summary,
        "raw_text": _raw_text(raw_payload, summary),
        "incident_key": incident_key,
        "source_system": platform_key,
        "labels": {
            "source_event_id": source_event_id,
            "signal_type": signal_type,
            "entity_interface": normalized_entity["interface"],
            "entity_asset_id": normalized_entity["asset_id"],
            "platform_simulation": True,
        },
        "is_demo": True,
        "demo_id": _text(payload.get("scenario_id")) or "platform-lab",
    }
    if platform_key == "onsite_feedback":
        ingest_payload["observation"] = ingest_payload["raw_text"]
    elif PLATFORMS[platform_key]["ingest_source"] == "log":
        ingest_payload["log_text"] = ingest_payload["raw_text"]
    else:
        ingest_payload["message"] = ingest_payload["raw_text"]

    return {
        "platform_key": platform_key,
        "source_event_id": source_event_id,
        "occurred_at": occurred_at,
        "site": site,
        "explicit_incident_key": incident_key,
        "entity": normalized_entity,
        "signal_type": signal_type,
        "severity": severity,
        "summary": summary,
        "raw_payload": raw_payload,
        "field_provenance": provenance,
        "ingest_source": PLATFORMS[platform_key]["ingest_source"],
        "ingest_payload": ingest_payload,
        "simulation": True,
    }
