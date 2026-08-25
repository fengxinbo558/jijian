# 最小生产闭环实施计划

日期：2026-08-25  
设计依据：`docs/superpowers/specs/2026-08-25-minimum-production-loop-design.md`

## 实施原则

- 保留现有接口、事故调查、RAG、角色、现场操作和 OMS 流程；
- 新增告警治理层，不把现有事故表改造成万能表；
- 先写行为测试，再实现最小代码；
- 所有生产动作保持只读或人工确认；
- 公开数据的来源、用途和许可证必须可见；
- 每个阶段完成后运行对应测试，最后执行全量测试和浏览器验证。

## 任务一：生产治理数据结构

### 文件

- 修改：`idcops/store.py`
- 新增：`idcops/production.py`
- 新增：`tests/test_production_governance.py`

### 行为测试

1. 同一告警指纹重复输入只保留一条活动告警并增加次数；
2. 恢复信号更新原告警为 `recovered`；
3. 告警恢复后关联事故仍处于待验证状态；
4. 维护窗口命中时告警为 `silenced` 且不创建事故；
5. 已存在上游活动故障时，下游信号为 `suppressed`；
6. 数据源健康计数和最后接收时间会更新。

### 实现

- 新建治理相关表和索引；
- 实现 `ProductionGovernance` 服务；
- 以明确来源 ID、实体 ID 和信号类型计算指纹；
- 支持发生、恢复、确认、抑制、静默状态；
- 把需要处置的告警调用现有 `IncidentService.ingest()`。

## 任务二：可信身份、变更与数据质量

### 文件

- 修改：`idcops/production.py`
- 新增：`tests/test_production_trust.py`

### 行为测试

1. 同一字段多个来源时按字段权威优先级选择；
2. 权威来源冲突生成身份冲突；
3. 过期断言降低身份可信度；
4. 阻断冲突返回 `operation_blocked=true`；
5. 最近变更可以按站点/实体/时间查询；
6. 变更仅标记为候选证据，不自动确认根因；
7. 采集器异常与设备无数据可区分。

### 实现

- 实现身份断言、权威规则和冲突检测；
- 实现变更事件与前后快照；
- 计算字段完整性、身份可信度、时间新鲜度和来源健康评分；
- 将最近变更和数据质量加入事故分析扩展字段。

## 任务三：值班、分派、纠正与指标

### 文件

- 修改：`idcops/production.py`
- 新增：`tests/test_production_workflow.py`

### 行为测试

1. 可以创建值班记录并查询当前值班人员；
2. 事故可以分派、确认、延后和升级；
3. 延后必须记录原因；
4. 合并、拆分、无关反馈均保留审计；
5. 指标返回有效告警、抑制率、恢复待验证、身份冲突、无人负责和AI采纳情况。

### 实现

- 实现值班表和事故分派；
- 实现人工纠正反馈；
- 实现调查效果指标聚合。

## 任务四：API 与角色权限

### 文件

- 修改：`idcops/service.py`
- 修改：`idcops/server.py`
- 修改：`idcops/views.py`
- 新增：`tests/test_production_api.py`

### 行为测试

1. 生产总览、告警、来源健康、冲突、变更和指标可读取；
2. 接口人、AI管理员和最高管理员可以写入其权限内的数据；
3. 现场人员不能修改全局维护窗口或身份权威配置；
4. 所有写入接口返回明确状态和审计信息；
5. 旧接入接口继续工作。

### 实现

- 注册 `ProductionGovernance`；
- 新增 `/api/production/*` 路由；
- 复用现有 `X-IDC-Role` 和账号头；
- 对列表结果进行角色投影。

## 任务五：公开数据目录与导入

### 文件

- 新增：`idcops/public_datasets.py`
- 新增：`data/public-datasets/catalog.json`
- 新增：`tests/test_public_datasets.py`
- 修改：`.gitignore`
- 修改：`THIRD_PARTY.md`

### 行为测试

1. 六个公开数据源均展示来源、用途、许可证和获取方式；
2. 受限或大体量数据不会进入仓库；
3. 导入小样本会生成导入报告；
4. LogHub/GAIA 文本或 CSV 可转成生产告警测试输入；
5. Backblaze SMART CSV 可转成磁盘健康信号；
6. Redfish JSON 可转成 BMC 资产与健康信号；
7. 导入失败不会生成半成品记录。

### 实现

- 建立数据目录；
- 实现本地文件导入器和小样本生成器；
- 实际下载文件放入忽略目录；
- 记录哈希、来源、导入时间、行数和错误数。

## 任务六：告警治理页面

### 文件

- 修改：`web/index.html`
- 修改：`web/app.js`
- 修改：`web/styles.css`
- 修改：`scripts/browser_smoke.py`

### 页面

- 主导航增加“告警治理”；
- 显示数据源健康、有效告警、静默/抑制、待恢复验证、身份冲突、无人负责；
- 告警表显示来源、实体、生命周期、次数、事故和原因；
- 提供维护窗口、身份断言、变更、值班和纠正的测试表单；
- 提供公开数据目录和导入测试按钮；
- 页面用中文说明每个按钮的作用。

### 验证

- 桌面和移动端页面可读；
- 角色切换后可见内容不同；
- 键盘可操作，表单有标签和错误反馈；
- 浏览器烟测覆盖打开页面、提交测试告警和查看治理结果。

## 任务七：真实场景回归集

### 文件

- 新增：`evals/production_loop_cases.json`
- 新增：`evals/run_production_loop.py`
- 新增：`reports/production-loop-results.json`
- 新增：`reports/production-loop-report.md`
- 修改：`README.md`

### 场景

- 网络上游故障与下游抑制；
- 端口抖动恢复；
- 维护窗口；
- 采集器中断；
- 身份冲突；
- 变更关联；
- BMC 与 OS 证据不一致；
- 动环区域高温和单传感器异常；
- 夜间单人值班排队与升级；
- 重复工单、错误合并和复发；
- 恢复但业务未恢复；
- 两个独立故障并存。

## 最终验证

1. `python -m unittest discover -s tests -v`
2. `python evals/run_evaluation.py`
3. `python evals/run_synthetic_logs.py`
4. `python evals/run_production_loop.py`
5. 启动本地服务并运行浏览器烟测；
6. 检查 `git diff --check`；
7. 更新验证报告，只陈述实际通过的结果。
