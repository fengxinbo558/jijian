"""Built-in product demonstrations. Independent evaluation cases live in evals/."""

from __future__ import annotations

from typing import Any, Dict, List


DEMO_CASES: Dict[str, Dict[str, Any]] = {
    "heat-wave": {
        "name": "高温与多机风扇升高",
        "description": "三台服务器同一时间出现温度与风扇异常，并已由现场规则确认需要CC通报。",
        "inputs": [
            {
                "source": "monitor",
                "site": "BJYZ",
                "severity": "critical",
                "sn": "G3M02179543",
                "device_name": "bjyz-scloud-dun-ndstest1",
                "rack_position": "BJYZD2SC-A-08-10",
                "device_type": "server",
                "summary": "冷通道温度持续升高，服务器风扇进入高速",
                "message": "temperature high 30.8C; fan speed high 91%",
                "incident_key": "DEMO-HEAT-01",
                "cc_required": True,
                "uid_status": "on",
                "power_permission": "forbidden",
                "interface_team": "SIM",
            },
            {
                "source": "monitor",
                "site": "BJYZ",
                "severity": "critical",
                "sn": "G3M02179544",
                "device_name": "bjyz-scloud-dun-ndstest2",
                "rack_position": "BJYZD2SC-A-08-11",
                "device_type": "server",
                "summary": "相邻服务器温度升高",
                "message": "temperature rising; fan full speed",
                "incident_key": "DEMO-HEAT-01",
                "power_permission": "forbidden",
            },
            {
                "source": "onsite",
                "site": "BJYZ",
                "severity": "critical",
                "sn": "G3M02179545",
                "device_name": "bjyz-scloud-dun-ndstest3",
                "rack_position": "BJYZD2SC-A-08-12",
                "device_type": "server",
                "summary": "现场发现同一冷通道明显偏热",
                "observation": "现场体感高温，三台机器风扇满转，已联系动环",
                "incident_key": "DEMO-HEAT-01",
                "power_permission": "forbidden",
            },
        ],
    },
    "disk-io": {
        "name": "磁盘 I/O 与 SMART 异常",
        "description": "系统日志同时出现介质 I/O 错误和 SMART 失败提示。",
        "inputs": [
            {
                "source": "log",
                "site": "BJYZ",
                "severity": "critical",
                "sn": "CBN53700J12A",
                "device_name": "bjyz-baihua-offline-af5-master22",
                "rack_position": "BJYZD243-B-12-12",
                "device_type": "server",
                "summary": "服务器磁盘持续报错",
                "log_text": (
                    "kernel: blk_update_request: I/O error, dev sdb, sector 918273\n"
                    "smartd: Device /dev/sdb: SMART Failure Predicted\n"
                    "systemd: data.mount remounting filesystem read-only"
                ),
                "uid_status": "on",
                "from_reinstall": "no",
                "power_permission": "confirm",
                "interface_team": "SIM",
            }
        ],
    },
    "memory-mce": {
        "name": "内存 ECC 与主板候选",
        "description": "ECC 与 MCE 指向内存通道，但现场反馈换件后仍无法启动。",
        "inputs": [
            {
                "source": "log",
                "site": "BJYZ",
                "severity": "critical",
                "sn": "MEM20260823001",
                "device_name": "bjyz-compute-042",
                "rack_position": "BJYZD2MC-C-04-02",
                "device_type": "server",
                "summary": "CPU1_B0 出现不可纠正内存错误",
                "log_text": "EDAC MC0: Uncorrected memory error on CPU1 channel B DIMM 0; Machine Check Exception",
                "incident_key": "DEMO-MEM-01",
                "uid_status": "on",
                "from_reinstall": "yes",
            },
            {
                "source": "onsite",
                "site": "BJYZ",
                "severity": "critical",
                "sn": "MEM20260823001",
                "device_name": "bjyz-compute-042",
                "rack_position": "BJYZD2MC-C-04-02",
                "device_type": "server",
                "summary": "更换对应内存后机器仍无法启动",
                "observation": "内存条已替换且重新插拔，机器仍不开机，需要保留主板故障候选",
                "incident_key": "DEMO-MEM-01",
                "uid_status": "on",
                "from_reinstall": "yes",
            },
        ],
    },
    "network-optic": {
        "name": "网络链路与光模块排查",
        "description": "交换机端口 down，现场补充光功率测试现象。",
        "inputs": [
            {
                "source": "monitor",
                "site": "BJYZ",
                "severity": "critical",
                "sn": "2102114189P0G3000003",
                "device_name": "HB-BJYZD2MC-CIC1-CE12816-41.Int",
                "rack_position": "BJYZD2MC-A-06-01",
                "device_type": "switch",
                "summary": "本端端口 40GE5/0/3 链路中断",
                "message": "interface 40GE5/0/3 link down; optical power alarm",
                "incident_key": "DEMO-NET-01",
                "uid_status": "unknown",
                "power_permission": "forbidden",
                "interface_team": "网络组",
            },
            {
                "source": "onsite",
                "site": "BJYZ",
                "severity": "warning",
                "sn": "2102114189P0G3000003",
                "device_name": "HB-BJYZD2MC-CIC1-CE12816-41.Int",
                "rack_position": "BJYZD2MC-A-06-01",
                "device_type": "switch",
                "summary": "现场准备测试本端与对端光功率",
                "observation": "模块指示灯异常，需要接口人确认光功率正常范围后逐端测试",
                "incident_key": "DEMO-NET-01",
                "power_permission": "forbidden",
                "interface_team": "网络组",
            },
        ],
    },
    "core-single-feed": {
        "name": "核心机房单路掉电",
        "description": "A路供电中断、B路仍带载；核心机房按已确认矩阵产生一次CC提醒。",
        "inputs": [
            {
                "source": "monitor",
                "site": "CORE-DEMO",
                "severity": "critical",
                "device_type": "facility",
                "summary": "核心机房A路供电中断",
                "message": "[SIMULATED] feed A lost; feed B carrying load; devices remain online",
                "facility_criticality": "core",
                "event_subtype": "single_feed_loss",
                "impact_level": "redundancy_degraded",
                "incident_key": "DEMO-CORE-POWER-01",
            }
        ],
    },
    "normal-single-feed": {
        "name": "普通机房单路掉电",
        "description": "单路供电中断但设备在线；进入动力与接口人处理，不误触发CC。",
        "inputs": [
            {
                "source": "monitor",
                "site": "NORMAL-DEMO",
                "severity": "warning",
                "device_type": "facility",
                "summary": "普通机房A路供电中断",
                "message": "[SIMULATED] feed A lost; feed B healthy; no device outage",
                "facility_criticality": "normal",
                "event_subtype": "single_feed_loss",
                "impact_level": "redundancy_degraded",
            }
        ],
    },
    "water-core-switch": {
        "name": "漏水导致核心交换机故障",
        "description": "动环、NMS与现场证据合并；核心设备已受影响时触发一次CC提醒。",
        "inputs": [
            {
                "source": "monitor",
                "site": "CORE-DEMO",
                "severity": "critical",
                "sn": "SIM-CORE-WATER-SW-001",
                "device_name": "core-water-switch-01",
                "rack_position": "CORE-NET-A-01",
                "device_type": "switch",
                "summary": "漏水区域核心交换机宕机",
                "message": "[SIMULATED] water leak alarm; core switch unreachable; downstream links down",
                "facility_criticality": "core",
                "asset_criticality": "core",
                "event_subtype": "water_caused_core_device_failure",
                "impact_level": "widespread_outage",
                "incident_key": "DEMO-WATER-CORE-01",
                "power_permission": "forbidden",
            },
            {
                "source": "onsite",
                "site": "CORE-DEMO",
                "severity": "critical",
                "sn": "SIM-CORE-WATER-SW-001",
                "device_name": "core-water-switch-01",
                "rack_position": "CORE-NET-A-01",
                "device_type": "switch",
                "summary": "现场确认机柜顶部存在进水痕迹",
                "observation": "[SIMULATED] 核心交换机电源灯灭，禁止现场自行上电",
                "facility_criticality": "core",
                "asset_criticality": "core",
                "event_subtype": "water_caused_core_device_failure",
                "impact_level": "widespread_outage",
                "incident_key": "DEMO-WATER-CORE-01",
                "power_permission": "forbidden",
            },
        ],
    },
    "network-bgp": {
        "name": "BGP邻居会话下降",
        "description": "先区分对端、承载链路、计时器和控制面，不把邻居down直接当硬件损坏。",
        "inputs": [
            {
                "source": "log",
                "site": "NORMAL-DEMO",
                "severity": "critical",
                "sn": "SIM-BGP-SW-001",
                "device_name": "normal-border-01",
                "rack_position": "NORMAL-NET-B-01",
                "device_type": "switch",
                "summary": "BGP邻居中断",
                "log_text": "[SIMULATED] bgp: neighbor 192.0.2.10 state changed Established -> Idle; hold timer expired",
            }
        ],
    },
    "network-lacp": {
        "name": "LACP成员退出但聚合仍在线",
        "description": "识别冗余降低；如果链路已经恢复，不再把排查步骤一条路走到底。",
        "inputs": [
            {
                "source": "log",
                "site": "NORMAL-DEMO",
                "severity": "warning",
                "sn": "SIM-LACP-SW-001",
                "device_name": "normal-tor-lacp-01",
                "rack_position": "NORMAL-NET-L-01",
                "device_type": "switch",
                "summary": "聚合链路冗余降低",
                "log_text": "[SIMULATED] lacp: member Ethernet1/53 removed from Port-Channel10; bundle remains up with 1 of 2 links",
                "power_permission": "forbidden",
            }
        ],
    },
    "network-core-outage": {
        "name": "核心交换机宕机与下联批量中断",
        "description": "三条告警归到一个共同事故，只产生一次CC提醒并保留每台设备。",
        "inputs": [
            {
                "source": "monitor",
                "site": "CORE-DEMO",
                "severity": "critical",
                "sn": "SIM-CORE-NET-SW-001",
                "device_name": "core-switch-01",
                "rack_position": "CORE-NET-C-01",
                "device_type": "switch",
                "summary": "核心交换机不可达且48条下联中断",
                "message": "[SIMULATED] core switch unreachable; 48 downstream links down",
                "facility_criticality": "core",
                "asset_criticality": "core",
                "event_subtype": "core_switch_outage",
                "impact_level": "widespread_outage",
                "incident_key": "DEMO-CORE-NET-01",
            },
            {
                "source": "monitor",
                "site": "CORE-DEMO",
                "severity": "critical",
                "sn": "SIM-CORE-NET-SERVER-01",
                "rack_position": "CORE-SRV-C-01",
                "device_type": "server",
                "summary": "下联服务器网络不可达",
                "message": "[SIMULATED] host network unreachable after upstream loss",
                "facility_criticality": "core",
                "impact_level": "widespread_outage",
                "incident_key": "DEMO-CORE-NET-01",
            },
            {
                "source": "monitor",
                "site": "CORE-DEMO",
                "severity": "critical",
                "sn": "SIM-CORE-NET-SERVER-02",
                "rack_position": "CORE-SRV-C-02",
                "device_type": "server",
                "summary": "另一台下联服务器网络不可达",
                "message": "[SIMULATED] host network unreachable after upstream loss",
                "facility_criticality": "core",
                "impact_level": "widespread_outage",
                "incident_key": "DEMO-CORE-NET-01",
            },
        ],
    },
    "kernel-watchdog": {
        "name": "内核soft lockup与watchdog重启",
        "description": "保留内核、驱动、硬件和I/O候选，不把自动重启直接认定成主板故障。",
        "inputs": [
            {
                "source": "log",
                "site": "NORMAL-DEMO",
                "severity": "critical",
                "sn": "SIM-WATCHDOG-001",
                "rack_position": "NORMAL-SRV-W-01",
                "device_type": "server",
                "summary": "服务器失去响应后自动重启",
                "log_text": "[SIMULATED] kernel: watchdog: BUG: soft lockup - CPU#12 stuck for 32s\n[SIMULATED] kernel: watchdog initiated emergency restart",
            }
        ],
    },
    "application-dns-tls": {
        "name": "应用DNS与TLS依赖异常",
        "description": "分别验证域名解析、网络连接、证书与主机时间，不读取客户源代码。",
        "inputs": [
            {
                "source": "log",
                "site": "NORMAL-DEMO",
                "severity": "warning",
                "sn": "SIM-APP-DNS-TLS-001",
                "rack_position": "NORMAL-APP-D-01",
                "device_type": "server",
                "summary": "内部HTTPS依赖调用失败",
                "log_text": "[SIMULATED] resolver: lookup api.internal: Temporary failure in name resolution\n[SIMULATED] proxy: TLS certificate has expired for fallback.internal",
            }
        ],
    },
    "facility-smoke": {
        "name": "烟雾告警等待SOP确认",
        "description": "按最高风险展示，但没有内部SOP确认前不由AI擅自触发CC。",
        "inputs": [
            {
                "source": "monitor",
                "site": "NORMAL-DEMO",
                "severity": "critical",
                "device_type": "facility",
                "summary": "机柜上方烟雾传感器告警",
                "message": "[SIMULATED] smoke detector zone Z-12 active; no fire confirmation",
                "facility_criticality": "normal",
                "event_subtype": "smoke_alarm",
                "impact_level": "alarm_only",
            }
        ],
    },
}


def list_demos() -> List[Dict[str, str]]:
    return [
        {"id": key, "name": value["name"], "description": value["description"]}
        for key, value in DEMO_CASES.items()
    ]
