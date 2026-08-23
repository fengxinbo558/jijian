# 机鉴 · IDC AI 故障调查台

一个本地私有化、零第三方运行依赖的 IDC 故障调查与现场协同原型。它接收监控告警、日志和现场上报，将原始输入、字段来源、可核对事实、知识卡、竞争假设和下一步检查整理成一条可审计调查链。

当前版本不会把关键词匹配冒充已经确认的 AI 根因，也不会把演示数据冒充真实设备数据。

## 立即启动

要求 Python 3.9 或更高版本。

```bash
cd /Users/a0000/.codex/.chatgpt-projects/g-p-6a89daf197708191b93f386ec2b56df1/idc-ai-ops
python3 run.py
```

然后打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。macOS 也可以双击 `start.command`。

事件默认保存在 `data/incidents.db`，关闭再启动不会丢失。

## 首版能力

- 通用监控告警接口：`POST /api/ingest/alert`
- 日志上传或粘贴：Linux、内核、磁盘、内存、网络和应用运行日志
- 现场异常上报：完整 SN、机架位、UID、重装发起和电源权限
- 规则优先、可选模型增强的故障分类与证据整理
- 同一事故的多条输入、多台设备关联
- 现场处置卡与操作安全门
- 高温、磁盘、内存、网络四个内置回放场景
- CC 仅在输入明确确认时显示一次电话提醒，不进入后续流程

## 数据是怎么进入系统的

当前只支持三种明确入口：

1. 用户在浏览器粘贴或上传日志；
2. 现场人员填写观察结果；
3. 外部监控平台主动调用通用 Webhook。

内置演练由程序生成模拟输入，页面会始终显示“模拟数据”。当前没有自动登录 Linux、扫描目录、读取 `journalctl`、查询 BMC、OMS 或交换机；健康接口中的 `collectors_connected` 会如实返回已连接采集器列表，当前为空。

## 一条结论怎样形成

```text
保存原始输入与来源
  → 记录 SN/机架位等字段是谁提供的
  → 从原文提取设备、端口、错误码和指标等事实
  → 记录命中的规则及其能力边界
  → 用事实检索结构化知识卡
  → 形成多个竞争假设、反证条件和缺失证据
  → 说明事件为什么合并或为什么不合并
  → 选择风险最低、信息增益较高的下一项检查
  → 以“已确认/较大可能/调查候选/证据不足”展示结论
```

完整 SN、机架位、UID 和操作许可默认视为外部报告值，不视为 AI 推断或系统已经核验。没有物理槽位映射时，系统不会猜测具体盘位或 DIMM 位置。

## 诊断知识库

`knowledge/diagnostic_cards.json` 内置 40 张带来源和版本的知识卡，覆盖存储、内存/CPU、网络、动环、Linux 系统和应用六个领域。每张卡包含：

- 可观察症状和支持信号；
- 竞争原因和会削弱判断的情况；
- 缺失上下文、下一步检查和分支条件；
- 停止条件和禁止推断；
- 官方规范或项目文档来源。

本轮使用确定性标签检索，不依赖向量数据库。知识覆盖不足时返回“知识覆盖不足”，不让模型用常识补造答案。

## 通用告警示例

```bash
curl -X POST http://127.0.0.1:8765/api/ingest/alert \
  -H 'Content-Type: application/json' \
  -d '{
    "site": "BJYZ",
    "severity": "critical",
    "sn": "SERVER-FULL-SN-001",
    "device_name": "bjyz-compute-001",
    "rack_position": "BJYZD2MC-A-01-01",
    "device_type": "server",
    "summary": "服务器网络链路中断",
    "message": "interface eth0 link down",
    "uid_status": "on",
    "power_permission": "forbidden"
  }'
```

多个告警属于同一事故时，监控侧可以提供相同的 `incident_key`，系统会将它们合并为一个多设备事件。没有明确共同标识时，不同完整 SN 默认不会被强制合并。

## 可选 AI 增强

默认不把任何数据发送到外部模型。要使用客户内网或兼容 OpenAI Chat Completions 的模型接口，显式配置：

```bash
export IDCAI_ALLOW_EXTERNAL=1
export IDCAI_MODEL_URL='http://your-private-model/v1/chat/completions'
export IDCAI_MODEL='your-model-name'
export IDCAI_API_KEY='optional-key'
python3 run.py
```

调用模型前会对疑似密钥、令牌和邮箱脱敏；模型接收的是已提取事实、证据、召回知识卡和基线候选，不负责设备身份或操作许可。模型输出必须引用已有证据，不能使用未经工具验证的 `confirmed`，不能建议危险操作；输出无效时自动使用规则与知识结果。

提示词契约位于 `prompts/contracts.json`，分为事实解析、假设生成、下一步调查和角色化沟通四种职责。默认模型关闭时，页面明确显示“规则＋知识”，不会显示为 AI 已经完成调查。

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 evals/run_evaluation.py
```

- 人可读测试题：`docs/testing/2026-08-23-first-evaluation-questions.md`
- 机器测试集：`evals/test_cases.json`
- 最近自测报告：`reports/evaluation-report.md`

测试报告明确区分规则＋知识基线和 AI 增强。没有配置真实模型时，不会伪造 AI 对照结果；模拟题通过只代表调查结构和安全边界符合预期，不代表生产故障定位准确率。

## 安全边界

- 默认本机监听 `127.0.0.1`，不向公网暴露。
- 如需在受控内网监听其他地址，必须显式设置 `IDCAI_ALLOW_LAN=1`；跨域访问另需设置 `IDCAI_ALLOW_CORS=1`。
- 不读取代码仓库，不扫描未知目录。
- 不执行关机、重启、拔盘、改配置或任何基础设施写操作。
- AI 提供的是调查结论和建议，不是现场操作授权。
- 完整 SN、机架位、业务状态或操作许可不一致时，处置卡要求停止并联系接口人。
