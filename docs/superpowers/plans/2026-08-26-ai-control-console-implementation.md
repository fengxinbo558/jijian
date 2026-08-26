# 独立 AI 控制台实施计划

设计依据：`docs/superpowers/specs/2026-08-26-ai-control-console-design.md`

实施原则：保留现有数据和接口；每个后端切片先写失败测试再实现；页面改造先复用现有功能，再补新增能力；任何配置变更都不能绕过现有现场安全门禁。

## 切片一：约束与检索测试后端

### 任务 1：增加版本化约束资产表

文件：

- 修改 `idcops/store.py`
- 修改 `idcops/assets.py`
- 新增 `idcops/constraints.py`
- 新增 `tests/test_constraints.py`

步骤：

1. 写迁移测试：旧数据库升级后原事件、知识和提示词数量不变。
2. 新增 `constraint_profiles`、`constraint_versions`、`retrieval_test_runs`。
3. 写入默认 `investigation-policy` 已发布版本；包含 `retrieval_top_k`、`vector_assist_enabled`、`vector_only_min_similarity`、`evidence_excerpt_limit`、`no_evidence_mode` 和 `allowed_domains`。
4. 硬安全门禁从代码登记为只读说明，不放入可关闭字段。
5. 实现约束列表、详情、新草稿和当前已发布配置读取。
6. 验证迁移幂等、版本不可覆盖、非法范围被拒绝。

验收命令：`python3 -m unittest tests.test_constraints -v`

### 任务 2：让实际检索器读取已发布约束

文件：

- 修改 `idcops/knowledge.py`
- 修改 `idcops/investigation.py`
- 修改 `idcops/service.py`
- 扩展 `tests/test_knowledge.py`
- 扩展 `tests/test_investigation.py`

步骤：

1. 先写测试证明默认配置保持现有召回结果。
2. 将 Top-K、向量辅助开关和纯向量最低相似度注入 `KnowledgeBase.search`。
3. 只读取已发布约束；草稿不能影响生产分析。
4. RAG 轨迹保存约束版本和生效参数。
5. 硬门禁继续在权限、输出校验和操作服务执行，不由约束资产决定。

验收命令：`python3 -m unittest tests.test_knowledge tests.test_investigation tests.test_rag_trace -v`

### 任务 3：增加真实检索测试服务

文件：

- 新增 `idcops/retrieval_tests.py`
- 修改 `idcops/service.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_retrieval_tests.py`

接口：

- `POST /api/admin/retrieval-tests`
- `GET /api/admin/retrieval-tests`
- `GET /api/admin/rag-index`

步骤：

1. 测试 AI 管理员可运行、普通角色返回 403。
2. 输入脱敏文本、领域、设备类型和可选事实；调用生产使用的同一知识检索器。
3. 返回标准化请求、Top-K、精确事实/关键词/本地向量贡献、命中理由和覆盖状态。
4. 保存测试记录，但不得创建事件、告警、工单或现场操作。
5. 索引状态接口如实返回本地特征向量能力、维度、已发布知识数量和运行时可重建状态。

验收命令：`python3 -m unittest tests.test_retrieval_tests -v`

### 任务 4：扩展统一发布服务

文件：

- 修改 `idcops/releases.py`
- 修改 `idcops/server.py`
- 扩展 `tests/test_admin_api.py`
- 扩展 `tests/test_release_trust.py`

步骤：

1. 将 `constraint` 加入可测试和发布的资产类型。
2. 约束测试包含字段范围、硬门禁冲突、固定检索回归和回滚目标。
3. 测试通过后才能准备上线；发布仍要求最终确认。
4. 发布事务失败时旧约束继续生效。
5. 发布、回滚和拒绝原因写入审计。

验收命令：`python3 -m unittest tests.test_admin_api tests.test_release_trust -v`

## 切片二：独立 AI 控制台界面

