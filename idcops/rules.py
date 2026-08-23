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
        name="facility_power",
        category="facility",
        title="机房或设备供电异常",
        patterns=(
            r"单路.{0,10}(掉电|断电|中断)|双路.{0,10}(掉电|断电|中断)",
            r"feed\s*[AB]\s*lost|utility feed.{0,12}lost|without input power",
            r"power supply.{0,20}(fail|lost)|PSU\d*.{0,16}(fail|lost)|redundancy changed",
            r"UPS.{0,16}(fail|alarm|bypass)|PDU.{0,16}(fail|trip)|breaker.{0,16}(trip|fault)",
        ),
        causes=("上游供电路径或配电设备异常", "设备电源模块异常", "供电冗余能力降低"),
        suggestions=(
            "联系动力动环确认供电路径、UPS/PDU/ATS和当前冗余状态",
            "确认受影响机房、机柜和设备范围，不把单个电源模块告警等同于机房掉电",
            "根据机房等级和实际影响查看重大事件判断卡",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="facility_water",
        category="facility",
        title="机房漏水或设备进水风险",
        patterns=(
            r"漏水|渗水|进水|积水|water\s+leak|leak\s+alarm",
            r"雨水.{0,16}(进入|影响)|water.{0,24}(rack|cabinet|device)",
        ),
        causes=("管路、屋面或墙体渗漏", "空调排水或冷却系统泄漏", "进水导致设备或供电路径异常"),
        suggestions=(
            "联系动力动环确认漏水位置、范围和发展趋势",
            "确认水是否接触线缆、机柜或设备以及是否已有设备宕机",
            "涉及供电或设备进水时禁止现场自行上电",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="facility_fire",
        category="facility",
        title="烟雾、火灾或消防系统异常",
        patterns=(
            r"烟雾|火灾|smoke\s+detector|fire\s+alarm",
            r"fire suppression|灭火系统|消防系统.{0,12}(动作|告警)",
        ),
        causes=("真实烟雾或火情", "传感器或线路误报", "消防系统联动事件"),
        suggestions=(
            "按现场消防与人身安全SOP立即确认，不等待AI进一步推理",
            "确认告警区域、传感器状态和消防系统是否已经动作",
            "CC是否触发读取现有SOP或人工确认结果",
        ),
        requires_onsite=True,
        severity="critical",
    ),
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
            "核对容量、inode、SMART/NVMe 健康信息和最近 I/O 错误",
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
        name="hardware_bus",
        category="hardware",
        title="PCIe总线或适配器异常",
        patterns=(
            r"AER:|PCIe Bus Error|pcieport.{0,30}(error|fault)",
            r"transmit queue timed out|resetting adapter|adapter reset|firmware reset",
        ),
        causes=("PCIe链路或插卡异常", "网卡/存储适配器固件或驱动异常", "主板插槽或上游Root Port异常"),
        suggestions=(
            "核对AER严重度、BDF地址和关联驱动/设备",
            "检查同一Root Port下其他设备是否同时异常",
            "先由系统组读取日志和设备状态，需插拔硬件时再进入现场安全门",
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
            r"memory pressure|内存不足|内存耗尽|\bOOM\b",
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
        name="system_stability",
        category="system",
        title="内核锁死、watchdog或异常重启",
        patterns=(
            r"soft lockup|hard lockup|watchdog.{0,30}(lockup|restart|reset)",
            r"Kernel panic|panic - not syncing|emergency restart",
        ),
        causes=("内核或驱动路径锁死", "CPU调度、硬件或固件异常", "watchdog保护性重启"),
        suggestions=(
            "保留上一次启动的内核日志、panic信息和boot ID",
            "核对重启前CPU、I/O、驱动和硬件错误，不把watchdog直接等同于主板故障",
            "优先由系统组远程分析；机器无法启动时再转现场",
        ),
        requires_onsite=False,
        severity="critical",
    ),
    Rule(
        name="system_time",
        category="system",
        title="系统时钟或时间同步异常",
        patterns=(
            r"NTP.{0,24}(synchronization lost|unsynchroni[sz]ed|no selectable)",
            r"chrony[d]?.{0,40}(No selectable sources|clock wrong|not synchronized)",
            r"System clock wrong|time sync.{0,16}(failed|lost)",
        ),
        causes=("NTP/Chrony上游不可达", "主机时钟漂移", "时区或采集时间配置不一致"),
        suggestions=(
            "确认NTP/Chrony源、同步状态和当前时间偏移",
            "在关联多来源日志前统一时区并保留原始时间",
            "不要在时间未校正时确认事件因果先后",
        ),
        requires_onsite=False,
        severity="warning",
    ),
    Rule(
        name="network_core_outage",
        category="network",
        title="核心网络设备或大范围下联中断",
        patterns=(
            r"核心交换机.{0,24}(宕机|故障|离线|不可达)",
            r"core\s+switch.{0,24}(down|unreachable|offline|failed)",
            r"(?:\d+|大量|多台|多个).{0,18}(downstream|下联).{0,18}(down|中断|不可达)",
        ),
        causes=("核心交换机本体或供电故障", "核心设备上联/控制面异常", "监控路径或管理面不可达"),
        suggestions=(
            "核对核心设备角色、冗余状态和真实下联影响范围",
            "查询同一事故号下的NMS、syslog、供电和现场证据",
            "命中核心设备大范围中断规则时查看CC提醒，并继续正常调查流程",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="network_redundancy",
        category="network",
        title="网络聚合或双机冗余异常",
        patterns=(
            r"LACP.{0,30}(member|port).{0,20}(down|removed|inactive)|Port-Channel.{0,20}(degraded|member)",
            r"bond\d*.{0,28}(slave|interface).{0,16}down|active slave changed",
            r"MLAG.{0,30}(peer-link down|peer unreachable|dual-active)|vPC.{0,20}(peer.*down|split)",
        ),
        causes=("成员链路或光模块异常", "对端聚合配置或状态异常", "MLAG/vPC对等链路或保活异常"),
        suggestions=(
            "确认聚合是否仍有可用成员以及业务是否实际受影响",
            "对照本端、对端和服务器Bond在同一时间的状态变化",
            "冗余降低不等于业务已中断，但需要在第二条路径失效前处理",
        ),
        requires_onsite=True,
        severity="critical",
    ),
    Rule(
        name="network_control_plane",
        category="network",
        title="网络路由邻居或控制面异常",
        patterns=(
            r"BGP.{0,40}(Established\s*->\s*(?:Idle|Active|Down)|neighbor.{0,20}(down|idle)|hold timer expired)",
            r"OSPF.{0,120}(Full\s*->\s*Down|neighbor.{0,40}down|dead timer expired)",
        ),
        causes=("BGP/OSPF对端或链路异常", "路由协议计时器、认证或配置不一致", "设备控制面负载或进程异常"),
        suggestions=(
            "先由网络组查询邻居状态、最近变更和对端同时间事件",
            "区分单邻居故障与设备级控制面故障，并核对路由是否绕行",
            "协议邻居down不能单独证明本地交换机硬件损坏",
        ),
        requires_onsite=False,
        severity="critical",
    ),
    Rule(
        name="network_layer2",
        category="network",
        title="二层环路、STP保护或MAC漂移",
        patterns=(
            r"BPDU Guard|err-disabled|STP.{0,30}(topology|inconsistent|blocked)",
            r"MAC.{0,30}(flap|flapping|move).{0,40}(port|Ethernet|VLAN)",
        ),
        causes=("二层环路或错误接线", "STP/BPDU保护策略触发", "双归接入、虚拟机迁移或MAC学习异常"),
        suggestions=(
            "确认MAC、VLAN、两个端口及最近接线/配置变化",
            "先由网络组查看STP角色和保护原因，不要直接恢复被保护端口",
            "只有核对物理位置后才安排现场查线或拔线",
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
            r"CRC errors?|input errors?|transmit queue timed out|resetting adapter",
            r"bond\d*.{0,24}(slave|interface).{0,16}down|active slave changed",
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
        name="application_dependency",
        category="application",
        title="应用依赖、DNS或TLS异常",
        patterns=(
            r"Connection refused|connection timed out|dependency.{0,16}unavailable",
            r"Temporary failure in name resolution|DNS (?:lookup|resolution).{0,16}(failed|failure)|lookup .{0,80}(?:no such host|failed)",
            r"certificate has expired|TLS handshake.{0,24}(failed|error)|x509:.{0,30}expired",
        ),
        causes=("依赖服务未监听或不可用", "DNS解析路径或名称配置异常", "TLS证书过期、信任链或时间异常"),
        suggestions=(
            "核对目标域名、地址、端口和依赖服务监听状态",
            "分别检查DNS解析、网络连通和TLS证书有效期",
            "优先由系统/应用组远程处理，不读取未授权源代码",
        ),
        requires_onsite=False,
        severity="warning",
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
            r"Start request repeated too quickly|restart counter|crash loop|Main process exited",
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
                "evidence_ids": [item["id"] for item in evidence[:3]],
                "counter_evidence": "当前输入未提供足够反证，需继续验证",
                "status": "candidate",
                "basis": "规则匹配产生的调查候选，尚未通过工具或人工确认",
            }
            for cause in rule.causes
        ]
        severity = (
            event.severity
            if _SEVERITY_RANK[event.severity] >= _SEVERITY_RANK[rule.severity]
            else rule.severity
        )
        category = rule.category
        title = rule.title
        requires_onsite = rule.requires_onsite
        if rule.name == "network_redundancy" and re.search(r"MLAG|vPC", text, re.IGNORECASE):
            if not re.search(r"LACP|Port-Channel|\bbond\d*\b", text, re.IGNORECASE):
                requires_onsite = False
        suggestions = list(rule.suggestions)
    else:
        evidence_text = (event.summary or event.raw_text)[:360]
        evidence = [{"id": "E1", "source": event.source, "text": evidence_text}]
        causes = [
            {
                "title": "当前证据不足，故障类型待确认",
                "evidence_ids": ["E1"],
                "counter_evidence": "缺少可识别的日志模式或结构化告警",
                "status": "candidate",
                "basis": "知识与规则覆盖不足",
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
