# AI 资产治理中心实施计划

日期：2026-08-26
设计依据：`docs/superpowers/specs/2026-08-26-ai-asset-governance-design.md`

## 实施原则

- 现有知识、提示词、约束、发布记录和运行数据库继续作为真源，不复制第二套正文。
- 每个后端切片先写失败测试，再实现并运行对应测试。
- 迁移只新增表和索引，不重写现有事件及版本。
- AI 只产生重复、冲突和关系候选；合并、忽略、停用和发布必须由有权限的人确认。
- 新界面使用紧凑目录、问题队列和右侧详情抽屉，避免重新形成超长页面。
- 原有故障中心、演练、接入实验室、现场操作和 AI 控制台功能必须保持可用。

## 切片一：治理数据和真实目录

### 任务 1：增加治理表和幂等迁移

文件：

- 修改 `idcops/store.py`
- 新增 `tests/test_asset_governance.py`

步骤：

1. 写旧数据库升级测试，证明现有事件、知识、提示词、约束和运行记录数量不变。
2. 新增资产元数据、版本元数据、关系、治理问题、导入批次、导入项、来源版本、资产反馈、运行快照和测试用例版本表。
3. 为资产类型与编号、问题状态、导入批次、关系端点、反馈事件和复审时间建立索引。
4. 为现有知识、提示词和约束生成最小目录元数据；未知负责人和适用范围保持“待补充”。
5. 验证重复执行迁移不会新增重复目录记录。

验收：`python3 -m unittest tests.test_asset_governance.AssetGovernanceMigrationTests -v`

### 任务 2：实现统一资产目录服务

文件：

- 新增 `idcops/governance.py`
- 修改 `idcops/service.py`
- 扩展 `tests/test_asset_governance.py`

步骤：

1. 统一列出知识、提示词、约束和测试用例，不复制正文。
2. 支持分页、搜索、资产类型、领域、状态、负责人、风险和复审状态筛选。
3. 返回目录健康状态、线上版本、草稿数量、问题数量和最近更新时间。
4. 详情返回正文入口、版本、来源、关系、问题、反馈、历史命中和审计摘要。
5. 更新管理信息时保存审计；已发布内容仍只能通过版本服务修改。

验收：`python3 -m unittest tests.test_asset_governance.AssetCatalogTests -v`

## 切片二：重复、冲突和治理队列

### 任务 3：实现确定性内容指纹和重复候选

文件：

- 修改 `idcops/governance.py`
- 扩展 `tests/test_asset_governance.py`

步骤：

1. 对知识正文做稳定标准化，排除编号、版本和审核时间后计算 SHA-256 指纹。
2. 完全重复时生成 `exact_duplicate` 问题并关联已有版本，不创建第二项正式资产。
3. 比较领域、故障族、适用范围、症状、信号、验证步骤和停止条件，生成带字段依据的 `near_duplicate` 候选。
4. 复用本地文本特征作为补充相似信号，并在结果中明确能力来源。
5. 相似能力不可用时保留哈希和结构检查，并返回降级状态。

验收：`python3 -m unittest tests.test_asset_governance.DuplicateDetectionTests -v`

### 任务 4：实现结构化冲突候选和发布阻断

文件：

- 修改 `idcops/governance.py`
- 修改 `idcops/releases.py`
- 扩展 `tests/test_asset_governance.py`
- 扩展 `tests/test_release_trust.py`

步骤：

1. 在适用范围重叠时比较安全动作、停止条件、禁止推断和高风险动作。
2. 与硬安全门禁明确矛盾的项目生成阻断级问题。
3. 自然语言矛盾只标为待人工确认，并显示原字段和比较依据。
4. 待发布版本存在未解决阻断问题时，发布服务拒绝上线并返回中文原因。
5. 处理问题支持合并、分别保留、建立关系、补充条件和忽略；所有决定写入审计。

