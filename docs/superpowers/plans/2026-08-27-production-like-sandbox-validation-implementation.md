# 生产近似沙盒盲测实施计划

日期：2026-08-27  
设计依据：`docs/superpowers/specs/2026-08-27-production-like-sandbox-validation-design.md`

## 实施原则

- 复用现有正式接入、规则分析、RAG、事故关联、AI Agent 和审计链，不另写一套“看起来会分析”的假逻辑。
- 每次运行创建独立 SQLite 数据库；生产数据库只保存沙盒运行索引和治理审计，不接收沙盒事故与日志。
- 题面、隐藏答案和评分规则分库存放；运行器没有读取隐藏答案的连接。
- 规则基线与真实 AI 分轨报告；未配置真实模型时 AI 轨道必须显示 `not_run`。
- 首版固定生成 120 道题，允许用种子复现；公开数据不可用时报告标记不完整，不用合成数据冒充。
- 界面采用紧凑验收台，避免 120 道题形成超长页面；所有状态和失败都说明下一步。
- 每个后端切片先写测试，再实现；提交前运行完整自动测试和真实浏览器验证。

## 切片一：独立存储、题包和清单

### 任务 1：实现沙盒控制库和每次运行库

文件：

- 新增 `idcops/sandbox_validation.py`
- 新增 `tests/test_sandbox_validation.py`

步骤：

1. 创建 `data/sandbox/control.db`、`data/sandbox/secrets/<suite_version>.db` 和 `data/sandbox/runs/<run_id>/sandbox.db`。
2. 控制库保存测试集、题面索引、运行、报告、数据清单和审计；隐藏答案库只保存 secret/rubric。
3. 每次运行库使用 `IncidentStore` 初始化正式数据结构，并增加本次题目、轨道结果和评分表。
4. 强制写入 `simulation=true`、`environment=sandbox`、`sandbox_run_id` 和 `SANDBOX-*` 机房编码。
5. 保存生产数据库创建前后指纹与关键表计数，证明沙盒没有污染生产记录。

验收：独立路径存在；运行库与生产库不是同一文件；缺少沙盒标识的输入被拒绝。

### 任务 2：构建 120 道版本化测试题和数据清单

文件：

- 修改 `idcops/sandbox_validation.py`
- 新增 `data/sandbox/catalog/suite-v1.json`
- 扩展 `tests/test_sandbox_validation.py`

步骤：

1. 按 30/35/25/15/10/5 生成公开日志、单点、连锁、冲突缺失、正常误报和安全责任题。
2. 题面只保存正式链路可观察信号；答案只进入 secrets 数据库。
3. 使用随机种子变化完整 SN、机架位、设备名、端口、时间、顺序、噪声和缺失平台。
4. 建立公开数据 manifest：来源、许可证、哈希、文件大小、真实性等级和限制。
5. 优先使用项目已有且哈希可验证的公开轻量缓存；不可用时如实标记。

验收：总数恰好 120；分类数量准确；同种子题面一致；题面不含答案字段。

## 切片二：正式链路双轨运行和隐藏评分

### 任务 3：运行规则与知识基线

文件：

- 修改 `idcops/sandbox_validation.py`
- 扩展 `tests/test_sandbox_validation.py`

步骤：

1. 为每次沙盒运行实例化指向独立运行库的 `IncidentService`。
2. 将题目信号通过现有 `ingest` 或 `ingest_governed_platform_event` 送入正式接入边界。
3. 记录解析结果、事故关联、证据、候选原因、门禁、现场下一步和审计轨迹。
4. 正常题验证不产生错误确认；冲突题验证停止或转人工；安全题验证高风险动作不被自动批准。
5. 每题异常隔离，基础设施错误不伪装成模型错误。

验收：运行库包含完整正式链路记录；生产库事件数不变；每题有可复现终态。

### 任务 4：运行真实 AI 轨道并记录能力边界

文件：

- 修改 `idcops/sandbox_validation.py`
- 扩展 `tests/test_sandbox_validation.py`

步骤：

1. 健康检查真实模型适配器；不可用时整条 AI 轨道标记 `not_run`。
2. 可用时调用现有 Agent，只开放获准的只读调查工具。
3. 保存模型、参数、提示词、知识、约束、工具版本和每轮调查证据。
4. 不允许测试桩或规则结果写入 AI 分数字段。

验收：无模型环境明确显示未运行；有模型环境能与基线独立评分。

### 任务 5：实现独立评分器和发布门禁

文件：

- 修改 `idcops/sandbox_validation.py`
- 扩展 `tests/test_sandbox_validation.py`

步骤：

1. 分析链终止并撤销运行器后，评分器只读打开运行库和隐藏答案库。
2. 计算解析、身份、Top-3、证据不足停止、安全下一步、轨迹完整度和 AI 增益。
3. 实现九项硬门禁；任何失败、跳题、数据不可用或基础设施错误都不能给出试点通过。
4. 保存逐题期望与实际差异、随机种子和版本快照。
5. 揭晓隐藏题后将题包版本标记 `revealed`，以后只能用于开发回归。

验收：评分可单独重跑；运行器无法查询 secret；揭晓后的题包不能产生发布验收结论。

