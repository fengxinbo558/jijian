# 第三版 SigNoz 复合底座验收摘要

> 验收日期：2026-08-24
> 运行环境：macOS 系统 Python 3.9.6；机鉴核心无第三方运行依赖；浏览器验证使用工作区 Playwright

## 自动验证结果

- Python 单元与 API 集成测试：27/27 通过。
- 独立场景题：12/12 通过。
- 场景检查点：73/73 通过。
- Python 源码编译检查：通过。
- 前端 JavaScript 语法检查：通过。
- OpenTelemetry Collector YAML 语法解析：通过。
- Git 差异格式检查：通过。
- 桌面端与 390px 手机端真实浏览器流程：通过。
- 浏览器控制台错误：0。

## 浏览器流程覆盖

- 页面入口使用“分析真实故障”和“查看模拟案例”，不再出现含糊的“事件接入”。
- 数据来源页显示人工日志、现场上报、监控告警、SigNoz、HolmesGPT、交换机/BMC 和 OMS/CMDB 的真实状态。
- 未配置 SigNoz 与 HolmesGPT 时明确显示“尚未连接”，不冒充已有自动采集或 AI 工具调查。
- 模拟案例明确显示“模拟数据不会查询真实设备”。
- 真实日志可以创建事件，完整 SN 与机架位正常保存。
- 事件“数据路径”显示收到故障、查询真实监控、规则与经验、AI 工具调查、人工确认五个阶段。
- 对真实事件执行一次外部调查时，未连接状态进入证据链，且不会阻塞规则＋知识分析。
- CC 仍只显示现有电话流程的一次提醒。
- 事件状态可以进入“处理中”。
- 桌面和手机界面均可查看证据线路。

## 连接器测试覆盖

- SigNoz 健康检查成功、未配置和连接失败状态。
- SigNoz 按设备身份和事故时间窗构造只读日志查询。
- SigNoz 按设备名查询 CPU、内存、文件系统、磁盘和网络指标。
- 响应大小、记录条数、时间窗和请求超时限制。
- SigNoz/Alertmanager 风格告警转换成统一事件。
- HolmesGPT `/healthz` 与 `/api/chat`，API Key 不进入返回数据。
- HolmesGPT 回答、工具调用和只读边界写入调查链。
- 外部日志能补充事实和候选，但不会直接把根因升级为“已确认”。
- 外部服务不可用时，原有人工入口和规则＋知识分析继续工作。

## 当前环境限制

- 当前开发电脑没有 Docker，因此没有在本机实际启动 SigNoz Community 或 HolmesGPT。
- 连接器已使用模拟 HTTP 服务验证成功、失败和鉴权路径，但真实 SigNoz 查询字段仍需在目标版本上做一次联调。
- OpenTelemetry Linux 配置已通过 YAML 解析，没有在真实客户服务器安装；是否能读取 journald 取决于客户授权和系统权限。
- SNMP、Redfish、OMS/CMDB 仍是明确标注的后续连接器，当前没有真实客户接口。
- 12/12 模拟题通过只证明调查结构与安全边界，不代表生产根因定位准确率。

## 产物

- 复合设计：`docs/superpowers/specs/2026-08-24-signoz-composite-platform-design.md`
- 实施计划：`docs/superpowers/plans/2026-08-24-signoz-composite-platform-implementation.md`
- 连接说明：`docs/integrations/signoz-holmes-setup.md`
- Linux 采集示例：`deploy/otel/linux-agent.yaml`
- 第三方边界：`THIRD_PARTY.md`
- 数据来源截图：`reports/browser-sources.png`
- 桌面截图：`reports/browser-desktop.png`
- 手机截图：`reports/browser-mobile.png`
- 场景报告：`reports/evaluation-report.md`
