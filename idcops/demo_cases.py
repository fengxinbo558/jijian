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
}


def list_demos() -> List[Dict[str, str]]:
    return [
        {"id": key, "name": value["name"], "description": value["description"]}
        for key, value in DEMO_CASES.items()
    ]

