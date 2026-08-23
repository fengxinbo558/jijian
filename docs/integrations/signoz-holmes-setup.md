# SigNoz 与 HolmesGPT 连接说明

这份说明对应“数据来源”页面。它不会自动登录客户服务器，也不会替用户取得任何权限。只有客户或测试环境明确允许的 Linux 主机、SigNoz 和 HolmesGPT 才能连接。

## 1. 组件分工

| 组件 | 做什么 | 不做什么 |
|---|---|---|
| Linux OpenTelemetry Collector | 读取本机 journald 和主机性能指标并发送到 SigNoz | 不分析根因，不操作机器 |
| SigNoz Community | 保存、查询日志和指标，产生监控告警 | 不决定现场操作权限 |
| 机鉴 | 按设备和时间窗取证，建立证据链、现场安全门和处理闭环 | 不自动重启、关机或改配置 |
| HolmesGPT | 使用获准的只读工具补充调查并返回工具调用记录 | 不决定 SN、机架位、断电许可或资产变化 |

## 2. 先部署 SigNoz Community

SigNoz 是独立服务，建议按照官方 Self-Host 文档在受控 Linux 或测试服务器上部署。小型测试环境至少需要约 4 GB 内存；当前开发电脑没有 Docker，因此本仓库不会伪装成已经运行了 SigNoz。

部署后需要确认：

- Web 和 API 地址可以从机鉴所在主机访问；
- OTLP gRPC 或 HTTP 端口只在受控网络开放；
- 为机鉴创建只读 Service Account/API Key；
- 不给机鉴修改告警、删除数据或调整保留期的权限。

## 3. 在获准的 Linux 主机安装采集器

示例配置位于 `deploy/otel/linux-agent.yaml`，采集两类数据：

- journald 系统日志；
- CPU、内存、磁盘、文件系统、负载、网络和分页指标。

启动采集器前设置：

```bash
export IDC_SITE_CODE='BJYZ'
export IDC_DEVICE_SN='完整服务器SN'
export IDC_RACK_POSITION='完整机架位'
export SIGNOZ_OTLP_ENDPOINT='signoz-otel-collector.example.internal:4317'
export SIGNOZ_OTLP_INSECURE='false'
```

配置把完整 SN 和机架位作为资源属性写入遥测数据，便于机鉴按事故对象查询。若客户不允许在遥测中写入这些字段，应改用客户 CMDB 中稳定的设备 ID，并在机鉴侧做只读映射。

采集器需要读取 journald 的权限；权限范围应只覆盖获准日志。业务日志可能包含商业机密、令牌和个人信息，不能因为“是日志”就默认全部采集。

## 4. 让机鉴连接 SigNoz

在启动机鉴前设置：

```bash
export IDCAI_SIGNOZ_URL='https://signoz.example.internal'
export IDCAI_SIGNOZ_API_KEY='只读服务账号密钥'
export IDCAI_QUERY_WINDOW_MINUTES='20'
export IDCAI_QUERY_MAX_RECORDS='40'
```

机鉴只调用：

- `GET /api/v1/health` 检查服务是否可达；
- `POST /api/v5/query_range` 按设备身份和事故时间窗查询日志。

页面显示“已连接”只表示健康检查成功；每个事件仍会记录本次到底查到多少数据。

## 5. 连接 HolmesGPT

HolmesGPT 应部署在受控内网，启用 API Key，并只配置获准的只读 Toolsets。启动机鉴前设置：

```bash
export IDCAI_HOLMES_URL='https://holmes.example.internal'
export IDCAI_HOLMES_API_KEY='Holmes服务密钥'
export IDCAI_HOLMES_MODEL='内网模型配置名'
```

机鉴调用：

- `GET /healthz` 检查服务；
- `POST /api/chat` 发送事故事实、时间窗和 SigNoz 查询摘要。

HolmesGPT 返回的回答与工具调用保存在事件调查链中。没有工具证据的回答不能成为“已确认根因”，也不能覆盖现场人员和接口人确认的操作权限。

## 6. SigNoz 告警送入机鉴

SigNoz 或兼容 Alertmanager 的告警 Webhook 可以发送到：

```text
POST /api/ingest/signoz-alert
```

机鉴读取常见的 `alerts[].labels`、`alerts[].annotations`、`startsAt` 和 `fingerprint`，转换成统一事件。完整 SN、机架位和设备类型最好作为告警标签直接传入；如果没有，机鉴会明确标为缺失，不从相似设备猜测。

## 7. 网络日志到底怎么来

交换机日志不是机鉴凭空产生的。真实环境通常有两条入口，可以同时使用：

1. 交换机把 syslog 主动发送给现有 syslog 中心、Grafana Alloy、Vector、Fluent Bit 或 OpenTelemetry Collector，再进入 SigNoz/Loki；
2. Zabbix、LibreNMS 或客户现有 NMS 通过 SNMP、Telemetry 和 ICMP 发现端口、路由邻居、模块、电源等异常，再把告警通过 `POST /api/ingest/alert` 送入机鉴。

机鉴接收的最小字段建议包含：事件时间、机房编码、设备名或管理 IP、完整设备 SN（若 NMS 有）、机架位、端口、厂商原始消息和 NMS 告警 ID。同一个 NMS 告警 ID 可作为 `incident_key`，让 syslog、指标和现场反馈进入同一事件，而不是仅靠关键词强行合并。

当前版本已经能分析 link down/flap、光功率、CRC、LACP/Bond、BGP、OSPF、STP/BPDU Guard、MAC flap、MLAG、交换机电源和核心交换机大范围中断的合成日志；尚未连接客户网络设备，页面会如实显示“等待客户接口”。

可选的中心 syslog 模板位于 `deploy/otel/network-syslog.yaml`。它默认监听 TCP 54527，只有在管理网络 ACL、发送源白名单和保留策略确认后才能部署；模板存在不等于当前电脑已经收到交换机日志。

## 8. BMC、动环与资产系统

本轮没有假装已连接真实设备。后续按同一边界扩展：

- 交换机：优先接现有 NMS/LibreNMS/Zabbix 告警和指标；没有现有平台时再考虑 SNMP Exporter。
- BMC：通过厂商管理平台或 Redfish 的只读账号读取 SEL、温度、风扇、电源和硬件状态。
- 动环/DCIM：通过已有平台告警 Webhook 接收温度、供电、漏水和烟雾事件；CC 判断还必须结合机房等级与实际设备影响。
- OMS/CMDB：只读查询完整 SN、机架位、业务状态和资产关系；上线/下线备件仍由既有资产流程确认。

这些连接器都必须先经过权限、字段和脱敏审核，不能用一个“万能 root 账号”解决。