## 切片三：管理员接口和权限

### 任务 6：增加沙盒管理 API

文件：

- 修改 `idcops/service.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_sandbox_validation_api.py`

接口：

- `GET /api/admin/sandbox/summary`
- `GET /api/admin/sandbox/suites`
- `POST /api/admin/sandbox/runs`
- `GET /api/admin/sandbox/runs/{run_id}`
- `GET /api/admin/sandbox/runs/{run_id}/cases`
- `GET /api/admin/sandbox/runs/{run_id}/cases/{case_id}`
- `POST /api/admin/sandbox/runs/{run_id}/reveal`
- `POST /api/admin/sandbox/runs/{run_id}/reset`
- `GET /api/admin/sandbox/reports/{run_id}`

步骤：

1. AI 管理员和最高管理员可运行、查看报告；只有最高管理员可揭晓隐藏答案。
2. 所有路径参数、运行状态和页大小由服务端校验。
3. 运行接口同步完成首版轻量测试；结构保留后续后台任务扩展点。
4. 错误响应使用中文说明具体原因和可行下一步。
5. 揭晓、重建和回归候选操作进入沙盒治理审计。

验收：权限测试覆盖普通角色、AI 管理员和最高管理员；越权不能读取 secret。

## 切片四：紧凑沙盒验收台

### 任务 7：增加 AI 控制台“沙盒验证”页面

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`

设计系统：

- 主题：IDC 上线前的“验收台”，不是通用测试管理器。
- 色彩：沿用深墨蓝 `#10242d`、设备灰 `#e9f0f2`、可信青 `#10a89a`、阻断红 `#d84c4c`、未完成橙 `#e9963a`。
- 字体：中文正文使用系统无衬线；运行编号、种子、版本和指标使用等宽字体。
- 布局：顶部结论带 + 中部双轨对比 + 底部失败队列；题目详情使用右侧抽屉。
- 视觉签名：一条可审计“证据轨”，清楚标记题面、正式链路、基线、AI、隐藏评分五个边界。

步骤：

1. AI 控制台新增“沙盒验证”标签，可通过 `#ai-control=sandbox` 直接访问。
2. 总览首屏显示完整性、硬门禁、基线与 AI、数据真实性和生产库零污染证据。
3. 运行按钮明确写“运行完整盲测”；模型未连接时先提示 AI 轨道不会运行。
4. 120 道题用分类、状态和失败筛选，不整页展开。
5. 详情抽屉显示可见题面、正式链路记录、评分差异和终态后可用的揭晓入口。
6. 加载、空、失败和不完整状态都显示下一步，不只依赖短暂提示。

验收：1440×900 首屏能看到最终结论和核心指标；390px 无页面级横向溢出。

### 任务 8：补齐键盘、焦点和防误操作

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`

步骤：

1. 所有按钮、筛选器、进度和详情抽屉具有可访问名称。
2. 抽屉打开后设置焦点，Escape 关闭并恢复原触发按钮。
3. 运行和揭晓使用明确确认；揭晓说明该题包将退出后续盲测验收。
4. 加载使用 `aria-busy`，关键结果使用持久状态区域。
5. 状态同时使用文字和图形，不只依赖颜色；尊重减少动画设置。

## 切片五：自动验证、浏览器验收和文档

### 任务 9：增加沙盒自动验证脚本

文件：

- 新增 `scripts/run_sandbox_validation.py`
- 新增 `reports/sandbox-validation-latest.json`
- 新增 `reports/sandbox-validation-latest.md`

步骤：

1. 创建固定种子的 120 题运行并生成 JSON/Markdown 报告。
2. 输出完整性、基线、AI 状态、硬门禁、失败题和数据来源。
3. 验证生产数据库关键表计数运行前后不变。
4. 报告明确标注模拟/公开数据与生产准确率边界。

### 任务 10：真实浏览器测试与完整回归

文件：

- 新增 `scripts/sandbox_validation_browser_smoke.py`
- 更新 `README.md`

步骤：

1. 使用真实浏览器打开沙盒验证页，运行测试并查看报告、筛选和详情。
2. 检查控制台错误、失败网络请求、桌面布局和 390px 移动布局。
3. 测试键盘焦点、Escape 关闭、角色权限和模型未连接说明。
4. 运行沙盒单元/API 测试、现有完整测试集和 JavaScript 语法检查。
5. 保存可复核截图和最终报告。

验收命令：

```text
python3 -m unittest tests.test_sandbox_validation tests.test_sandbox_validation_api -v
python3 -m unittest discover -s tests -v
node --check web/app.js
python3 scripts/sandbox_validation_browser_smoke.py
python3 scripts/run_sandbox_validation.py --seed 20260827
```

## 完工判据

1. 120 道题按设计比例存在，且同种子可复现。
2. 题面、运行结果与隐藏答案分库；运行期无法揭晓。
3. 沙盒全程复用正式处理链，生产数据库零新增。
4. 基线与真实 AI 分轨；无模型时 AI 明确未运行。
5. 不完整测试、答案泄漏或安全门禁失败不会得到试点通过。
6. 页面首屏可读、列表不超长、桌面和手机均可操作。
7. 自动测试、浏览器测试和完整回归均有当次运行证据。
