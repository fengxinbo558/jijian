# 第四版网络、系统日志与机房事件矩阵验收摘要

> 验收日期：2026-08-24
> 运行环境：macOS 系统 Python；机鉴核心无第三方运行依赖；浏览器验证使用隔离数据库与 Playwright

## 自动验证结果

- Python 单元与 API 集成测试：36/36 通过。
- 原有独立场景题：12/12 通过，73/73 个检查点通过。
- 新增合成日志：36/36 场景通过，259/259 个检查点通过。
- 前端 JavaScript 语法、Python 编译和 JSON/YAML 解析：通过。
- Git 差异格式检查：通过。
- 桌面端与 390px 手机端真实浏览器流程：通过。
- 浏览器控制台错误：0。

## 新增覆盖

- Linux：OOM、磁盘 I/O、只读文件系统、inode、NVMe、ECC/MCE、PCIe AER、watchdog、systemd 重启循环和时钟同步。
- 应用：端口冲突、依赖连接拒绝、DNS、TLS 证书和数据库死锁。
- 网络：link down/flap、光功率、CRC、LACP/Bond、BGP、OSPF、BPDU Guard、MAC flap、MLAG、交换机电源和核心交换机共同事故。
- 动环：核心/普通机房单路与双路掉电、漏水、核心设备受影响、温升和烟雾告警。
- 机房档案：本地标注核心、普通和未确认；输入未知时使用档案；输入与档案冲突时转人工确认。
- 三态 CC：`required`、`needs_confirmation`、`not_required`，并展示规则编号、证据、影响与缺失信息。

## 浏览器流程覆盖

- 数据来源页明确区分 Linux Collector、网络 syslog/NMS、BMC、动环、资产系统、SigNoz 和 HolmesGPT 的当前状态。
- 机房等级页面可以保存本地标注，且不根据名称猜测等级。
- 核心机房单路掉电显示“需要CC”和 `CC-CORE-SINGLE-FEED`。
- 普通机房单路掉电且设备在线显示“普通处理”，不误触发 CC。
- 每个事件显示数据路径、原始输入、字段来源、事实、规则、知识卡、竞争候选和下一步检查。
- 模拟案例明确标记为模拟；真实日志入口保留完整 SN、机架位和现场安全门。
- 桌面与手机端均能查看设施判断卡和调查轨迹。

## 当前环境限制

- 36 个日志场景全部是合成数据；通过只证明规则、知识召回、安全边界和页面链路符合预期，不能证明真实生产准确率。
- 当前电脑没有运行真实 SigNoz、HolmesGPT、NMS、BMC 或动环平台；页面会显示未配置或等待客户接口。
- OpenTelemetry Linux 与网络 syslog 配置已通过 YAML 解析，但本机没有 `otelcol-contrib` 可执行文件，因此未做 Collector 启动级校验。
- 不同交换机厂商的 syslog 文案、OID、Trap、Redfish 字段以及客户告警模板仍需真实样本适配。
- 当前不自动登录设备、不自动修复、不自动拨打 CC，也不读取客户源代码仓库。

## 产物

- 调研与竞品简报：`docs/research/2026-08-24-log-collection-competitive-brief.md`
- 机房重大事件设计：`docs/superpowers/specs/2026-08-24-facility-major-event-cc-design.md`
- 连接说明：`docs/integrations/signoz-holmes-setup.md`
- Linux 采集模板：`deploy/otel/linux-agent.yaml`
- 网络 syslog 模板：`deploy/otel/network-syslog.yaml`
- 合成日志数据集：`evals/synthetic_log_cases.json`
- 合成日志逐项结果：`reports/synthetic-log-report.md`
- 设施判断页面截图：`reports/browser-facility-assessment.png`
- 数据来源、桌面和手机截图：`reports/browser-sources.png`、`reports/browser-desktop.png`、`reports/browser-mobile.png`
