# AI 主动调查与多平台模拟接入实验室实施计划

设计依据：`docs/superpowers/specs/2026-08-24-ai-first-multiplatform-simulation-lab-design.md`

实施目标：交付设计中的第一阶段可运行闭环。所有模拟平台必须走未来真实平台可复用的服务端接口；测试模型桩不得显示为真实 AI；设备身份与现场许可不得由模型推断。

## 交付切片

1. 平台、事件、拓扑、Agent 与备份数据基础
2. 六类模拟平台和统一接入接口
3. 确定性身份、拓扑与跨平台事故关联
4. 只读工具注册表和 Agent 调查回放
5. 平台接入实验室与 AI 审计界面
6. 分角色工作台和最高管理员权限
7. 知识发布可信检查与 SQLite 在线备份
8. 连锁场景、全量回归和浏览器验收

## 切片一：数据基础

### 任务 1：扩展 SQLite 表结构

修改：

- `idcops/store.py`
- `tests/test_lab_store.py`

新增表：

- `integration_platforms`
- `integration_events`
- `topology_entities`
- `topology_links`
- `agent_runs`
- `agent_steps`
- `raw_access_audit`
- `backup_runs`

步骤：

1. 先写旧数据库升级和幂等初始化测试。
2. 为来源事件 ID、Agent 轮次、拓扑实体和备份路径增加唯一约束或索引。
3. 原始事件、工具返回和 Agent 步骤均采用追加式记录。
4. 运行 `python3 -m unittest tests.test_lab_store -v`。

验收：重复启动不产生重复平台或拓扑记录；旧事件与既有资产表内容不变。

### 任务 2：实现模拟平台与拓扑仓库

新增/修改：

- 新增 `idcops/lab.py`
- 修改 `idcops/service.py`
- 新增 `tests/test_lab.py`

步骤：

1. 幂等初始化动环、网络、BMC、Linux/应用、OMS/CMDB、现场六个平台。
2. 实现连接状态、延迟、权限不足和结构错误配置。
3. 实现平台事件保存、重复检测、投递结果回写和最近事件查询。
4. 实现拓扑实体、连接关系和邻接查询。
5. 运行 `python3 -m unittest tests.test_lab -v`。

验收：六个平台可以独立启停；事件原始快照不被标准化结果覆盖。

## 切片二：统一接入接口

### 任务 3：定义并校验统一平台事件契约

新增/修改：

- 新增 `idcops/platform_contracts.py`
- 修改 `idcops/lab.py`
- 新增 `tests/test_platform_contracts.py`

步骤：

1. 校验来源系统、来源事件 ID、发生时间、机房、实体、信号类型、严重级别和原始载荷。
2. 标准化字段保留字段来源，不允许模型参与身份填充。
3. 将六类平台事件转换为现有 `monitor`、`log` 或 `onsite` 输入。
4. 对缺少身份的事件允许入库，但写入明确的数据缺口。
5. 运行契约单元测试。

验收：同一契约可被模拟平台和未来真实连接器复用；错误载荷返回可读错误。

### 任务 4：增加平台实验室 API

修改：

- `idcops/server.py`
- `tests/test_lab_api.py`

接口：

- `GET /api/lab/platforms`
- `POST /api/lab/platforms/{key}/state`
- `GET /api/lab/events`
- `POST /api/lab/events`
- `GET /api/lab/topology`
- `POST /api/lab/topology/seed`
- `GET /api/lab/scenarios`
- `POST /api/lab/scenarios/{id}/run`

步骤：

1. 所有事件发送真实调用统一接入服务，返回 HTTP 状态、来源事件 ID、事故 ID 和关联依据。
2. 重复事件幂等返回原投递结果。
3. 平台断开返回不可用，不创建伪事件。
4. 延迟上限限制在测试可接受范围内。
5. 运行 API 测试。

验收：前端不需要直接调用内部事故服务；部分平台断开不影响其他平台。

## 切片三：确定性关联

### 任务 5：实现身份与拓扑关联等级

新增/修改：

