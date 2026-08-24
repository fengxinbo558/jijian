# 数据与 AI 资产、混合 RAG 及现场复核实施计划

设计依据：`docs/superpowers/specs/2026-08-24-data-ai-assets-hybrid-rag-design.md`  
实施原则：按切片交付；每一步先补测试，再实现；任何切片不得删除现有入口或降低现有安全门。

## 总体交付顺序

1. 数据与 AI 资产基础
2. 知识 / 提示词测试发布
3. 混合 RAG 与运行追踪
4. 现场身份、OMS 快照与远程复核
5. 真实外部平台连接

前三个切片完成后，用户即可查看数据库、维护知识和提示词、在测试环境调试并查看完整 RAG 链路。第四个切片补齐现场协同。第五个切片只有在获得真实接口和授权后执行。

## 切片一：数据与 AI 资产基础

### 任务 1：扩展 SQLite 迁移机制

文件：

- 修改 `idcops/store.py`
- 新增 `tests/test_asset_store.py`

步骤：

1. 先写测试，验证旧数据库升级后仍能读取原事件。
2. 增加 `schema_migrations`，每个迁移只执行一次。
3. 新增版本化资产表：`record_annotations`、`knowledge_cards`、`knowledge_versions`、`knowledge_sources`、`prompt_definitions`、`prompt_versions`、`release_runs`、`evaluation_runs`、`evaluation_results`、`model_providers`、`model_policies`、`rag_runs`、`rag_steps`、`rag_hits`。
4. 所有已发布版本写入后不可原地修改；新增版本必须新建记录。
5. 运行：`python3 -m unittest tests.test_asset_store -v`。

验收：迁移可重复执行；旧事件数量和内容不变；外键和唯一约束生效。

### 任务 2：迁移现有知识卡和提示词

文件：

- 新增 `idcops/assets.py`
- 修改 `idcops/knowledge.py`
- 修改 `idcops/ai.py`
- 新增 `tests/test_asset_migration.py`

步骤：

1. 先写测试，验证 48 张卡、12 个来源和 4 个提示词契约均被导入。
2. 实现幂等迁移；重复启动不能产生重复版本。
3. 将写死的 System 提示词迁入 `prompt_versions`。
4. 运行时优先读取数据库中的已发布版本；数据库尚未迁移时才允许只读文件降级。
5. 每次分析保存使用的知识包和提示词版本。

验收：知识内容和版本不丢；旧测试仍通过；健康接口显示数据库资产数量。

### 任务 3：实现角色和服务端权限骨架

文件：

- 新增 `idcops/auth.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_auth.py`

步骤：

1. 定义 `onsite_operator`、`facility_lead`、`interface_person`、`ai_admin` 四种角色和 `remote_reviewer` 临时能力。
2. 首版使用本地会话角色；默认开发用户为 `ai_admin`，但 API 测试必须覆盖拒绝路径。
3. 所有管理写接口执行服务端权限检查。
4. 原始证据、历史分析和审计记录不提供更新或删除接口。

验收：无权限角色请求发布或修改返回 403；只隐藏前端按钮不能绕过权限。

### 任务 4：实现“我的数据库”查询接口

文件：

- 新增 `idcops/admin.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_admin_api.py`

接口：

- `GET /api/admin/summary`
- `GET /api/admin/records?type=...&q=...&page=...`
- `GET /api/admin/records/{type}/{id}`
- `POST /api/admin/annotations`

步骤：

1. 提供中文业务字段和可选技术字段。
2. 记录列表支持分页、来源、状态、机房和时间筛选。
3. 详情返回原始数据、字段来源、关联事件和审计链。
4. 更正通过追加备注实现，禁止改写原记录。

验收：用户能从事件追到输入、事实、分析、版本和审计；技术字段开关不泄露密钥。

