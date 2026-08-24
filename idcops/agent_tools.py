"""Allow-listed read-only tools available to the incident investigation agent."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from .lab import IntegrationLab


TOOL_DEFINITIONS = {
    "network.query_port": {
        "platform": "network_nms",
        "description": "查询交换机端口、抖动、CRC、光功率和链路状态",
    },
    "network.query_peer": {
        "platform": "network_nms",
        "description": "查询同一网络事故中的对端和相关端口记录",
    },
    "bmc.query_health": {
        "platform": "bmc_redfish",
        "description": "查询服务器电源、SEL、温度、风扇、电源和网卡健康",
    },
    "facility.query_environment": {
        "platform": "facility_dcim",
        "description": "查询温度、供电、漏水、烟雾和制冷告警",
    },
    "linux.query_logs": {
        "platform": "linux_app",
        "description": "查询同一事故的Linux、内核、服务和应用日志",
    },
    "oms.query_asset": {
        "platform": "oms_cmdb",
        "description": "查询完整SN、机架位、业务状态、许可和资产关系",
    },
    "onsite.query_observation": {
        "platform": "onsite_feedback",
        "description": "查询现场观察与已回填的检查结果",
    },
}

ALLOWED_ARGUMENTS = {"incident_id", "entity_key", "limit"}


class AgentToolRegistry:
    def __init__(self, lab: IntegrationLab) -> None:
        self.lab = lab

    def list_tools(self) -> list:
        return [
            {
                "name": name,
                "description": item["description"],
                "platform": item["platform"],
                "read_only": True,
                "allowed_arguments": sorted(ALLOWED_ARGUMENTS),
            }
            for name, item in TOOL_DEFINITIONS.items()
        ]

    def execute(self, tool_name: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
        definition = TOOL_DEFINITIONS.get(str(tool_name))
        if definition is None:
            raise ValueError("AI请求了未授权工具")
        unknown = set(str(key) for key in arguments) - ALLOWED_ARGUMENTS
        if unknown:
            raise ValueError("AI工具参数包含未授权字段")
        result = self.lab.query_platform(
            definition["platform"],
            incident_id=str(arguments.get("incident_id") or ""),
            entity_key=str(arguments.get("entity_key") or ""),
            limit=int(arguments.get("limit") or 50),
        )
        return {
            "tool": tool_name,
            "platform": definition["platform"],
            "read_only": True,
            **result,
        }