- 新增 `idcops/correlation.py`
- 修改 `idcops/lab.py`
- 修改 `idcops/service.py`
- 新增 `tests/test_cross_platform_correlation.py`

步骤：

1. 相同 `incident_key` 作为显式关联。
2. 完整 SN 或不可变资产 ID 完全一致时关联同一设备。
3. 通过已保存拓扑关系和时间窗口推导共同事故键，并保存关系路径。
4. 只有文本语义相似时只保存待确认建议，不自动合并。
5. 身份冲突时停止自动绑定。
6. 运行跨分类关联测试，确保网络、系统和应用事件可以进入一个事故。

验收：每次合并都有机器可读和人可读依据；AI 不能覆盖关联结果。

### 任务 6：增加可复用连锁场景

新增：

- `idcops/lab_scenarios.py`
- `tests/test_lab_scenarios.py`

首批场景：

1. 交换机模块/端口异常 → 服务器断连 → 系统重试 → 应用超时。
2. 制冷异常 → 动环升温 → 多台 BMC 温度告警 → 系统降频。
3. 单/双路供电异常 → BMC 电源事件 → 系统失联。
4. 身份冲突与平台缺失，验证不得强行合并。

验收：场景按统一接口逐条发送；隐藏真值不进入 Agent 输入。

## 切片四：AI Agent 与可审计工具调用

### 任务 7：实现只读工具注册表

新增：

- `idcops/agent_tools.py`
- `tests/test_agent_tools.py`

工具：

- `network.query_port`
- `network.query_peer`
- `bmc.query_health`
- `facility.query_environment`
- `linux.query_logs`
- `oms.query_asset`
- `onsite.query_observation`

步骤：

1. 每个工具声明允许参数、来源平台和只读级别。
2. 平台断开、延迟、无权限和无数据均返回结构化状态。
3. 工具只读取模拟平台已保存的数据，不生成不存在的结果。
4. 原始返回进入证据账本。

验收：任意未知工具、额外参数或写操作被拒绝并审计。

### 任务 8：实现 Agent 调查编排器

新增/修改：

- 新增 `idcops/agent.py`
- 修改 `idcops/security.py`
- 修改 `idcops/service.py`
- 新增 `tests/test_agent.py`

步骤：

1. 实现固定规则基线、测试模型桩和真实模型三种明确区分的运行模式。
2. 真实模型复用受控的兼容模型接口；未连通时返回 `model_not_configured`。
3. 每轮要求结构化事实、竞争原因、证据引用、下一工具和停止原因。
4. 校验模型只能引用已有证据和允许工具。
5. 支持最大轮次、超时、工具预算、无效输出次数和安全降级。
6. 测试模型桩只用于协议与场景测试，所有返回带 `test_stub_not_real_ai`。

验收：模型未配置时不显示 AI 已运行；危险动作、无证据确认和身份猜测全部被拦截。

### 任务 9：保存并查询完整 Agent 回放

新增/修改：

- 新增 `idcops/agent_trace.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_agent_trace.py`

接口：

- `POST /api/agent/runs`
- `GET /api/agent/runs?incident_id=...`
- `GET /api/agent/runs/{id}`

验收：每轮输入、工具、返回、候选变化、校验、人工修正和停止原因可回放；模型私有思维不作虚假承诺。

## 切片五：接入实验室和 AI 审计界面

### 任务 10：增加平台接入实验室

修改：

- `web/index.html`
- `web/app.js`
- `web/styles.css`

功能：

1. 主导航增加“接入实验室”。
2. 六张平台卡显示连接状态、事件量和最近错误。
3. 支持字段表单、原始 JSON、连接状态、错误和延迟模拟。
4. 支持场景编排和单条事件发送。
5. 显示真实接口路径、状态码、事故 ID、关联等级和原始响应。
6. 显示拓扑实体、关系和数据来源。

验收：用户能看到“数据从模拟平台进入统一接口，再影响事故”的全过程。

### 任务 11：增加 AI 调查回放与对照页面

修改：