验收：`python3 -m unittest tests.test_asset_governance.ConflictGovernanceTests tests.test_release_trust -v`

## 切片三：安全导入、反馈和溯源

### 任务 5：实现临时导入批次

文件：

- 修改 `idcops/governance.py`
- 扩展 `tests/test_asset_governance.py`

步骤：

1. 支持粘贴结构化知识、JSON 和 CSV 内容，先写入临时导入区。
2. 每个导入项执行字段校验、来源登记、重复和冲突扫描。
3. 返回准备创建、完全重复、疑似重复、冲突和失败数量。
4. 人工确认批次时使用单一事务生成草稿及版本元数据。
5. 中途失败时不留下半批正式资产；未确认批次可以撤销。

验收：`python3 -m unittest tests.test_asset_governance.ImportBatchTests -v`

### 任务 6：实现关系、反馈、影响和版本快照

文件：

- 修改 `idcops/governance.py`
- 修改 `idcops/rag_trace.py`
- 修改 `idcops/retrieval_tests.py`
- 扩展 `tests/test_asset_governance.py`
- 扩展 `tests/test_rag_trace.py`

步骤：

1. 建立替代、合并、等价、相关、竞争和依赖关系，支持逻辑资产或具体版本端点。
2. 接收采用、否定、解决、未解决和无法验证反馈，关联故障和资产版本。
3. 从 `rag_hits`、反馈、发布和测试记录计算使用效果与影响范围；样本不足时不输出成功率。
4. 正式 RAG 和检索测试保存知识、提示词、约束、模型及检索能力快照。
5. 实现来源 → 资产版本 → RAG 命中 → 故障结果的双向溯源查询。

验收：`python3 -m unittest tests.test_asset_governance.LineageAndFeedbackTests tests.test_rag_trace tests.test_retrieval_tests -v`

### 任务 7：增加版本化测试用例资产

文件：

- 修改 `idcops/governance.py`
- 修改 `idcops/retrieval_tests.py`
- 扩展 `tests/test_asset_governance.py`

步骤：

1. 保存测试用例定义、版本、输入、预期命中、禁止结论和预期门禁。
2. 草稿测试用例不进入发布回归集。
3. 运行测试时保存测试用例版本和资产组合快照。
4. 资产影响分析列出引用它的固定测试用例。

验收：`python3 -m unittest tests.test_asset_governance.TestCaseAssetTests -v`

## 切片四：接口、权限和管理审计

### 任务 8：增加治理 API

文件：

- 修改 `idcops/server.py`
- 修改 `idcops/admin.py`
- 新增 `tests/test_asset_governance_api.py`

接口范围：

- `GET /api/admin/governance/summary`
- `GET /api/admin/assets`
- `GET/PATCH /api/admin/assets/{type}/{key}`
- `GET /api/admin/governance/issues`
- `POST /api/admin/governance/issues/{id}/resolve`
- `GET/POST /api/admin/import-batches`
- `POST /api/admin/import-batches/{id}/confirm`
- `POST /api/admin/import-batches/{id}/cancel`
- `GET/POST /api/admin/asset-relations`
- `GET/POST /api/admin/asset-feedback`
- `GET /api/admin/lineage`
- `GET/POST /api/admin/test-cases`

步骤：

1. AI 运营管理员和最高管理员可进入治理后台。
2. 其他角色只能通过专门的反馈或候选提交接口追加自己职责范围内的信息。
3. 服务端拒绝越权修改、发布和原始证据读取。
4. 所有写入返回可理解的中文错误，关键动作进入统一审计时间线。
5. 列表接口限制页大小，防止数据增长后一次加载全部记录。

验收：`python3 -m unittest tests.test_asset_governance_api -v`

## 切片五：紧凑治理中心界面

### 任务 9：增加治理中心页面骨架和视觉系统

文件：

- 修改 `web/index.html`
- 修改 `web/styles.css`
- 修改 `web/app.js`

