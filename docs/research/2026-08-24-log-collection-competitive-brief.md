# 日志采集与故障分析竞品简报

> 调研日期：2026-08-24  
> 决策目标：确定机鉴应如何取得网络、Linux、应用、BMC 与动环日志，并据此建立模拟测试集。  
> GitHub 星数为调研时通过 GitHub 官方 API 取得的快照，会随时间变化。

## 1. 结论先行

机鉴不应自己重做一套通用日志采集器。推荐组合保持不变：

- **SigNoz + OpenTelemetry Collector**：主要的日志、指标和时间窗查询底座；
- **网络设备 syslog + 现有 NMS/Zabbix/LibreNMS 告警**：交换机和链路事件入口；
- **Redfish/IPMI 或厂商管理平台**：BMC、SEL、服务器电源、温度、风扇和硬件事件入口；
- **机鉴**：统一设备身份、机架位、机房等级、共同事故、CC 判断、现场安全门、经验分支和 AI 调查审计；
- **HolmesGPT**：在已授权的只读数据源上补充工具调查，不替代固定规则和人工确认。

产品差异化不在“谁能收更多文本”，而在于把服务器日志、网络事件、动环事件和现场反馈放进同一条可追溯调查链，并与 IDC 现场操作约束结合。

## 2. 主流开源产品如何取得日志