- `web/index.html`
- `web/app.js`
- `web/styles.css`

功能：

1. 简洁、完整和原始审计三种查看层级。
2. 跨平台时间线、Agent 轮次、工具输入输出和候选变化并列展示。
3. 固定基线、测试模型桩、真实模型三种状态明显区分。
4. 未配置模型时显示不能运行，不用测试桩冒充。
5. 每个最终结论可以跳回证据和原始来源。

验收：管理员能解释系统实际做了什么；普通用户无需阅读全部技术字段。

## 切片六：角色与最高管理员

### 任务 12：实现五种角色和服务端视图投影

修改：

- `idcops/auth.py`
- `idcops/service.py`
- `idcops/server.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- 新增 `tests/test_role_views.py`

步骤：

1. 增加 `super_admin`。
2. 为现场、组长、接口人、AI 管理员和最高管理员定义字段与动作能力。
3. 事件详情按服务端角色返回必要字段，不能只靠 CSS 隐藏。
4. 角色切换器标记为演示功能；正式模式关闭自由切换。
5. 角色切换后重载事件和工作台。

验收：不同角色页面和 API 返回均不同；越权请求返回 403。

### 任务 13：实现最高管理员原始数据临时查看

修改：

- `idcops/admin.py`
- `idcops/server.py`
- `web/app.js`
- 新增 `tests/test_raw_access.py`

流程：填写原因 → 二次确认 → 短时查看 → 审计。首版可以使用单次响应，不持久化可复用明文令牌。

验收：AI 管理员不能查看；最高管理员未填写原因或未确认时被拒绝；每次查看均有记录。

## 切片七：知识可信与备份

### 任务 14：增强知识发布检查

修改：

- `idcops/releases.py`
- `idcops/assets.py`
- `web/app.js`
- 新增 `tests/test_trusted_releases.py`

步骤：

1. 将“结构检查通过”和“专业审核通过”分开显示。
2. 知识草稿要求来源、适用条件、反证、审核人和正反例。
3. 发布测试运行历史回归与反例，危险建议直接失败。
4. 旧版本继续保留，条件不同的经验作为并列分支。

验收：只有标题和领域的错误知识不能显示为“知识正确”。

### 任务 15：实现 SQLite 在线备份与恢复演练

新增/修改：

- 新增 `idcops/backups.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_backups.py`

步骤：

1. 使用 SQLite backup API 创建一致性快照。
2. 支持手工备份、发布前备份和保留策略。
3. 恢复演练只恢复到临时数据库并验证关键表数量与哈希。
4. 真正覆盖当前数据库不在本阶段开放。

验收：备份可以独立打开；恢复验证不会修改正在运行的数据库。

## 切片八：验证与交付

### 任务 16：全量自动测试

运行：

```bash
python3 -m unittest discover -s tests -v
python3 evals/run_evaluation.py
python3 evals/run_synthetic_logs.py
node --check web/app.js
node --check web/device-scan.js
git diff --check
```

要求：现有测试无回退；新增平台、Agent、角色和备份测试全部通过。

### 任务 17：浏览器端到端验收

场景：

1. 打开接入实验室，确认六个平台和独立状态。
2. 断开 BMC，运行网络连锁场景，确认其余来源继续进入且显示证据缺口。
3. 恢复 BMC，运行完整场景，确认跨平台事件进入同一事故。
4. 运行测试模型桩，确认明确标记非真实 AI；模型未配置时真实 Agent 不可运行。
5. 查看 Agent 每一轮工具、原始返回和候选变化。
6. 切换五种演示角色，确认页面与服务端字段不同。
7. 最高管理员二次确认后查看原始数据，确认审计记录产生。
8. 创建备份并完成临时恢复验证。

### 任务 18：更新说明和真实性声明

修改：

- `README.md`
- `reports/verification-summary.md`

明确说明：

- 哪些平台是模拟、哪些是真实连接；
- 测试模型桩不是 AI；
- 真实模型是否配置；
- 自动测试不能证明生产准确率；
- 正式角色认证和生产高可用仍需客户环境。
