# 机鉴 · IDC AI 故障调查台

一个本地私有化、零第三方运行依赖的 IDC 故障调查与现场协同 MVP。它接收监控告警、日志和现场上报，将多条证据整理成统一事件，并给出有证据的故障候选与现场处置卡。

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

调用模型前会对疑似密钥、令牌和邮箱脱敏；模型不可用或输出无效时自动使用规则结果。

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 evals/run_evaluation.py
```

- 人可读测试题：`docs/testing/2026-08-23-first-evaluation-questions.md`
- 机器测试集：`evals/test_cases.json`
- 最近自测报告：`reports/evaluation-report.md`

## 安全边界

- 默认本机监听 `127.0.0.1`，不向公网暴露。
- 如需在受控内网监听其他地址，必须显式设置 `IDCAI_ALLOW_LAN=1`；跨域访问另需设置 `IDCAI_ALLOW_CORS=1`。
- 不读取代码仓库，不扫描未知目录。
- 不执行关机、重启、拔盘、改配置或任何基础设施写操作。
- AI 提供的是调查结论和建议，不是现场操作授权。
- 完整 SN、机架位、业务状态或操作许可不一致时，处置卡要求停止并联系接口人。