### 任务 5：重构角色工作台导航并保留旧功能

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`
- 修改 `scripts/browser_smoke.cjs`

步骤：

1. 使用“现场工作台、接口人工作台、机房组长、AI 管理员”角色入口。
2. 现场工作台保留真实日志、现场上报、模拟案例、数据来源、机房等级和事件详情。
3. AI 管理员增加“我的数据库、经验知识库、提示词中心、RAG 分析链路、模型与厂商、模拟测试中心”。
4. 按钮使用动作词，不使用含义模糊的“事件接入”。
5. 响应式检查桌面和手机布局。

验收：现有 13 个案例、真实故障入口和事件详情仍可用；浏览器冒烟测试通过。

## 切片二：知识和提示词测试发布

### 任务 6：知识库读写和版本接口

文件：

- 扩展 `idcops/assets.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_knowledge_admin.py`

接口：

- `GET /api/admin/knowledge`
- `GET /api/admin/knowledge/{id}`
- `POST /api/admin/knowledge`
- `POST /api/admin/knowledge/{id}/versions`
- `POST /api/admin/knowledge/{id}/deactivate`

步骤：

1. 表单字段完全覆盖现有知识契约。
2. 编辑已发布知识时新建草稿版本，不原地覆盖。
3. 新场景可保存为并列分支。
4. 详情显示版本、来源、使用次数和命中结果。

验收：第四次故障可重新命中第一版经验；停用版本不参与新召回但历史可读。

### 任务 7：提示词中心和实际消息预览

文件：

- 新增 `idcops/prompts.py`
- 修改 `idcops/ai.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_prompt_admin.py`

接口：

- `GET /api/admin/prompts`
- `GET /api/admin/prompts/{key}`
- `POST /api/admin/prompts/{key}/versions`
- `POST /api/admin/prompts/{key}/preview`

步骤：

1. 分离 System、用户模板、变量、输出契约、模型参数和禁止行为。
2. 预览接口使用测试数据渲染脱敏后的完整 `messages`，不调用模型。
3. AI 调用记录保存提示词版本、模型、脱敏后的实际消息和原始输出。
4. 密钥只保存配置状态，不通过 API 返回明文。

验收：管理员能看到原始模板和某次运行实际发送的内容；提示词不能关闭硬性安全校验。

### 任务 8：测试环境、差异比较、上线和回滚

文件：

- 新增 `idcops/releases.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_releases.py`

接口：

- `POST /api/admin/releases/test`
- `GET /api/admin/releases/{id}`
- `POST /api/admin/releases/{id}/prepare`
- `POST /api/admin/releases/{id}/publish`
- `POST /api/admin/releases/{id}/rollback`

步骤：

1. 草稿只能发布到测试环境。
2. 测试失败时阻止准备上线。
3. 准备上线返回新旧差异、影响范围和回滚目标。
4. 当前模式要求同一管理员完成两步确认。
5. 预留 `dual_approval_required` 开关，正式版由第二管理员批准。
6. 发布使用事务，失败时旧版本继续生效。

验收：误点一次不能上线；发布和回滚均有审计；双人模式不能自审自批。

### 任务 9：知识和提示词管理界面

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`
- 扩展浏览器冒烟测试

步骤：

1. 实现知识列表、筛选、详情、版本和新增分支。
2. 实现提示词编辑器的 System、用户模板、变量、输出结构和模型参数页签。
3. 实现草稿、测试、差异预览、两步上线和回滚。
4. 危险按钮和测试按钮视觉分离。

验收：所有动作有明确反馈；刷新后草稿和版本仍存在；无权限角色看不到写按钮且 API 同样拒绝。

## 切片三：混合 RAG 与全链路追踪

### 任务 10：全文检索和嵌入提供方接口

文件：

- 新增 `idcops/embeddings.py`
- 新增 `idcops/retrieval.py`
- 修改 `idcops/store.py`
- 新增 `tests/test_retrieval.py`

步骤：

1. 为已发布知识建立 SQLite FTS5 全文索引。
2. 定义 `EmbeddingProvider`，测试使用确定性本地桩。
3. 生产默认读取本地模型路径或本地兼容接口；未配置时明确禁用向量分支，不冒充语义检索。
4. 以 SQLite 保存知识向量和索引版本；当前 48 张卡使用内存余弦排序即可，避免提前引入独立向量数据库。
5. 日志只生成查询向量，不保存未经授权的原始外发内容。

验收：FTS 和向量分支均有独立测试；嵌入服务不可用时安全退回规则＋关键词。

### 任务 11：实现混合召回和透明排序

文件：

- 修改 `idcops/knowledge.py`
- 修改 `idcops/investigation.py`
- 修改 `idcops/retrieval.py`
- 新增 `tests/test_hybrid_rag.py`

步骤：

1. 并行执行规则、事实、关键词和向量召回。
2. 只保留已发布且适用设备 / 环境满足的版本。
3. 合并去重后保存各分支分数和人类可读命中理由。
4. 向量相似不能直接提升为 `confirmed`。
5. 未召回知识时显示覆盖不足，不用模型常识补造。

验收：关键测试案例的预期卡进入 Top-K；短 ASCII 标识仍按完整词匹配。

### 任务 12：RAG 运行追踪和模型校验记录

文件：

- 新增 `idcops/rag_trace.py`
- 修改 `idcops/investigation.py`
- 修改 `idcops/ai.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_rag_trace.py`

接口：

- `GET /api/admin/rag-runs`
- `GET /api/admin/rag-runs/{id}`

步骤：

1. 保存原始输入引用、事实、检索请求、命中结果、提示词版本、模型、原始输出和校验结果。
2. 记录模型输出被删除或降级的原因。
3. 不在追踪页面返回未脱敏密钥、令牌或受限字段。
4. 事件重新分析生成新的 RAG 运行，不覆盖旧记录。

验收：从最终候选可以逆向追到证据、知识、提示词和模型调用；模型失败时追踪明确标记降级。

### 任务 13：RAG 链路界面

文件：

- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`
- 扩展浏览器冒烟测试

步骤：

1. 提供总体架构图和单次运行列表。
2. 单次运行依次展示八步：原始输入、事实、检索请求、召回结果、模型输入、模型原始输出、校验、最终结果。
3. 每张知识卡显示规则、事实、关键词、语义和适用条件理由。
4. 明确显示本次是否执行了向量检索和模型调用。

验收：用户不用读代码即可解释某条结论的来源；降级路径不会伪装成完整 AI 调查。

## 切片四：现场身份、OMS 与远程复核

### 任务 14：资产、工单快照和操作状态

文件：

- 新增 `idcops/work_orders.py`
- 新增 `idcops/operations.py`
- 修改 `idcops/store.py`
- 新增 `tests/test_operations.py`

步骤：

1. 新增 `asset_devices`、`work_order_snapshots`、`operation_permissions`、`operation_reviews` 和操作结果字段。
2. 实现细粒度操作状态机和非法跳转保护。
3. OMS 先实现模拟连接器和人工导入；保存不可变快照和版本。
4. 将“设备身份确认”和“操作许可”实现为独立门。
5. 完成结果支持成功 / 失败、结构化原因、详细反馈、上线 / 下线 SN 和超时原因。

验收：工单后续变化不覆盖旧快照；缺任一道门不能进入可操作状态；失败接单可追查并显示在再次派单中。

### 任务 15：扫码、OCR 回退和现场复核界面

文件：

- 新增 `web/device-scan.js`
- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/styles.css`
- 新增服务器端扫描结果接口和测试

步骤：

1. 优先使用浏览器可用的条形码 / 二维码扫描能力。
2. 不支持或失败时进入 SN 局部照片 OCR 适配接口；首版允许本地模拟 OCR，不承诺未配置时自动识别。
3. 最后回退到完整 SN 手工输入并增加复核要求。
4. 与工单 SN 逐字符比对；不一致时明确阻止。
5. 条码成功默认不保存画面；OCR 只保存裁剪后的 SN 局部证据。

验收：完整 SN 一致 / 不一致 / 无法识别三种路径可测试；不能只比较末位；设备身份一致不自动等于允许操作。

### 任务 16：远程复核、排班和通知升级

文件：

- 新增 `idcops/reviews.py`
- 新增 `idcops/notifications.py`
- 修改 `idcops/server.py`
- 新增 `tests/test_reviews.py`

步骤：

1. 实现现场双人复核和单人远程复核。
2. 远程复核人不能与操作人相同。
3. 建立夜间授权复核池、当前接手人、备用人和组长升级。
4. 同一事故去重提醒；有人接手后停止其他升级。
5. 首版实现产品内队列和浏览器通知；电话和如流仅提供适配接口及人工快捷入口。
6. “紧急”读取 OMS / 影响 / 时效规则，普通用户不能随意触发电话升级。

验收：普通夜间任务不叫醒组长；紧急任务按链路升级；无人授权时保持阻止状态。

## 切片五：真实平台连接

### 任务 17：模型和嵌入厂商适配

文件：

- 扩展 `idcops/ai.py`
- 扩展 `idcops/embeddings.py`
- 新增 `idcops/providers.py`
- 新增 `tests/test_providers.py`

步骤：

1. 支持本地模型、合作厂商私有化接口和经授权云接口。
2. 每个提供方配置数据分类、驻留、超时、回退和模型版本。
3. 未授权时阻止完整 SN、内网 IP、客户身份、业务信息和原始日志外发。
4. 失败时优先退回本地能力，不自动改发另一个外部厂商。

验收：外发包可审计；密钥不回显；策略阻止测试通过。

### 任务 18：OMS / CMDB / 如流连接器

文件：

- 新增 `idcops/connectors/oms.py`
- 新增 `idcops/connectors/cmdb.py`
- 新增 `idcops/connectors/ruliu.py`
- 新增相应契约测试和连接状态页面

步骤：

1. 只有获得官方接口文档、测试环境和用户授权后才实现真实连接。
2. 所有连接器先定义输入 / 输出契约，再接真实 API。
3. 连接失败时保留人工路径并明确显示未连接，不用网页抓取伪装为正式接口。
4. 如流只用于通知和深链，不在未授权时自动发送消息。

验收：真实与模拟数据显式区分；接口不可用时不影响本地调查和审计。

## 全程验证

每个切片完成后执行：

```bash
python3 -m unittest discover -s tests -v
python3 evals/run_evaluation.py
python3 evals/run_synthetic_logs.py
node scripts/browser_smoke.cjs
```

最终还需验证：

- 现有 13 个页面案例全部可运行。
- 现有 36 组合成日志和全部检查点不回退。
- 48 张知识卡迁移前后数量、内容和来源一致。
- 无证据的 `confirmed` 为零。
- AI 生成操作许可为零。
- 每次知识 / 提示词上线和回滚均可审计。
- 每次 RAG 运行均能显示实际执行的检索和模型能力。
- 原始日志、工单快照、人工确认和历史分析没有更新 / 删除入口。

## 提交策略

每个任务独立提交，提交信息使用：

- `test:` 新增失败测试
- `feat:` 新增可用能力
- `fix:` 修复回归或安全问题
- `docs:` 更新使用和运维说明

若某任务必须跨多个文件完成，仍在测试通过后形成一个可回滚提交；不得把五个切片压成一个巨大提交。