设计方向：

- 主题：大型 IDC 的“资产检修台”，不是通用 CMS。
- 调色：沿用深墨蓝、设备灰和青绿色；橙色只用于待治理，红色只用于阻断风险。
- 字体：中文正文沿用系统无衬线，编号与版本使用等宽数据字体。
- 布局：控制台栏目 + 治理工作区；目录固定表头，详情使用右侧抽屉。
- 记忆点：每项资产使用一条“来源—版本—使用结果”状态轨，直接表达资产是否可追查。

步骤：

1. AI 控制台增加“资产治理”栏目，通过 `#ai-control=governance` 直接访问。
2. 治理内部使用“总览、目录、待治理、来源与导入、关系与溯源”五个紧凑标签。
3. 桌面端主工作区不使用整页长卡片；移动端先列表后详情。
4. 原数据资产、知识、提示词、约束、测试和审计栏目保留。
5. 加载、空状态和失败状态说明用户下一步能做什么。

验收：主要目录和治理任务在 1440×900 首屏内可见，390px 宽度无页面级横向溢出。

### 任务 10：实现目录、队列、导入和溯源交互

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`

步骤：

1. 总览显示可行动数量，点击直接带筛选进入队列。
2. 目录支持搜索、类型、状态、领域和复审筛选，保存当前分页和滚动位置。
3. 详情抽屉分为正文、来源、版本、适用范围、关系、效果和审计；关闭后焦点返回原行。
4. 治理比较区左右并排显示差异和检测依据，并提供明确处理按钮。
5. 导入区提供粘贴、JSON/CSV 内容预览、扫描、确认和撤销。
6. 溯源区从来源或资产出发加载真实关系；未知关系显示未知，不画猜测连线。
7. 提示词、约束、测试和发布动作跳转到现有专业编辑器。

验收：能够完成“导入 → 检查 → 处理问题 → 生成草稿 → 跳转测试”的可见流程。

### 任务 11：无障碍和操作防错

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`

步骤：

1. 所有按钮、筛选器、抽屉标签和表单有可访问名称。
2. 详情抽屉打开后设置初始焦点，Escape 关闭，关闭后恢复触发元素焦点。
3. 表单错误使用 `aria-invalid`、`aria-describedby` 和可见错误文本。
4. 加载使用 `aria-busy`，关键结果使用状态区域，不依赖短暂提示条。
5. 风险、状态和差异不只依赖颜色表达；键盘焦点清晰；尊重减少动画设置。
6. 批量确认、合并、停用和发布显示受影响数量及二次确认。

## 切片六：说明、浏览器验证和完整回归

### 任务 12：更新说明并完成真实浏览器测试

文件：

- 修改 `README.md`
- 新增 `scripts/asset_governance_browser_smoke.cjs`
- 必要时扩展现有浏览器测试脚本

步骤：

1. 说明运行证据与 AI 资产治理的区别、五个入口和权限。
2. 测试桌面和 390px 移动视口的总览、目录、筛选、详情、队列、导入与溯源。
3. 测试键盘操作、焦点恢复、加载失败和服务端权限拒绝。
4. 检查浏览器控制台错误和网络失败信息。
5. 回归故障中心、真实故障、模拟演练、接入实验室和现场操作。

最终验证命令：

1. `python3 -m unittest discover -s tests -v`
2. `node --check web/app.js`
3. `node scripts/asset_governance_browser_smoke.cjs`
4. `node scripts/ai_control_console_browser_smoke.cjs`
5. `node scripts/incident_center_browser_smoke.cjs`
6. `node scripts/drill_browser_smoke.mjs`
7. `git diff --check`

最终检查：

- 对照设计规格逐项核验第一版交付和 14 项验收标准。
- 不纳入用户已有或测试产生的无关报告文件。
- 在正式本地地址 `http://127.0.0.1:8765/#ai-control=governance` 做只读验收并打开页面给用户。
