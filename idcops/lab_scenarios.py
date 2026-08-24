"""Reusable multiplatform scenarios sent through the public lab contract."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List


SCENARIOS = {
    "network-module-cascade": {
        "id": "network-module-cascade",
        "name": "网络模块/端口故障引发服务器与应用连锁异常",
        "description": "不提供共同事故号，依靠端口到服务器到应用的真实拓扑与时间窗口关联。",
        "hidden_truth": "交换机端口或模块路径异常",
        "events": [
            {
                "source_system": "network_nms",
                "occurred_at": "2026-08-24T10:00:00+08:00",
                "site": "BJYZ",
                "entity": {
                    "device_name": "HB-BJYZD2SC-ADC-S1",
                    "interface": "HundredGigE7/0/36",
                    "device_type": "switch",
                },
                "signal_type": "link_flap_crc",
                "severity": "critical",
                "summary": "交换机端口反复抖动并出现CRC错误",
                "raw_payload": {
                    "message": "HundredGigE7/0/36 link flap 17 times; CRC errors 4281",
                    "oper_status": "down",
                    "flap_count_10m": 17,
                    "crc_errors": 4281,
                    "optical_rx_power_dbm": -18.7,
                },
            },
            {
                "source_system": "bmc_redfish",
                "occurred_at": "2026-08-24T10:00:20+08:00",
                "site": "BJYZ",
                "entity": {
                    "sn": "SERVER-SN-20260824-001",
                    "device_name": "bjyz-app-001",
                    "rack_position": "BJYZD2SC-A-08-10",
                    "device_type": "server",
                },
                "signal_type": "nic_link_down",
                "severity": "warning",
                "summary": "服务器仍通电但业务网卡链路中断",
                "raw_payload": {
                    "message": "PowerState=On; PSU1=OK; PSU2=OK; NIC1 Link=Down",
                    "power_state": "on",
                    "psu_health": "ok",
                    "nic_link": "down",
                },
            },
            {
                "source_system": "linux_app",
                "occurred_at": "2026-08-24T10:00:40+08:00",
                "site": "BJYZ",
                "entity": {
                    "sn": "SERVER-SN-20260824-001",
                    "device_name": "bjyz-app-001",
                    "device_type": "server",
                },
                "signal_type": "network_carrier_lost",
                "severity": "critical",
                "summary": "Linux检测到网卡carrier丢失",
                "raw_payload": {
                    "message": "kernel: bond0: link status definitely down; eth0 carrier lost"
                },
            },
            {
                "source_system": "linux_app",
                "occurred_at": "2026-08-24T10:01:05+08:00",
                "site": "BJYZ",
                "entity": {
                    "asset_id": "service:order-api",
                    "device_name": "order-api",
                    "device_type": "application",
                },
                "signal_type": "connection_timeout",
                "severity": "critical",
                "summary": "订单服务连接下游持续超时",
                "raw_payload": {
                    "message": "order-api: upstream connection timeout after network carrier lost"
                },
            },
        ],
    },
    "cooling-row-cascade": {
        "id": "cooling-row-cascade",
        "name": "制冷异常引发一排服务器温度与降频告警",
        "description": "动环、BMC和Linux信号共同构成热问题时间线。",
        "hidden_truth": "机房制冷区域异常",
        "events": [
            {
                "source_system": "facility_dcim",
                "occurred_at": "2026-08-24T11:00:00+08:00",
                "site": "BJYZ",
                "entity": {"asset_id": "zone:BJYZ-D2SC-A", "device_type": "facility"},
                "signal_type": "cooling_temperature_high",
                "severity": "critical",
                "summary": "A排冷通道温度持续升高",
                "raw_payload": {"message": "cold aisle temperature 34.2C", "temperature_c": 34.2},
            },
            {
                "source_system": "bmc_redfish",
                "occurred_at": "2026-08-24T11:00:30+08:00",
                "site": "BJYZ",
                "entity": {"sn": "SERVER-SN-20260824-001", "device_type": "server"},
                "signal_type": "thermal_fan_high",
                "severity": "critical",
                "summary": "服务器进风温度高且风扇满速",
                "raw_payload": {"message": "Inlet Temp 36C; Fan PWM 100%"},
            },
            {
                "source_system": "linux_app",
                "occurred_at": "2026-08-24T11:01:00+08:00",
                "site": "BJYZ",
                "entity": {"sn": "SERVER-SN-20260824-001", "device_type": "server"},
                "signal_type": "cpu_thermal_throttle",
                "severity": "warning",
                "summary": "系统检测到CPU热降频",
                "raw_payload": {"message": "CPU thermal throttling activated"},
            },
        ],
    },
}


def list_scenarios() -> List[Dict[str, Any]]:
    return [
        {key: copy.deepcopy(value[key]) for key in ("id", "name", "description")}
        for value in SCENARIOS.values()
    ]


def scenario_events(scenario_id: str) -> List[Dict[str, Any]]:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise ValueError("模拟接入场景不存在")
    run_id = uuid.uuid4().hex[:8].upper()
    events = []
    for index, raw in enumerate(scenario["events"], start=1):
        event = copy.deepcopy(raw)
        event["source_event_id"] = f"LAB-{scenario_id}-{run_id}-{index:02d}"
        event["scenario_id"] = scenario_id
        events.append(event)
    return events