### 任务 5：把弹窗迁移成一级页面

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`
- 修改 `tests/test_role_views.py`

步骤：

1. 左侧“数据与AI”改为“AI控制台”。
2. 增加独立 `adminView`，通过 `#ai-control=<module>` 保存当前栏目。
3. 将现有数据库、知识、提示词、模型、发布和分析轨迹 DOM 迁入页面；删除弹窗依赖但保留原数据加载函数。
4. 增加控制台返回故障中心入口；浏览器前进/后退保持栏目。
5. AI 运营管理员和最高审计管理员显示入口；其他角色隐藏入口且服务端继续拒绝。
6. 角色名称改为“AI运营管理员”和“最高审计管理员”，不改变底层角色键。

验收：现有管理功能全部可访问；关闭弹窗不再是退出管理后台的唯一方式。

### 任务 6：重组九个管理模块

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`

步骤：

1. 栏目按“数据与知识 / AI配置 / 治理与追溯”分组。
2. 数据资产复用记录浏览器；审计记录成为独立栏目。
3. “经验知识库”更名为“RAG知识库”，表单按业务字段展示并保留完整 JSON 高级模式。
4. 提示词模块显示线上、草稿、测试状态轨。
5. 分析轨迹与 RAG 知识库分离。
6. 模型厂商、版本上线保留全部现有按钮和安全说明。

验收：九个模块无需长弹窗滚动即可直接进入，现有按钮没有丢失。

### 任务 7：增加检索测试与约束中心界面

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`

步骤：

1. 检索测试提供日志/事实输入、领域、设备类型和 Top-K 结果。
2. 结果显示命中卡、各分支贡献、命中理由、知识版本和约束版本。
3. 约束中心上半区展示不可关闭的硬门禁及执行位置。
4. 下半区展示可调策略线上版本、草稿表单和测试/发布入口。
5. 明确提示“检索测试不创建生产故障”。
6. 所有加载失败显示具体模块、中文原因和重试按钮。

验收：管理员能完成“修改草稿 → 测试 → 发布 → 新检索采用 → 回滚”的完整可视流程。

### 任务 8：响应式、键盘与视觉校准

文件：

- 修改 `web/styles.css`
- 修改 `web/index.html`
- 新增 `scripts/ai_control_console_browser_smoke.cjs`

步骤：

1. 桌面端使用全局导航、控制台栏目、工作区三段布局。
2. 390px 宽度下改为顶部分组和单列详情，不产生页面横向溢出。
3. 所有栏目、表单和状态使用语义化标签、可见焦点和文字状态。
4. 状态轨使用“线上 / 草稿 / 测试 / 停用”而非单靠颜色。
5. 截图检查数据资产、RAG知识、约束中心、检索测试和移动端。

验收命令：`node scripts/ai_control_console_browser_smoke.cjs`

## 切片三：兼容、说明与最终验证

### 任务 9：补充回归和使用说明

文件：

- 修改 `README.md`
- 修改 `scripts/browser_smoke.cjs`
- 修改 `scripts/incident_center_browser_smoke.cjs`
- 更新必要的浏览器截图报告

步骤：

1. 说明两个管理员角色、九个模块、知识录入、检索测试、提示词和约束发布流程。
2. 验证故障中心、现场作业、故障演练和数据来源入口未受影响。
3. 验证历史 48 张知识卡、4 类提示词和历史 RAG 轨迹可读。
4. 验证模拟事件不混入真实故障队列。

### 任务 10：完整验证与提交

命令：

1. `python3 -m unittest discover -s tests -v`
2. `node --check web/app.js`
3. `node scripts/browser_smoke.cjs`
4. `node scripts/incident_center_browser_smoke.cjs`
5. `node scripts/ai_control_console_browser_smoke.cjs`
6. `git diff --check`

最终检查：

- 只提交本任务文件，不纳入此前已有的评测报告改动和 `.superpowers/` 临时目录。
- 在正式 `8765` 地址执行只读验收并打开 AI 控制台给用户。