| 产品 | GitHub 快照 | 日志从哪里来 | 强项 | 对机鉴的限制/启示 |
|---|---:|---|---|---|
| [SigNoz](https://github.com/SigNoz/signoz) | 31,921 | OpenTelemetry Collector；journald、syslog、文件、OTLP、应用 SDK | 日志、指标、Trace 放在同一时间线上；适合作为主查询底座 | 不直接解决 IDC 资产、核心机房、CC 和现场操作许可 |
| [Grafana Loki](https://github.com/grafana/loki) | 28,775 | Grafana Alloy 读取 journald、文件、Docker/Kubernetes、syslog、HTTP、消息队列 | 标签化日志存储成熟，生态广 | 需要额外的指标、告警、AI 和 IDC 业务层；AGPL 边界需单独评估 |
| [Vector](https://github.com/vectordotdev/vector) | 22,438 | journald、文件、syslog、socket、云和容器来源 | 采集、转换、路由灵活；journald 支持检查点 | 是数据管道，不是故障调查与工单产品 |
| [Fluent Bit](https://github.com/fluent/fluent-bit) | 8,059 | systemd、tail 文件、容器日志以及大量输入插件 | 轻量、部署广、适合节点侧 | 主要负责采集与转发，需要后端和分析层 |
| [Zabbix](https://github.com/zabbix/zabbix) | 6,302 | Agent 日志监控、SNMP 轮询/Trap、IPMI、HTTP/JMX 等 | 网络、UPS、服务器和传统基础设施监控完整 | 日志推理和 IDC 现场处置不是其主要差异点 |
| [OpenTelemetry Collector Contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib) | 4,874 | journald、filelog、syslog、hostmetrics 及多种接收器 | 标准化、厂商中立，适合放在采集层 | 只负责接收、处理、导出，不负责事故判断 |
| [LibreNMS](https://github.com/librenms/librenms) | 4,845 | SNMP 轮询/Trap、syslog 和设备发现 | 网络设备、端口、光模块与告警 | Linux 应用日志和现场工单链较弱 |
| [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) | 3,122 | 通过工具访问日志、指标、Kubernetes 与监控平台 | 能把多个只读查询组织成调查过程 | 数据源不完整时不能凭空判断；没有机鉴的安全门和 CC 规则 |

## 3. 不同数据的实际入口

### 3.1 Linux 系统

主要来源：

- systemd journal：内核、systemd 服务、驱动、OOM、挂载、启动和部分应用标准输出；
- `/var/log/*` 或业务自定义文件：传统 syslog、审计、应用和数据库日志；
- `/proc`、`/sys`、node/host metrics：CPU、内存、磁盘、网卡和文件系统指标；
- BMC/SEL：Linux 无法启动或主机失联时补充带外硬件证据。

推荐入口：OpenTelemetry Collector 的 journald、filelog、hostmetrics；保留 Fluent Bit/Vector 作为客户已有采集器的兼容输入。

### 3.2 应用和服务

主要来源：

- systemd 托管服务的 journal；
- 应用日志文件；
- Docker/Kubernetes 标准输出；
- OpenTelemetry Logs/Traces；
- 数据库、中间件和反向代理日志。

应用日志可能包含商业信息。采集范围、脱敏、保留期限和访问权限必须由客户授权；默认不读取源代码仓库。

### 3.3 交换机与路由设备

主要来源：

- syslog：端口 up/down、协议邻居变化、环路保护、堆叠/MLAG、模块、电源和温度事件；
- SNMP 轮询：端口状态、计数器、光功率、CPU、内存、风扇和电源；
- SNMP Trap：设备主动上报状态变化；
- 流式遥测或厂商 API：更高频指标和结构化状态；
- 已有 NMS：将已聚合的告警和影响范围通过 Webhook/API 送入机鉴。

第一版不直接轮询生产交换机，优先接客户现有 NMS 告警或把网络 syslog 送入 Collector。

### 3.4 BMC 与服务器硬件

主要来源：

- Redfish EventService、LogService/SEL；
- 厂商带外管理平台；
- IPMI SEL；
- Linux EDAC/MCE、NVMe/SMART 等带内日志。

同一个硬件问题可能同时出现 BMC、内核和监控告警，必须保留来源并合并证据，不能简单去重后丢掉时间线。

### 3.5 动环和基础设施

主要来源：

- DCIM/动环平台告警；
- UPS、PDU、ATS、断路器和制冷设备的 SNMP/Modbus/厂商接口；
- 温湿度、漏水、烟雾、振动等传感器；
- 现场人员反向上报。

这些来源先归一化成事件，CC 是否触发再由机房等级、影响范围和已确认规则决定。

## 4. 功能能力对比

评级：强、可用、较弱、无。

| 买方能力 | SigNoz | Loki/Alloy | Zabbix/LibreNMS | HolmesGPT | 机鉴目标 |
|---|---|---|---|---|---|
| Linux 日志与指标 | 强 | 强（配合 Prometheus） | 可用 | 依赖外部 | 强（复用 SigNoz） |
| 网络 SNMP/Trap | 较弱 | 较弱 | 强 | 依赖工具 | 可用（接现有 NMS） |
| 日志、指标、Trace 关联 | 强 | 可用 | 较弱 | 可用 | 强 |
| AI 工具调查 | 较弱/发展中 | 无 | 无 | 强 | 强且可审计 |
| 完整 SN、机架位与资产历史 | 无 | 无 | 可用 | 无 | 强 |
| 核心/普通机房与 CC 规则 | 无 | 无 | 需定制 | 无 | 强 |
| 现场断电与操作安全门 | 无 | 无 | 需定制 | 无 | 强 |
| 经验分支保留与复盘 | 较弱 | 无 | 可用 | 可生成 | 强 |

## 5. 首批测试场景范围

测试集按真实入口分层，而不是只按关键词分层：

1. **Linux 内核与 systemd**：OOM、I/O、只读文件系统、inode、ECC/MCE、PCIe AER、Kernel panic、服务重启循环、时钟异常。
2. **应用与依赖**：端口占用、连接超时/拒绝、DNS、TLS 证书、数据库死锁。
3. **服务器网络栈**：网卡 link down、驱动重置、Bond 主备切换。
4. **交换机网络**：端口 flap、光功率、CRC、LACP、BGP、OSPF、STP、MAC 漂移、MLAG、交换机电源和重启。
5. **动环与共同事故**：核心/普通机房掉电、漏水、温升、核心交换机及下联批量中断。

每条模拟日志必须包含：来源、原始日志、设备身份、预期事实、预期类别、CC 结果、禁止推断和下一步检查。

## 6. 对产品路线的影响

### 立即实现

- 在现有 OpenTelemetry 配置中增加可选 syslog 接收入口；
- 扩展网络控制面、二层环路、冗余链路、时钟、DNS、TLS、漏水和供电事实；
- 用固定的合成日志数据集做回归测试；
- 把机房等级和 CC 判断结果展示为独立、可解释的卡片。

### 后续适配

- Zabbix/LibreNMS 告警适配器；
- Redfish 只读事件订阅；
- CMDB 机房等级与核心设备角色；
- 客户自有 syslog 字典和设备厂商消息映射。

### 暂不实现

- 直接对生产交换机进行配置写入；
- 自动拨打 CC；
- 未经授权采集客户源代码或全部业务日志；
- 仅凭 AI 文本判断就确认故障根因。

## 7. 主要资料

- SigNoz systemd 日志采集：<https://signoz.io/docs/logs-management/send-logs/collect-systemd-logs/>
- SigNoz syslog 采集：<https://signoz.io/docs/userguide/collecting_syslogs/>
- Grafana Alloy/Loki 日志来源：<https://grafana.com/docs/loki/latest/send-data/alloy/>
- Fluent Bit systemd 与文件采集：<https://docs.fluentbit.io/manual/3.1/pipeline/inputs/systemd>、<https://docs.fluentbit.io/manual/pipeline/inputs/tail>
- Vector journald 与 syslog：<https://vector.dev/docs/reference/configuration/sources/journald/>、<https://vector.dev/docs/reference/configuration/sources/syslog/>
- Zabbix SNMP 与多类采集能力：<https://www.zabbix.com/documentation/current/en/manual/config/items/itemtypes/snmp>、<https://www.zabbix.com/documentation/current/en/manual/introduction/features>
- Linux RAS/EDAC：<https://cdn.kernel.org/doc/html/latest/admin-guide/RAS/main.html>
- DMTF Redfish：<https://redfish.dmtf.org/>

