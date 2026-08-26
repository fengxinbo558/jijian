const state = {
  incidents: [],
  selectedId: null,
  selected: null,
  sources: [],
  facilities: [],
  filter: "active",
  query: "",
  categoryFilter: "all",
  severityFilter: "all",
  view: "list",
  detailTab: "overview",
  listScrollTop: 0,
  role: localStorage.getItem("idcai-role") || "ai_admin",
  actor: localStorage.getItem("idcai-actor") || "local-admin",
  operations: [],
  selectedOperation: null,
  lab: {
    tab: "signals",
    platforms: [],
    scenarios: [],
    events: [],
    topology: { entities: [], links: [] },
    agentRuns: [],
    selectedAgentRun: null,
    backups: [],
  },
  drills: {
    catalog: null,
    loadStatus: "idle",
    loadError: "",
    category: "network",
    selectedScenarioId: "net-optical-module",
    runs: [],
    active: null,
  },
  governance: {
    tab: "alerts",
    overview: {},
    alerts: [],
    maintenance: [],
    sourceHealth: [],
    identityConflicts: [],
    changes: [],
    rosters: [],
    assignments: [],
    feedback: [],
    datasets: [],
  },
  admin: {
    tab: "database",
    summary: null,
    knowledge: [],
    prompts: [],
    releases: [],
    ragRuns: [],
    providers: [],
    selectedKnowledge: null,
    selectedPrompt: null,
    selectedRagRun: null,
    selectedProvider: null,
    pendingPublishId: null,
  },
};

const labels = {
  status: { new: "新发现", processing: "处理中", resolved: "已解决" },
  severity: { critical: "严重", warning: "警告", info: "信息", unknown: "未知" },
  category: {
    hardware: "硬件",
    network: "网络",
    application: "应用",
    facility: "动环",
    system: "系统",
    unknown: "待确认",
  },
  source: { monitor: "监控", log: "日志", onsite: "现场" },
  facility: { core: "核心机房", normal: "普通机房", unknown: "未确认" },
  ccDecision: { required: "需要CC", needs_confirmation: "等待确认", not_required: "普通处理" },
  impact: { alarm_only: "只有告警", redundancy_degraded: "冗余降低", partial_outage: "部分设备受影响", widespread_outage: "核心/大范围中断" },
  facilityEvent: {
    single_feed_loss: "单路掉电",
    dual_feed_loss: "双路掉电",
    water_leak: "漏水但未确认设备影响",
    water_caused_core_device_failure: "漏水导致核心设备故障",
    core_switch_outage: "核心交换机宕机",
    temperature_rising: "温度升高",
    power_supply_failure: "电源模块异常",
    smoke_alarm: "烟雾或消防告警",
    general_incident: "普通故障",
  },
  unknown: { unknown: "未知", on: "已点亮", off: "未点亮", yes: "是", no: "否" },
};

const listEl = document.querySelector("#incidentList");
const detailEl = document.querySelector("#incidentDetail");
const toastEl = document.querySelector("#toast");
const ingestDialog = document.querySelector("#ingestDialog");
const demoDialog = document.querySelector("#demoDialog");
const sourceDialog = document.querySelector("#sourceDialog");
const facilityDialog = document.querySelector("#facilityDialog");
const adminDialog = document.querySelector("#adminDialog");
const publishConfirmDialog = document.querySelector("#publishConfirmDialog");
const operationDialog = document.querySelector("#operationDialog");
const cameraDialog = document.querySelector("#cameraDialog");
const labDialog = document.querySelector("#labDialog");
const governanceDialog = document.querySelector("#governanceDialog");
const drillDialog = document.querySelector("#drillDialog");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function translated(group, value) {
  return labels[group]?.[value] || value || "未知";
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-IDCAI-Role": state.role,
        "X-IDCAI-User": state.actor,
        ...(options.headers || {}),
      },
    });
  } catch (_error) {
    throw new Error("无法连接后台服务。页面可能仍显示上次数据，请确认本地服务已启动后重试");
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = { error: "服务返回了无法读取的内容" };
  }
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function showToast(message, isError = false) {
  toastEl.textContent = message;
  toastEl.classList.toggle("is-error", isError);
  toastEl.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toastEl.classList.remove("is-visible"), 3000);
}

function primaryIdentity(incident) {
  const device = incident.devices?.[0] || {};
  return device.sn || device.name || device.rack_position || "设备身份待补充";
}

function isSimulationIncident(incident = {}) {
  return Boolean(incident.simulation || incident.investigation?.simulation || (incident.inputs || []).some((item) => item.simulation));
}

function isWaitingForHuman(incident = {}) {
  if (incident.status === "resolved") return false;
  const card = incident.onsite_card || {};
  const nextStep = incident.investigation?.conclusion?.next_step || "";
  return Boolean(
    (card.required && ["confirm", "stop"].includes(card.power?.gate))
    || /等待人工|等待现场|等待.{0,8}确认|许可|复核|完整\s*SN/i.test(nextStep)
  );
}

function incidentLane(incident = {}) {
  if (isSimulationIncident(incident)) return "simulation";
  if (incident.status === "resolved") return "resolved";
  if (incident.status === "new") return "new";
  if (isWaitingForHuman(incident)) return "waiting_human";
  return "processing";
}

function incidentNextAction(incident = {}) {
  if (incident.status === "resolved") return "查看恢复验证和处理记录";
  if (isWaitingForHuman(incident)) {
    return incident.onsite_card?.power?.message
      || incident.investigation?.conclusion?.next_step
      || "等待人工确认后继续";
  }
  return incident.investigation?.conclusion?.next_step
    || (incident.status === "new" ? "等待认领并开始调查" : "继续补充只读证据");
}

function filteredIncidents() {
  const query = state.query.trim().toLowerCase();
  return state.incidents.filter((incident) => {
    const lane = incidentLane(incident);
    if (state.filter === "active") {
      if (!["new", "processing", "waiting_human"].includes(lane)) return false;
    } else if (lane !== state.filter) return false;
    if (state.categoryFilter !== "all" && incident.category !== state.categoryFilter) return false;
    if (state.severityFilter !== "all" && incident.severity !== state.severityFilter) return false;
    if (!query) return true;
    const searchable = [
      incident.id,
      incident.title,
      incident.site,
      incident.summary,
      ...(incident.devices || []).flatMap((device) => [
        device.sn,
        device.name,
        device.rack_position,
        device.ip,
      ]),
    ].join(" ").toLowerCase();
    return searchable.includes(query);
  });
}

function renderCounts() {
  const counts = { active: 0, new: 0, processing: 0, waiting_human: 0, resolved: 0, simulation: 0 };
  state.incidents.forEach((incident) => {
    const lane = incidentLane(incident);
    counts[lane] += 1;
    if (["new", "processing", "waiting_human"].includes(lane)) counts.active += 1;
  });
  document.querySelector("#countActive").textContent = counts.active;
  document.querySelector("#countNew").textContent = counts.new;
  document.querySelector("#countProcessing").textContent = counts.processing;
  document.querySelector("#countWaiting").textContent = counts.waiting_human;
  document.querySelector("#countResolved").textContent = counts.resolved;
  document.querySelector("#countSimulation").textContent = counts.simulation;
}

function renderList() {
  const items = filteredIncidents();
  document.querySelector("#queueResultCount").textContent = `${items.length} 条`;
  if (!items.length) {
    const message = state.filter === "simulation"
      ? "当前没有模拟事件。可以从故障演练或模拟案例开始一次测试。"
      : "当前筛选下没有真实故障。可以清除筛选或分析新的故障信息。";
    listEl.innerHTML = `<div class="list-empty"><strong>没有符合条件的事件</strong><span>${escapeHtml(message)}</span><button class="text-button" data-action="clear-incident-filters" type="button">清除筛选</button></div>`;
    return;
  }
  listEl.innerHTML = items.map((incident) => {
    const device = incident.devices?.[0] || {};
    const position = device.rack_position || "位置待补充";
    const lane = incidentLane(incident);
    const simulation = lane === "simulation";
    return `
      <button class="incident-row ${incident.id === state.selectedId ? "is-selected" : ""}"
        data-incident-id="${escapeHtml(incident.id)}" data-severity="${escapeHtml(incident.severity)}" data-lane="${escapeHtml(lane)}" type="button"
        aria-label="打开故障 ${escapeHtml(incident.title)}">
        <span class="incident-state-track" aria-hidden="true"><i></i><i></i><i></i></span>
        <div class="row-incident">
          <div class="row-meta"><span class="row-id">${escapeHtml(incident.id)}</span>${simulation ? `<span class="row-simulation">模拟</span>` : ""}</div>
          <h3>${escapeHtml(incident.title)}</h3>
          <p>${escapeHtml(incident.summary || "尚未形成摘要")}</p>
        </div>
        <div class="row-field row-device"><small>设备定位</small><strong>${escapeHtml(primaryIdentity(incident))}</strong><span>${escapeHtml(position)}</span></div>
        <div class="row-field row-owner"><small>责任专业</small><strong>${escapeHtml(translated("category", incident.category))}</strong><span>${escapeHtml(incident.site || "机房待确认")} · ${incident.affected_count || 0} 台</span></div>
        <div class="row-field row-next"><small>下一步</small><strong>${escapeHtml(incidentNextAction(incident))}</strong><span>${escapeHtml(lane === "waiting_human" ? "等待人工" : translated("status", incident.status))}</span></div>
        <div class="row-updated">
          <time>${escapeHtml(formatTime(incident.updated_at))}</time>
          <span class="row-status">${escapeHtml({ active: "进行中", new: "待处理", processing: "调查中", waiting_human: "等人工", resolved: "已恢复", simulation: "模拟" }[lane] || translated("status", incident.status))}</span>
          <b aria-hidden="true">→</b>
        </div>
      </button>`;
  }).join("");
  window.requestAnimationFrame(() => { listEl.scrollTop = state.listScrollTop; });
}

function identityStrip(device, incident = null) {
  return `
    <div class="identity-strip">
      <div class="identity-field"><small>完整 SN</small><strong>${escapeHtml(device.sn || "待补充")}</strong></div>
      <div class="identity-field"><small>机架位</small><strong>${escapeHtml(device.rack_position || "待补充")}</strong></div>
      <div class="identity-field"><small>设备名</small><strong>${escapeHtml(device.name || "待补充")}</strong></div>
      ${incident ? `<div class="identity-field"><small>责任专业</small><strong>${escapeHtml(translated("category", incident.category))}</strong></div>
      <div class="identity-field"><small>当前状态</small><strong>${escapeHtml(incidentLane(incident) === "waiting_human" ? "等待人工" : translated("status", incident.status))}</strong></div>` : ""}
    </div>`;
}

function statusLabel(value) {
  return {
    confirmed: "已确认",
    high_likelihood: "较大可能",
    candidate: "调查候选",
    weakened: "已削弱",
    rejected: "已排除",
    insufficient: "证据不足",
  }[value] || value || "证据不足";
}

function modeLabel(value) {
  return {
    rules_only: "规则＋知识",
    ai_enriched: "大模型增强",
    tool_assisted: "工具验证",
    legacy_untraced: "旧版事件",
  }[value] || value || "未知模式";
}

function connectionLabel(value) {
  return {
    available: "可使用",
    connected: "已连接",
    configured: "等待检查",
    not_configured: "尚未连接",
    failed: "连接失败",
    planned: "等待客户接口",
    completed: "已完成",
    skipped: "未执行",
  }[value] || value || "状态未知";
}

function renderSources(items = []) {
  const target = document.querySelector("#sourceGrid");
  if (!items.length) {
    target.innerHTML = `<div class="analysis-warning">还没有读取到数据来源状态。</div>`;
    return;
  }
  target.innerHTML = items.map((item) => `
    <article class="source-card" data-state="${escapeHtml(item.state)}">
      <header>
        <span class="source-light" aria-hidden="true"></span>
        <div><h3>${escapeHtml(item.name)}</h3><small>${escapeHtml(item.automatic ? "系统自动" : "人工提供")}</small></div>
        <strong>${escapeHtml(connectionLabel(item.state))}</strong>
      </header>
      <p>${escapeHtml(item.role)}</p>
      <div class="source-message">${escapeHtml(item.message)}</div>
      <footer><span>${item.read_only ? "只读" : "可能写入"}</span><time>${escapeHtml(formatTime(item.checked_at))}</time></footer>
    </article>`).join("");
}

async function loadSources(checkExternal = false) {
  const payload = await api(`/api/sources?check=${checkExternal ? "1" : "0"}`);
  state.sources = payload.items || [];
  renderSources(state.sources);
  return state.sources;
}

function renderFacilities(items = []) {
  const target = document.querySelector("#facilityGrid");
  if (!items.length) {
    target.innerHTML = `<div class="facility-empty">还没有本地机房等级。未标注的事件会显示“未确认”，系统不会根据机房名称猜测。</div>`;
    return;
  }
  target.innerHTML = items.map((item) => `
    <article class="facility-profile" data-criticality="${escapeHtml(item.criticality)}">
      <div>
        <small>${escapeHtml(item.source === "local_config" ? "本地标注" : item.source || "来源未知")}</small>
        <h3>${escapeHtml(item.site)}</h3>
        <p>${escapeHtml(item.display_name || item.site)}</p>
      </div>
      <strong>${escapeHtml(translated("facility", item.criticality))}</strong>
      <time>更新于 ${escapeHtml(formatTime(item.updated_at))}</time>
    </article>`).join("");
}

async function loadFacilities() {
  const payload = await api("/api/facilities");
  state.facilities = payload.items || [];
  renderFacilities(state.facilities);
  return state.facilities;
}

async function saveFacility(form) {
  const button = form.querySelector('button[type="submit"]');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const payload = Object.fromEntries(new FormData(form).entries());
    await api("/api/facilities", { method: "POST", body: JSON.stringify(payload) });
    showToast(`${String(payload.site || "").toUpperCase()} 的机房等级已保存`);
    form.reset();
    await loadFacilities();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function listLines(items = [], empty = "暂无") {
  if (!items.length) return `<p class="muted-empty">${escapeHtml(empty)}</p>`;
  return `<ul class="plain-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function hypothesisList(hypotheses = []) {
  if (!hypotheses.length) return `<p class="list-empty">没有可审计的候选。请补充新的日志或检查结果。</p>`;
  return `<div class="hypothesis-list">${hypotheses.map((hypothesis) => `
    <article class="hypothesis-card" data-status="${escapeHtml(hypothesis.status)}">
      <header>
        <div><span class="hypothesis-id">${escapeHtml(hypothesis.id)}</span><h4>${escapeHtml(hypothesis.title)}</h4></div>
        <span class="grade-chip" data-grade="${escapeHtml(hypothesis.status)}">${escapeHtml(statusLabel(hypothesis.status))}</span>
      </header>
      <p class="candidate-meta">${escapeHtml(hypothesis.basis || "尚未说明形成依据")}</p>
      <dl class="evidence-dl">
        <div><dt>支持证据</dt><dd>${escapeHtml((hypothesis.supporting_evidence_ids || []).join("、") || "无直接证据")}</dd></div>
        <div><dt>需要补充</dt><dd>${escapeHtml((hypothesis.missing_evidence || []).join("；") || "暂无")}</dd></div>
        <div><dt>会削弱它的情况</dt><dd>${escapeHtml((hypothesis.known_counter_conditions || []).join("；") || "尚未列出")}</dd></div>
        <div><dt>产生方式</dt><dd>${escapeHtml(hypothesis.generated_by === "model_enhanced" ? "大模型增强（已校验证据引用）" : hypothesis.knowledge_card_id ? `知识卡 ${hypothesis.knowledge_card_id} · ${hypothesis.knowledge_card_version}` : "规则降级候选")}</dd></div>
      </dl>
    </article>`).join("")}</div>`;
}

function evidenceList(evidence = []) {
  if (!evidence.length) return `<p class="list-empty">当前没有证据。</p>`;
  return `<ol class="evidence-list">${evidence.map((item) => `
    <li class="evidence-item">
      <span class="evidence-id">${escapeHtml(item.id)}</span>
      <p class="evidence-text">${escapeHtml(item.text)}</p>
    </li>`).join("")}</ol>`;
}

function provenanceTable(fields = []) {
  if (!fields.length) return `<p class="muted-empty">没有字段溯源记录。</p>`;
  return `<div class="provenance-table" role="table" aria-label="字段来源">
    <div class="provenance-row provenance-head" role="row"><span>字段</span><span>当前值</span><span>怎么得到</span><span>是否核验</span></div>
    ${fields.map((field) => `<div class="provenance-row" role="row">
      <span>${escapeHtml(field.label)}</span>
      <code>${escapeHtml(field.value || "未知")}</code>
      <span>${escapeHtml(field.method === "provided" ? field.source_label : "输入未提供")}</span>
      <span class="provenance-state" data-reliability="${escapeHtml(field.reliability)}">${escapeHtml(field.verification)}</span>
    </div>`).join("")}
  </div>`;
}

function factTable(facts = []) {
  if (!facts.length) return `<div class="analysis-warning">没有从当前原文提取到结构化异常事实。系统保留了原文，但不会为了给出答案而补造事实。</div>`;
  return `<div class="fact-grid">${facts.map((fact) => `
    <article class="fact-card">
      <span class="fact-type">${escapeHtml(fact.type)}</span>
      <strong>${escapeHtml(fact.label)}</strong>
      <code>${escapeHtml(fact.value)}${escapeHtml(fact.unit || "")}</code>
      <p>${escapeHtml(fact.excerpt)}</p>
      <small>证据 ${escapeHtml((fact.evidence_ids || []).join("、"))} · ${escapeHtml(fact.parser)}</small>
    </article>`).join("")}</div>`;
}

function ruleAndKnowledge(investigation) {
  const rules = investigation.rule_matches || [];
  const cards = investigation.knowledge_retrieval?.cards || [];
  return `<div class="matched-columns">
    <div><h4>命中规则</h4>${rules.length ? rules.map((rule) => `
      <article class="match-card">
        <span class="match-id">${escapeHtml(rule.id)}</span><strong>${escapeHtml(rule.title)}</strong>
        <p>范围：${escapeHtml(rule.scope)}</p><p class="limitation">能力边界：${escapeHtml(rule.limitation)}</p>
      </article>`).join("") : `<p class="muted-empty">没有规则命中。</p>`}</div>
    <div><h4>召回知识卡</h4>${cards.length ? cards.map((card) => `
      <article class="match-card knowledge-card">
        <span class="match-id">${escapeHtml(card.id)} · v${escapeHtml(card.version)}</span><strong>${escapeHtml(card.title)}</strong>
        <p>召回原因：${escapeHtml((card.retrieval_reasons || []).join("；"))}</p>
        <p class="limitation">禁止推断：${escapeHtml((card.prohibited_inferences || []).join("；"))}</p>
        <div class="source-links">${(card.sources || []).map((source) => `<a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.title)} · ${escapeHtml(source.version)}</a>`).join("")}</div>
      </article>`).join("") : `<p class="muted-empty">知识覆盖不足，系统不会用常识补答案。</p>`}</div>
  </div>`;
}

function correlationPanel(correlation = {}) {
  return `<div class="correlation-panel" data-level="${escapeHtml(correlation.level)}">
    <div><small>关联等级</small><strong>${escapeHtml({ explicit: "外部明确指定", deterministic: "确定性设备关联", possible: "可能相关", none: "未关联", unknown: "历史未知" }[correlation.level] || correlation.level)}</strong></div>
    <div><small>关联键</small><code>${escapeHtml(correlation.key || "无")}</code></div>
    <p>${escapeHtml(correlation.reason || "没有关联说明")}</p>
    ${listLines(correlation.limitations || [], "没有额外限制")}
  </div>`;
}

function verificationPlan(items = []) {
  if (!items.length) return `<p class="muted-empty">尚未形成下一步验证。</p>`;
  return `<ol class="verification-list">${items.map((item) => `
    <li>
      <span class="verification-priority">${String(item.priority || 0).padStart(2, "0")}</span>
      <div>
        <h4>${escapeHtml(item.method)}</h4>
        <p>${escapeHtml(item.purpose)}</p>
        <div class="verification-meta"><span>风险：只读</span><span>工具：${item.tool === "not_connected" ? "未接入，待人工/外部执行" : escapeHtml(item.tool)}</span></div>
        <details><summary>这一步怎样改变判断</summary>${listLines(item.expected_effects || [], "等待检查结果")}</details>
      </div>
    </li>`).join("")}</ol>`;
}

function intakeList(intake = []) {
  if (!intake.length) return `<p class="muted-empty">没有可审计的原始输入。</p>`;
  return `<div class="intake-list">${intake.map((item) => `
    <details class="intake-card">
      <summary><span class="intake-summary"><strong>${escapeHtml(item.source_label)}</strong><small>${escapeHtml(item.summary)}</small></span><time>${escapeHtml(formatTime(item.event_time))}</time>${item.simulation ? `<b>模拟数据</b>` : ""}</summary>
      <div class="intake-body"><p>${escapeHtml(item.summary)}</p><pre>${escapeHtml(item.raw_text)}</pre><small>输入编号 ${escapeHtml(item.id)} · 类型 ${escapeHtml(item.source_kind)}</small></div>
    </details>`).join("")}</div>`;
}

function traceContent(stage, investigation) {
  if (stage === "received") return intakeList(investigation.intake);
  if (stage === "normalized") return provenanceTable(investigation.field_provenance);
  if (stage === "extracted") return factTable(investigation.extracted_facts);
  if (stage === "matched") return ruleAndKnowledge(investigation);
  if (stage === "correlated") return correlationPanel(investigation.correlation);
  if (stage === "hypothesized") return hypothesisList(investigation.hypotheses);
  if (stage === "model_enriched") return `<div class="model-observation"><p>${escapeHtml(investigation.model_observation?.impact_summary || "模型没有新增影响摘要")}</p>${listLines(investigation.model_observation?.limitations || [])}</div>`;
  if (stage === "external_telemetry") return externalChecksPanel(investigation.external_checks, "signoz");
  if (stage === "holmes_investigation") return externalChecksPanel(investigation.external_checks, "holmes");
  if (stage === "planned") return verificationPlan(investigation.verification_plan);
  return `<p class="muted-empty">该步骤没有详细输出。</p>`;
}

function externalChecksPanel(checks = [], provider) {
  const item = (checks || []).find((check) => check.provider === provider);
  if (!item) return `<p class="muted-empty">本事件还没有执行这一步。</p>`;
  const records = item.records || [];
  const metrics = item.metrics || [];
  const calls = item.tool_calls || [];
  return `<div class="external-check" data-state="${escapeHtml(item.state)}">
    <header><strong>${escapeHtml(provider === "signoz" ? "SigNoz 监控查询" : "HolmesGPT AI 调查")}</strong><span>${escapeHtml(connectionLabel(item.state))}</span></header>
    <p>${escapeHtml(item.message || "没有状态说明")}</p>
    ${item.analysis ? `<div class="tool-analysis"><small>AI 返回内容（仍需证据校验）</small><p>${escapeHtml(item.analysis)}</p></div>` : ""}
    ${records.length ? `<details><summary>查看查询到的 ${records.length} 条日志</summary><ol class="tool-records">${records.map((record) => `<li><code>${escapeHtml(record.text || "空记录")}</code></li>`).join("")}</ol></details>` : ""}
    ${metrics.length ? `<details><summary>查看 ${metrics.length} 组主机指标结果</summary><ol class="tool-records">${metrics.map((record) => `<li><code>${escapeHtml(record.text || "空记录")}</code></li>`).join("")}</ol></details>` : ""}
    <div class="tool-calls"><small>只读工具记录</small>${calls.length ? calls.map((call) => `<div><code>${escapeHtml(call.tool || "只读工具")}</code><span>${escapeHtml(call.description || "已执行只读查询")}</span></div>`).join("") : `<p>没有工具调用记录。</p>`}</div>
  </div>`;
}

function dataPath(investigation = {}, incident = {}) {
  const checks = investigation.external_checks || [];
  const signoz = checks.find((item) => item.provider === "signoz");
  const holmes = checks.find((item) => item.provider === "holmes");
  const sourceText = investigation.simulation
    ? "模拟案例进入系统"
    : investigation.intake?.[0]?.source_label || "收到真实故障信息";
  const stateFor = (item) => {
    if (!item) return "idle";
    if (item.state === "completed") return "done";
    if (item.state === "failed") return "failed";
    return "waiting";
  };
  return `<section class="signal-route" aria-label="本事件的数据路径">
    <div class="signal-route-head">
      <div><p class="eyebrow">DATA ROUTE</p><h3>这次数据从哪来，又经过了什么</h3></div>
      ${investigation.simulation ? `<span class="simulation-badge">模拟数据不会查询真实设备</span>` : `<button class="secondary-button compact-button" data-action="run-investigation" type="button">查询监控并让 AI 补充调查</button>`}
    </div>
    <ol class="signal-bus">
      <li data-state="done"><span>01</span><div><strong>收到故障</strong><small>${escapeHtml(sourceText)}</small></div></li>
      <li data-state="${escapeHtml(stateFor(signoz))}"><span>02</span><div><strong>查询真实监控</strong><small>${escapeHtml(signoz?.message || "尚未查询 SigNoz")}</small></div></li>
      <li data-state="done"><span>03</span><div><strong>规则与经验判断</strong><small>提取事实、召回知识卡、保留竞争候选</small></div></li>
      <li data-state="${escapeHtml(stateFor(holmes))}"><span>04</span><div><strong>AI 工具调查</strong><small>${escapeHtml(holmes?.message || "尚未调用 HolmesGPT")}</small></div></li>
      <li data-state="${incident.status === "resolved" ? "done" : "waiting"}"><span>05</span><div><strong>人工确认与处置</strong><small>${incident.status === "resolved" ? "事件已标记解决" : "等待验证结果或现场操作"}</small></div></li>
    </ol>
  </section>`;
}

function traceLine(investigation) {
  const trace = investigation.trace || [];
  if (!trace.length) return `<div class="analysis-warning">${escapeHtml(investigation.capability_notice || "没有可审计调查轨迹")}</div>`;
  return `<div class="evidence-line">${trace.map((node, index) => `
    <details class="evidence-node" data-state="${escapeHtml(node.state)}" ${index === 0 || node.stage === "extracted" ? "open" : ""}>
      <summary>
        <span class="node-marker" aria-hidden="true"></span>
        <span class="node-order">${String(index + 1).padStart(2, "0")}</span>
        <span class="node-title"><strong>${escapeHtml(node.title)}</strong><small>${escapeHtml(node.summary)}</small></span>
        <span class="node-state">${escapeHtml({ confirmed: "已保存", reported: "外部提供", inferred: "系统推断", waiting: "等待验证" }[node.state] || node.state)}</span>
      </summary>
      <div class="node-detail">
        ${traceContent(node.stage, investigation)}
        <p class="node-limitation">本步限制：${escapeHtml(node.limitation)}</p>
      </div>
    </details>`).join("")}</div>`;
}

function onsiteCard(card = {}) {
  if (!card.required) {
    return `
      <section class="section-block">
        <div class="section-heading"><h3>现场处置</h3><small>当前未判定必须到场</small></div>
        <p class="incident-summary">先由接口团队根据日志和运行状态远程确认；需要物理操作时再转现场。</p>
      </section>`;
  }
  const device = card.device || {};
  const power = card.power || { gate: "confirm", message: "操作权限待确认" };
  const actions = card.actions || [];
  const missing = card.missing_information || [];
  const gateTitle = power.gate === "stop" ? "停止操作" : power.gate === "ready" ? "可进入核对" : "先联系接口人确认";
  return `
    <section class="section-block onsite-card">
      <div class="section-heading"><h3>现场处置卡</h3><small>人机协同 · 不自动执行</small></div>
      <div class="operation-gate" data-gate="${escapeHtml(power.gate)}">
        <strong>${gateTitle}</strong><p>${escapeHtml(power.message)}</p>
      </div>
      <div class="field-matrix">
        <div class="matrix-cell"><small>完整 SN</small><strong>${escapeHtml(device.sn || "待补充")}</strong></div>
        <div class="matrix-cell"><small>机架位</small><strong>${escapeHtml(device.rack_position || "待补充")}</strong></div>
        <div class="matrix-cell"><small>UID 状态</small><strong>${escapeHtml(translated("unknown", card.uid_status))}</strong></div>
        <div class="matrix-cell"><small>从重装中发起</small><strong>${escapeHtml(translated("unknown", card.from_reinstall))}</strong></div>
        <div class="matrix-cell"><small>接口团队</small><strong>${escapeHtml(card.interface_team || "待补充")}</strong></div>
        <div class="matrix-cell"><small>接口人</small><strong>${escapeHtml(card.interface_person || "待补充")}</strong></div>
      </div>
      ${missing.length ? `<h3>操作前缺失</h3><ul class="missing-list">${missing.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
      <h3>建议核查</h3>
      <ul class="action-list">${actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      <p class="candidate-meta">停止条件：${escapeHtml(card.stop_condition)}</p>
    </section>`;
}

function facilityAssessmentCard(assessment = {}) {
  if (!assessment.decision) return "";
  const facility = assessment.facility || {};
  const event = assessment.event || {};
  const evidence = assessment.evidence || [];
  const missing = assessment.missing_evidence || [];
  const decisionHint = {
    required: "命中确定性规则：立即提醒现场按既有流程拨打 CC；后续通报流程不由 AI 接管。",
    needs_confirmation: "证据或资产等级不足：先确认缺失项，不把高风险事件误报成已确定 CC。",
    not_required: "当前不触发 CC：仍按普通故障流程联系接口人、系统组、网络组或动环处理。",
  }[assessment.decision] || "等待确认处理方式。";
  return `
    <section class="facility-assessment" data-decision="${escapeHtml(assessment.decision)}">
      <header class="facility-assessment-head">
        <div><p class="eyebrow">FACILITY &amp; CC MATRIX</p><h3>机房等级、影响范围与 CC 判断</h3></div>
        <strong>${escapeHtml(translated("ccDecision", assessment.decision))}</strong>
      </header>
      <div class="assessment-matrix">
        <div><small>机房等级</small><strong>${escapeHtml(translated("facility", facility.criticality))}</strong><span>${escapeHtml(facility.source === "local_config" ? "来自本地资产标注" : facility.source === "event_input" ? "来自本次输入" : facility.source === "conflict" ? `来源冲突：输入${translated("facility", facility.reported_criticality)}，档案${translated("facility", facility.stored_criticality)}` : "没有提供，不做猜测")}</span></div>
        <div><small>事件类型</small><strong>${escapeHtml(translated("facilityEvent", event.subtype))}</strong><span>${escapeHtml(event.category || "general")}</span></div>
        <div><small>影响程度</small><strong>${escapeHtml(translated("impact", event.impact_level))}</strong><span>核心设备：${escapeHtml(translated("facility", event.asset_criticality))}</span></div>
        <div><small>命中规则</small><strong>${escapeHtml(assessment.matched_rule_id || "未命中")}</strong><span>${escapeHtml(assessment.rule_version || "版本未知")}</span></div>
      </div>
      <div class="assessment-reason"><strong>为什么这样判断</strong><p>${escapeHtml(assessment.reason)}</p><small>${escapeHtml(decisionHint)}</small></div>
      ${evidence.length ? `<details><summary>查看本判断使用的输入证据</summary>${listLines(evidence.map((item) => `${item.source || "输入"}：${item.text || ""}`))}</details>` : ""}
      ${missing.length ? `<div class="assessment-missing"><strong>还缺什么</strong>${listLines(missing)}</div>` : ""}
    </section>`;
}

function defaultDetailTabForRole(role) {
  if (["ai_admin", "super_admin"].includes(role)) return "evidence";
  if (role === "facility_lead") return "collaboration";
  return "overview";
}

function compactEvidencePanel(investigation = {}) {
  const evidence = (investigation.evidence || []).slice(0, 3);
  const total = (investigation.evidence || []).length;
  return `<section class="workbench-card evidence-glance">
    <header><div><small>关键证据</small><strong>${escapeHtml(total)} 条已登记</strong></div><button class="text-button" data-detail-tab="evidence" type="button">查看全部</button></header>
    <ol>${evidence.map((item, index) => `<li><span>${String(index + 1).padStart(2, "0")}</span><p>${escapeHtml(item.text)}</p></li>`).join("") || `<li class="is-empty"><p>当前还没有可核对证据，不能给出确定结论。</p></li>`}</ol>
  </section>`;
}

function compactCollaborationPanel(incident = {}) {
  const card = incident.onsite_card || {};
  const device = card.device || incident.devices?.[0] || {};
  const identityReady = Boolean(device.sn && device.rack_position);
  const gate = card.power?.gate || "confirm";
  const gateText = gate === "ready" ? "操作许可已满足" : gate === "stop" ? "当前禁止操作" : "操作许可待确认";
  const gateState = gate === "ready" ? "done" : "waiting";
  return `<section class="workbench-card collaboration-glance">
    <header><div><small>协同与责任</small><strong>${escapeHtml(incidentLane(incident) === "waiting_human" ? "等待人工反馈" : "按当前阶段推进")}</strong></div><button class="text-button" data-detail-tab="collaboration" type="button">查看沟通</button></header>
    <ol>
      <li data-state="${identityReady ? "done" : "waiting"}"><i></i><span>${identityReady ? "完整 SN 与机架位已具备" : "设备身份仍需补齐"}</span></li>
      <li data-state="${gateState}"><i></i><span>${escapeHtml(gateText)}</span></li>
      <li data-state="${incident.status === "resolved" ? "done" : "waiting"}"><i></i><span>${incident.status === "resolved" ? "恢复结果已记录" : "等待处置结果与恢复验证"}</span></li>
    </ol>
  </section>`;
}

function incidentStatusTrack(incident) {
  const lane = incidentLane(incident);
  const current = { new: 0, processing: 1, waiting_human: 2, resolved: 3, simulation: incident.status === "resolved" ? 3 : 1 }[lane] ?? 0;
  const steps = [
    ["已接收", "原始信号已保存"],
    ["调查", "规则、知识与只读查询"],
    ["人工", "许可、复核或现场操作"],
    ["恢复", "监控、业务与人工确认"],
  ];
  return `<ol class="incident-status-track" aria-label="故障处理进度">${steps.map(([label, note], index) => {
    const stepState = index < current ? "done" : index === current ? "current" : "upcoming";
    return `<li data-state="${stepState}"><span>${String(index + 1).padStart(2, "0")}</span><div><strong>${label}</strong><small>${note}</small></div></li>`;
  }).join("")}</ol>`;
}

function renderDetail(incident) {
  if (!incident) return;
  const investigation = incident.investigation || {};
  const conclusion = investigation.conclusion || {};
  const device = incident.devices?.[0] || {};
  const moreDevices = (incident.devices || []).slice(1);
  const activeTab = ["overview", "evidence", "collaboration", "raw", "audit"].includes(state.detailTab)
    ? state.detailTab
    : defaultDetailTabForRole(state.role);
  state.detailTab = activeTab;
  document.querySelector("#workspaceKicker").textContent = incident.id;
  document.querySelector("#workspaceTitle").textContent = `${incident.site || "机房待确认"} · ${incident.title}`;
  detailEl.innerHTML = `
    <article class="incident-layout">
      <div class="incident-fixed-summary">
        <header class="incident-heading">
          <div>
            <p class="eyebrow">AUDITABLE INCIDENT</p>
            <h2>${escapeHtml(incident.title)}</h2>
            <p class="incident-summary">${escapeHtml(incident.summary)}</p>
          </div>
          <div class="incident-tags">
            ${isSimulationIncident(incident) ? `<span class="simulation-badge">模拟事件</span>` : ""}
            <span class="severity-chip" data-value="${escapeHtml(incident.severity)}">${escapeHtml(translated("severity", incident.severity))}</span>
            <span class="category-chip">${escapeHtml(translated("category", incident.category))}</span>
            <span class="status-chip">${escapeHtml(translated("status", incident.status))}</span>
          </div>
        </header>

        ${identityStrip(device, incident)}
        <div class="incident-workbench-grid">
          <section class="workbench-card judgment-glance" data-grade="${escapeHtml(conclusion.grade)}">
            <header><div><small>AI 当前判断</small><strong>${escapeHtml(statusLabel(conclusion.grade))}</strong></div><span>${escapeHtml(modeLabel(investigation.mode))}</span></header>
            <h3>${escapeHtml(conclusion.leading_hypothesis || "证据不足")}</h3>
            <p>${escapeHtml(conclusion.uncertainty || "尚未说明不确定性")}</p>
            <div class="field-next-action"><small>现场下一步</small><strong>${escapeHtml(conclusion.next_step || incidentNextAction(incident))}</strong><span>只读优先；高风险动作必须由人确认</span></div>
          </section>
          ${compactEvidencePanel(investigation)}
          ${compactCollaborationPanel(incident)}
        </div>
      </div>

      <nav class="detail-tabs" role="tablist" aria-label="故障详情分区">
        <button role="tab" data-detail-tab="overview" aria-selected="${activeTab === "overview"}" class="${activeTab === "overview" ? "is-active" : ""}" type="button">处置步骤</button>
        <button role="tab" data-detail-tab="evidence" aria-selected="${activeTab === "evidence"}" class="${activeTab === "evidence" ? "is-active" : ""}" type="button">AI 分析与证据</button>
        <button role="tab" data-detail-tab="collaboration" aria-selected="${activeTab === "collaboration"}" class="${activeTab === "collaboration" ? "is-active" : ""}" type="button">沟通记录</button>
        <button role="tab" data-detail-tab="raw" aria-selected="${activeTab === "raw"}" class="${activeTab === "raw" ? "is-active" : ""}" type="button">原始日志</button>
        <button role="tab" data-detail-tab="audit" aria-selected="${activeTab === "audit"}" class="${activeTab === "audit" ? "is-active" : ""}" type="button">审计记录</button>
      </nav>

      <div class="detail-tab-scroll">
        <section class="detail-panel" role="tabpanel" data-detail-panel="overview" ${activeTab === "overview" ? "" : "hidden"}>
          ${moreDevices.length ? `<p class="multi-device-note">事件还关联 ${moreDevices.length} 台设备：${moreDevices.map((item) => escapeHtml(item.sn || item.name || item.rack_position)).join("、")}。关联不等于已经证明共同根因。</p>` : ""}
          <div class="collaboration-grid">
            <div class="detail-column">${onsiteCard(incident.onsite_card)}</div>
            <div class="detail-column">
              ${incidentStatusTrack(incident)}
              <section class="section-block">
                <div class="section-heading"><h3>事件处理状态</h3><small>${escapeHtml(formatTime(incident.updated_at))}</small></div>
                <div class="status-actions">
                  <button class="secondary-button" data-status="new" ${incident.status === "new" ? "disabled" : ""} type="button">退回待处理</button>
                  <button class="secondary-button" data-status="processing" ${incident.status === "processing" ? "disabled" : ""} type="button">开始处理</button>
                  <button class="primary-button" data-status="resolved" ${incident.status === "resolved" ? "disabled" : ""} type="button">标记已恢复</button>
                </div>
              </section>
              <section class="section-block operation-entry-card">
                <div class="section-heading"><h3>现场操作闭环</h3><small>OMS 工单与双岗/远程复核</small></div>
                <button class="primary-button" data-action="open-operations" type="button">进入现场操作</button>
              </section>
            </div>
          </div>
          ${facilityAssessmentCard(incident.analysis?.facility_assessment)}
          ${incident.cc_reminder?.required ? `
            <aside class="cc-alert" role="alert">
              <span class="cc-mark">CC</span>
              <div><strong>${escapeHtml(incident.cc_reminder.message)}</strong><p>${escapeHtml(incident.cc_reminder.reason || "输入已明确标记需要通报")}</p></div>
            </aside>` : ""}
        </section>

        <section class="detail-panel" role="tabpanel" data-detail-panel="evidence" ${activeTab === "evidence" ? "" : "hidden"}>
          <section class="investigation-section">
            <div class="section-heading investigation-heading"><div><p class="eyebrow">EVIDENCE ROUTE</p><h3>系统如何一步步得到当前判断</h3></div><small>展开步骤查看原文、依据、工具结果和限制</small></div>
            ${traceLine(investigation)}
          </section>
          <section class="section-block evidence-register">
            <div class="section-heading"><h3>证据登记</h3><small>${(investigation.evidence || []).length} 条</small></div>
            ${evidenceList(investigation.evidence)}
          </section>
        </section>

        <section class="detail-panel" role="tabpanel" data-detail-panel="collaboration" ${activeTab === "collaboration" ? "" : "hidden"}>
          <section class="section-block">
            <div class="section-heading"><h3>接口沟通摘要</h3><small>只使用已保存结果</small></div>
            <div class="communication-box" id="communicationText">${escapeHtml(incident.communication_text)}</div>
            <div class="copy-row"><button class="secondary-button" data-action="copy-summary" type="button">复制沟通摘要</button></div>
          </section>
        </section>

        <section class="detail-panel" role="tabpanel" data-detail-panel="raw" ${activeTab === "raw" ? "" : "hidden"}>
          <section class="section-block raw-records-panel">
            <div class="section-heading"><div><p class="eyebrow">SOURCE RECORDS</p><h3>原始日志与调查输入</h3></div><small>${(investigation.intake || []).length} 次输入</small></div>
            <p class="raw-records-note">默认展示来源、时间和摘要；需要核对时再展开原文。原始记录不会因为结论变化而被覆盖。</p>
            ${intakeList(investigation.intake)}
          </section>
        </section>

        <section class="detail-panel" role="tabpanel" data-detail-panel="audit" ${activeTab === "audit" ? "" : "hidden"}>
          ${dataPath(investigation, incident)}
          <aside class="capability-banner" data-mode="${escapeHtml(investigation.mode)}">
            <div class="capability-mode"><span>当前分析模式</span><strong>${escapeHtml(modeLabel(investigation.mode))}</strong></div>
            <p>${escapeHtml(investigation.capability_notice || "当前能力状态未知")}</p>
            ${investigation.simulation ? `<span class="simulation-badge">模拟数据 · 不代表真实设备状态</span>` : `<span class="live-input-badge">外部/人工输入 · 尚未独立核验</span>`}
          </aside>
        </section>
      </div>
    </article>`;
}

function activateDetailTab(name, focus = false) {
  if (!["overview", "evidence", "collaboration", "raw", "audit"].includes(name)) return;
  state.detailTab = name;
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    const active = button.dataset.detailTab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    if (active && focus) button.focus();
  });
  document.querySelectorAll("[data-detail-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.detailPanel !== name;
  });
  document.querySelector(".detail-tab-scroll")?.scrollTo({ top: 0, behavior: "auto" });
}

function historyUrlFor(view, incidentId = null) {
  if (view === "detail" && incidentId) return `#incident=${encodeURIComponent(incidentId)}`;
  return "#incidents";
}

function showIncidentList(pushHistory = true) {
  state.view = "list";
  document.body.dataset.incidentView = "list";
  document.querySelector("#incidentListView").hidden = false;
  document.querySelector("#incidentDetailView").hidden = true;
  document.querySelectorAll('.rail-button[data-action="show-incidents"]').forEach((button) => button.classList.add("is-active"));
  if (pushHistory && window.location.hash !== "#incidents") {
    window.history.pushState({ view: "list" }, "", historyUrlFor("list"));
  }
  window.requestAnimationFrame(() => {
    listEl.scrollTop = state.listScrollTop;
    if (state.selectedId) {
      document.querySelector(`[data-incident-id="${CSS.escape(state.selectedId)}"]`)?.focus({ preventScroll: true });
    }
  });
}

function showIncidentDetail(pushHistory = true) {
  if (!state.selectedId) return;
  state.view = "detail";
  document.body.dataset.incidentView = "detail";
  document.querySelector("#incidentListView").hidden = true;
  document.querySelector("#incidentDetailView").hidden = false;
  if (pushHistory && window.location.hash !== historyUrlFor("detail", state.selectedId)) {
    window.history.pushState({ view: "detail", incidentId: state.selectedId }, "", historyUrlFor("detail", state.selectedId));
  }
  window.requestAnimationFrame(() => document.querySelector(".back-list-button")?.focus({ preventScroll: true }));
}

async function loadIncidents(preferredId = null, pushSelectionHistory = true) {
  try {
    const payload = await api("/api/incidents");
    state.incidents = payload.items || [];
    renderCounts();
    renderList();
    const targetId = preferredId || (state.view === "detail" ? state.selectedId : null);
    if (targetId && state.incidents.some((item) => item.id === targetId)) {
      await selectIncident(targetId, false, pushSelectionHistory);
    } else {
      showIncidentList(false);
    }
  } catch (error) {
    document.querySelector("#systemPulse").classList.add("is-offline");
    showToast(error.message, true);
  }
}

async function selectIncident(incidentId, rerenderList = true, pushHistory = true) {
  try {
    if (state.view === "list") state.listScrollTop = listEl.scrollTop;
    state.selectedId = incidentId;
    state.selected = await api(`/api/incidents/${encodeURIComponent(incidentId)}`);
    if (rerenderList) renderList();
    renderDetail(state.selected);
    showIncidentDetail(pushHistory);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function selectAdjacentIncident(offset) {
  const items = filteredIncidents();
  const currentIndex = items.findIndex((item) => item.id === state.selectedId);
  if (!items.length || currentIndex < 0) {
    showToast("当前筛选中没有可切换的事件", true);
    return;
  }
  const nextIndex = currentIndex + offset;
  if (nextIndex < 0 || nextIndex >= items.length) {
    showToast(offset < 0 ? "已经是当前筛选的第一条" : "已经是当前筛选的最后一条");
    return;
  }
  await selectIncident(items[nextIndex].id, true, true);
}

async function updateStatus(status) {
  if (!state.selectedId) return;
  try {
    const incident = await api(`/api/incidents/${encodeURIComponent(state.selectedId)}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    showToast(`事件已更新为“${translated("status", status)}”`);
    await loadIncidents(incident.id);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function runInvestigation(button) {
  if (!state.selectedId) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在查询真实监控…";
  try {
    const incident = await api(`/api/incidents/${encodeURIComponent(state.selectedId)}/investigate`, {
      method: "POST",
      body: "{}",
    });
    state.selected = incident;
    showToast("本次只读调查已记录，未连接的来源也已如实标出");
    await loadIncidents(incident.id);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function openDialog(dialog) {
  dialog._returnFocus = document.activeElement;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  window.requestAnimationFrame(() => dialog.querySelector("button, input, select, textarea")?.focus());
}

function closeDialog(dialog) {
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
  const target = dialog._returnFocus;
  if (target && typeof target.focus === "function") target.focus();
}

function formObject(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  data.cc_required = form.querySelector('[name="cc_required"]')?.checked || false;
  return data;
}

async function submitForm(form, source) {
  const button = form.querySelector('button[type="submit"]');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在分析…";
  try {
    let payload;
    if (source === "alert") {
      payload = JSON.parse(new FormData(form).get("payload"));
    } else {
      payload = formObject(form);
      payload.device_type ||= "server";
      payload.severity ||= source === "onsite" ? "warning" : "unknown";
      payload.summary ||= source === "onsite" ? "现场发现异常" : "日志中发现异常";
    }
    const incident = await api(`/api/ingest/${source}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (source === "alert") closeDialog(sourceDialog);
    else closeDialog(ingestDialog);
    showToast(`真实故障信息已保存到事件 ${incident.id}`);
    await loadIncidents(incident.id);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function loadDemos() {
  try {
    const payload = await api("/api/demos");
    document.querySelector("#demoGrid").innerHTML = payload.items.map((demo, index) => `
      <article class="demo-card">
        <span class="demo-index">SCENE ${String(index + 1).padStart(2, "0")}</span>
        <h3>${escapeHtml(demo.name)}</h3>
        <p>${escapeHtml(demo.description)}</p>
        <button class="secondary-button" data-demo-id="${escapeHtml(demo.id)}" type="button">运行这个场景</button>
      </article>`).join("");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function runDemo(demoId, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在回放…";
  try {
    const result = await api(`/api/demos/${encodeURIComponent(demoId)}/run`, {
      method: "POST",
      body: "{}",
    });
    closeDialog(demoDialog);
    const incident = result.incidents?.[0];
    showToast("模拟案例已完成分析，并明确标记为模拟数据");
    await loadIncidents(incident?.id);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

const roleNames = {
  onsite_operator: "现场人员",
  facility_lead: "机房组长",
  interface_person: "系统/网络接口人",
  ai_admin: "AI 管理员",
  super_admin: "最高管理员",
};

const roleBriefs = {
  onsite_operator: ["现场作业", "核对完整 SN、机架位与许可", "只看现场需要的信息；高风险动作必须等人工确认。"],
  facility_lead: ["机房协调", "看待处理、复核与升级", "关注人员分配、双岗复核、CC条件和超时。"],
  interface_person: ["系统 / 网络调查", "看证据、候选原因与缺口", "向现场下发清晰检查项，并确认操作许可。"],
  ai_admin: ["AI 运营", "管理知识、提示词与模拟接入", "能调试但默认看脱敏轨迹，不能突破查看原文。"],
  super_admin: ["全局审计", "复核 AI 每轮依据与数据来源", "必要时可填写原因、再次确认后查看原始记录。"],
};

const ragStepNames = {
  raw_input: "保存原始输入",
  facts: "提取可回溯事实",
  retrieval_query: "生成检索条件",
  retrieval_hits: "召回经验知识",
  model_input: "组织模型输入",
  model_output: "接收模型输出",
  validation: "执行硬规则校验",
  final_result: "形成最终结果",
};

function jsonText(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function defaultActorForRole(role) {
  return { onsite_operator: "onsite-a", facility_lead: "lead-a", interface_person: "sim-a", ai_admin: "ai-admin-a", super_admin: "root-auditor" }[role] || "onsite-a";
}

function renderRoleBrief() {
  const [kicker, title, note] = roleBriefs[state.role] || roleBriefs.onsite_operator;
  document.querySelector("#roleBrief").innerHTML = `<small>${escapeHtml(kicker)}</small><strong>${escapeHtml(title)}</strong><span>${escapeHtml(note)}</span>`;
}

function setRole(role, preserveActor = false) {
  state.role = roleNames[role] ? role : "onsite_operator";
  state.detailTab = defaultDetailTabForRole(state.role);
  if (!preserveActor) state.actor = defaultActorForRole(state.role);
  localStorage.setItem("idcai-role", state.role);
  localStorage.setItem("idcai-actor", state.actor);
  document.body.dataset.role = state.role;
  document.querySelector("#roleSelect").value = state.role;
  document.querySelector("#userIdentity").value = state.actor;
  const adminButton = document.querySelector("#adminRailButton");
  const labButton = document.querySelector("#labRailButton");
  const drillButton = document.querySelector("#drillRailButton");
  const isAdmin = ["ai_admin", "super_admin"].includes(state.role);
  adminButton.classList.toggle("is-locked", !isAdmin);
  adminButton.setAttribute("aria-description", isAdmin ? "打开数据与 AI 管理" : "仅 AI 管理员可以打开");
  labButton.classList.toggle("is-locked", !isAdmin);
  labButton.setAttribute("aria-description", isAdmin ? "打开接入实验室" : "仅 AI 管理员与最高管理员可以打开");
  drillButton.hidden = !isAdmin;
  drillButton.setAttribute("aria-description", "打开管理员故障演练台");
  document.querySelector("#breakGlassPanel").hidden = state.role !== "super_admin";
  applyGovernancePermissions();
  renderRoleBrief();
  showToast(`已切换到${roleNames[state.role]}工作台，当前账号 ${state.actor}`);
  if (state.incidents.length) loadIncidents(state.view === "detail" ? state.selectedId : null, false).catch((error) => showToast(error.message, true));
  if (governanceDialog.open) loadGovernance().catch((error) => showToast(error.message, true));
  if (!isAdmin && drillDialog.open) closeDialog(drillDialog);
}

function adminSummaryCard(label, value, note) {
  return `<article><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong><span>${escapeHtml(note)}</span></article>`;
}

function renderAdminSummary(summary = {}) {
  const knowledge = summary.knowledge || {};
  const prompts = summary.prompts || {};
  document.querySelector("#adminSummary").innerHTML = [
    adminSummaryCard("故障事件", summary.incidents || 0, `${summary.event_inputs || 0} 条原始输入`),
    adminSummaryCard("经验知识", knowledge.published || 0, "已发布并参与检索"),
    adminSummaryCard("提示词", prompts.published || 0, "线上版本"),
    adminSummaryCard("分析链路", summary.rag_runs || 0, "每次均可回放"),
    adminSummaryCard("审计记录", summary.audit_records || 0, "只追加、不覆盖"),
  ].join("");
}

async function loadAdminSummary() {
  state.admin.summary = await api("/api/admin/summary");
  renderAdminSummary(state.admin.summary);
}

function activateAdminTab(name) {
  state.admin.tab = name;
  document.querySelectorAll("[data-admin-tab]").forEach((button) => {
    const active = button.dataset.adminTab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll("[data-admin-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.adminPanel === name);
  });
  if (name === "database") loadAdminRecords().catch((error) => showToast(error.message, true));
  if (name === "knowledge") loadKnowledge().catch((error) => showToast(error.message, true));
  if (name === "prompts") loadPrompts().catch((error) => showToast(error.message, true));
  if (name === "rag") loadRagRuns().catch((error) => showToast(error.message, true));
  if (name === "providers") loadProviders().catch((error) => showToast(error.message, true));
  if (name === "releases") loadReleases().catch((error) => showToast(error.message, true));
}

async function openAdmin() {
  if (!["ai_admin", "super_admin"].includes(state.role)) {
    showToast(`当前是${roleNames[state.role]}工作台，只有 AI 管理员或最高管理员能修改数据与 AI 资产`, true);
    return;
  }
  openDialog(adminDialog);
  try {
    await loadAdminSummary();
    activateAdminTab(state.admin.tab);
  } catch (error) {
    showToast(error.message, true);
  }
}

function recordIdentity(record, index) {
  return record.id || record.site || record.card_id || record.prompt_key || `记录 ${index + 1}`;
}

function renderAdminRecords(items = [], recordType = "incidents") {
  const target = document.querySelector("#recordBrowser");
  if (!items.length) {
    target.innerHTML = `<div class="admin-empty">这个范围内还没有数据。先接入一条真实日志或运行一个模拟案例。</div>`;
    return;
  }
  target.innerHTML = items.map((item, index) => {
    const identity = recordIdentity(item, index);
    const summary = item.title || item.action || item.display_name || item.source || item.summary || "展开查看完整记录";
    return `<details class="record-row">
      <summary><span>${escapeHtml(identity)}</span><strong>${escapeHtml(summary)}</strong><time>${escapeHtml(formatTime(item.updated_at || item.created_at || item.event_time))}</time></summary>
      <pre>${escapeHtml(jsonText(item))}</pre>
      <form class="annotation-form" data-record-type="${escapeHtml(recordType)}" data-record-id="${escapeHtml(identity)}">
        <label>追加备注<input name="note" required placeholder="补充来源、核对结论或说明，不会改写原记录"></label>
        <button class="secondary-button" type="submit">保存备注</button>
      </form>
    </details>`;
  }).join("");
}

async function loadAdminRecords() {
  const type = document.querySelector("#recordTypeSelect").value;
  const query = document.querySelector("#recordSearch").value.trim();
  const payload = await api(`/api/admin/records?type=${encodeURIComponent(type)}&q=${encodeURIComponent(query)}&limit=100`);
  renderAdminRecords(payload.items || [], type);
}

async function saveAnnotation(form) {
  const note = new FormData(form).get("note");
  await api("/api/admin/annotations", {
    method: "POST",
    body: JSON.stringify({ record_type: form.dataset.recordType, record_id: form.dataset.recordId, note }),
  });
  form.reset();
  showToast("备注已追加，原始记录没有被覆盖");
  await loadAdminSummary();
}

function renderKnowledgeList() {
  const search = document.querySelector("#knowledgeSearch").value.trim().toLowerCase();
  const items = state.admin.knowledge.filter((item) => !search || `${item.card_id} ${item.domain} ${item.title}`.toLowerCase().includes(search));
  document.querySelector("#knowledgeList").innerHTML = items.length ? items.map((item) => `
    <button class="asset-row ${state.admin.selectedKnowledge === item.card_id ? "is-selected" : ""}" data-knowledge-id="${escapeHtml(item.card_id)}" type="button">
      <small>${escapeHtml(item.domain)} · ${escapeHtml(item.published_version || "未发布")}</small>
      <strong>${escapeHtml(item.title)}</strong>
      <span>${escapeHtml(item.card_id)}</span>
    </button>`).join("") : `<div class="admin-empty">没有匹配的经验。</div>`;
}

async function loadKnowledge() {
  const payload = await api("/api/admin/knowledge");
  state.admin.knowledge = payload.items || [];
  renderKnowledgeList();
  if (!state.admin.selectedKnowledge && state.admin.knowledge[0]) await selectKnowledge(state.admin.knowledge[0].card_id);
}

async function selectKnowledge(cardId) {
  state.admin.selectedKnowledge = cardId;
  renderKnowledgeList();
  const card = await api(`/api/admin/knowledge/${encodeURIComponent(cardId)}`);
  const latest = card.versions?.[0] || {};
  const published = card.versions?.find((version) => version.version === card.published_version) || latest;
  document.querySelector("#knowledgeDetail").innerHTML = `
    <header class="asset-detail-head"><div><small>${escapeHtml(card.domain)} · ${escapeHtml(card.card_id)}</small><h3>${escapeHtml(card.title)}</h3></div><span class="version-chip">线上 ${escapeHtml(card.published_version || "无")}</span></header>
    <details class="raw-asset" open><summary>查看当前线上知识原文</summary><pre>${escapeHtml(jsonText(published.content || {}))}</pre></details>
    <details class="raw-asset"><summary>查看全部版本（${card.versions?.length || 0}）</summary>
      <div class="version-list">${(card.versions || []).map((version) => `<div><strong>${escapeHtml(version.version)}</strong><span>${escapeHtml(version.release_status)}</span><time>${escapeHtml(formatTime(version.created_at))}</time>${version.release_status === "draft" ? `<button class="secondary-button" data-action="test-asset" data-asset-type="knowledge" data-asset-key="${escapeHtml(card.card_id)}" data-version="${escapeHtml(version.version)}" type="button">运行发布测试</button>` : ""}</div>`).join("")}</div>
    </details>
    <form class="asset-editor" id="knowledgeDraftForm" data-card-id="${escapeHtml(card.card_id)}">
      <div class="editor-heading"><div><h4>基于当前内容创建草稿</h4><p>草稿不会立即参与分析。</p></div></div>
      <label>新版本号<input name="version" required value="${escapeHtml(`${card.card_id}-draft-${Date.now().toString().slice(-6)}`)}"></label>
      <label>知识内容（JSON）<textarea name="content" rows="16" required spellcheck="false">${escapeHtml(jsonText({ ...(latest.content || {}), version: "" }))}</textarea></label>
      <footer class="editor-actions"><button class="primary-button" type="submit">保存知识草稿</button></footer>
    </form>`;
}

async function createKnowledgeDraft(form) {
  const data = new FormData(form);
  const version = String(data.get("version") || "").trim();
  const content = JSON.parse(String(data.get("content") || "{}"));
  content.version = version;
  const created = await api(`/api/admin/knowledge/${encodeURIComponent(form.dataset.cardId)}/versions`, {
    method: "POST",
    body: JSON.stringify({ version, content }),
  });
  showToast(`知识草稿 ${created.version} 已保存，尚未上线`);
  await selectKnowledge(form.dataset.cardId);
  await loadAdminSummary();
}

function renderPromptList() {
  document.querySelector("#promptList").innerHTML = state.admin.prompts.map((item) => `
    <button class="asset-row ${state.admin.selectedPrompt === item.prompt_key ? "is-selected" : ""}" data-prompt-key="${escapeHtml(item.prompt_key)}" type="button">
      <small>线上 ${escapeHtml(item.published_version || "未发布")}</small>
      <strong>${escapeHtml(item.name)}</strong>
      <span>${escapeHtml(item.purpose || item.prompt_key)}</span>
    </button>`).join("");
}

async function loadPrompts() {
  const payload = await api("/api/admin/prompts");
  state.admin.prompts = payload.items || [];
  renderPromptList();
  if (!state.admin.selectedPrompt && state.admin.prompts[0]) await selectPrompt(state.admin.prompts[0].prompt_key);
}

async function selectPrompt(promptKey) {
  state.admin.selectedPrompt = promptKey;
  renderPromptList();
  const prompt = await api(`/api/admin/prompts/${encodeURIComponent(promptKey)}`);
  const published = prompt.versions?.find((version) => version.version === prompt.published_version) || prompt.versions?.[0] || {};
  const draftVersion = `${promptKey}-v${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(2, 14)}`;
  document.querySelector("#promptDetail").innerHTML = `
    <header class="asset-detail-head"><div><small>${escapeHtml(prompt.prompt_key)}</small><h3>${escapeHtml(prompt.name)}</h3><p>${escapeHtml(prompt.purpose)}</p></div><span class="version-chip">线上 ${escapeHtml(prompt.published_version)}</span></header>
    <div class="prompt-raw-grid">
      <section><small>SYSTEM 提示词</small><pre>${escapeHtml(published.system_content || "（空）")}</pre></section>
      <section><small>用户提示词模板</small><pre>${escapeHtml(published.user_template || "（空）")}</pre></section>
    </div>
    <details class="raw-asset"><summary>变量、输出格式和模型参数</summary><pre>${escapeHtml(jsonText({ variables: published.variables, output_schema: published.output_schema, settings: published.settings }))}</pre></details>
    <details class="raw-asset"><summary>查看全部版本（${prompt.versions?.length || 0}）</summary>
      <div class="version-list">${(prompt.versions || []).map((version) => `<div><strong>${escapeHtml(version.version)}</strong><span>${escapeHtml(version.release_status)}</span><time>${escapeHtml(formatTime(version.created_at))}</time>${version.release_status === "draft" ? `<button class="secondary-button" data-action="test-asset" data-asset-type="prompt" data-asset-key="${escapeHtml(promptKey)}" data-version="${escapeHtml(version.version)}" type="button">运行发布测试</button>` : ""}</div>`).join("")}</div>
    </details>
    <form class="asset-editor" id="promptDraftForm" data-prompt-key="${escapeHtml(promptKey)}">
      <div class="editor-heading"><div><h4>编辑为新草稿</h4><p>保存后先预览、再测试，最后才可上线。</p></div></div>
      <label>新版本号<input name="version" required value="${escapeHtml(draftVersion)}"></label>
      <label>System 提示词<textarea name="system_content" rows="5" spellcheck="false">${escapeHtml(published.system_content || "")}</textarea></label>
      <label>用户提示词模板<textarea name="user_template" rows="11" required spellcheck="false">${escapeHtml(published.user_template || "")}</textarea></label>
      <div class="editor-grid">
        <label>变量（JSON 数组）<textarea name="variables" rows="5" spellcheck="false">${escapeHtml(jsonText(published.variables || []))}</textarea></label>
        <label>输出格式（JSON）<textarea name="output_schema" rows="5" spellcheck="false">${escapeHtml(jsonText(published.output_schema || []))}</textarea></label>
        <label>模型参数（JSON）<textarea name="settings" rows="5" spellcheck="false">${escapeHtml(jsonText(published.settings || {}))}</textarea></label>
      </div>
      <footer class="editor-actions"><button class="primary-button" type="submit">保存提示词草稿</button></footer>
    </form>
    <div id="promptDraftStatus" class="draft-status" aria-live="polite"></div>`;
}

async function createPromptDraft(form) {
  const data = new FormData(form);
  const payload = {
    version: String(data.get("version") || "").trim(),
    system_content: String(data.get("system_content") || ""),
    user_template: String(data.get("user_template") || ""),
    variables: JSON.parse(String(data.get("variables") || "[]")),
    output_schema: JSON.parse(String(data.get("output_schema") || "[]")),
    settings: JSON.parse(String(data.get("settings") || "{}")),
  };
  const promptKey = form.dataset.promptKey;
  const created = await api(`/api/admin/prompts/${encodeURIComponent(promptKey)}/versions`, { method: "POST", body: JSON.stringify(payload) });
  document.querySelector("#promptDraftStatus").innerHTML = `
    <div class="draft-ready"><div><strong>草稿 ${escapeHtml(created.version)} 已保存</strong><span>尚未影响线上分析。先检查渲染结果，再运行发布测试。</span></div>
      <button class="secondary-button" data-action="preview-prompt" data-prompt-key="${escapeHtml(promptKey)}" data-version="${escapeHtml(created.version)}" type="button">预览实际提示词</button>
      <button class="primary-button" data-action="test-asset" data-asset-type="prompt" data-asset-key="${escapeHtml(promptKey)}" data-version="${escapeHtml(created.version)}" type="button">运行发布测试</button>
    </div>`;
  showToast(`草稿 ${created.version} 已保存`);
}

async function previewPrompt(button) {
  const values = {
    event_summary: "示例：NVMe I/O timeout，完整SN为 TEST-SN-001",
    redacted_log_excerpt: "nvme0: I/O timeout",
    evidence: ["E-01"],
    facts: ["nvme0 发生 I/O timeout"],
    knowledge_cards: ["KB-STORAGE-001"],
    baseline_hypotheses: ["链路或介质异常，待验证"],
  };
  const preview = await api(`/api/admin/prompts/${encodeURIComponent(button.dataset.promptKey)}/preview`, {
    method: "POST",
    body: JSON.stringify({ version: button.dataset.version, variables: values }),
  });
  document.querySelector("#promptDraftStatus").insertAdjacentHTML("beforeend", `
    <details class="prompt-preview" open><summary>这次测试会发送给模型的内容</summary><pre>${escapeHtml(jsonText(preview))}</pre></details>`);
}

async function testAsset(button) {
  const release = await api("/api/admin/releases/test", {
    method: "POST",
    body: JSON.stringify({ asset_type: button.dataset.assetType, asset_key: button.dataset.assetKey, version: button.dataset.version }),
  });
  showToast(`版本 ${release.version} 已通过结构与流程检查；这不代表事实内容已经验证`);
  await loadReleases();
  activateAdminTab("releases");
}

function releaseStatusName(status) {
  return { tested: "结构检查通过", prepared: "等待人工最终确认", published: "已上线", rolled_back: "已回滚" }[status] || status;
}

async function loadReleases() {
  const payload = await api("/api/admin/releases");
  state.admin.releases = payload.items || [];
  document.querySelector("#releaseList").innerHTML = state.admin.releases.length ? state.admin.releases.map((item) => `
    <article class="release-row" data-release-status="${escapeHtml(item.status)}">
      <div><small>${escapeHtml(item.asset_type)} · ${escapeHtml(item.asset_key)}</small><strong>${escapeHtml(item.version)}</strong><span>${escapeHtml(item.id)} · ${escapeHtml(formatTime(item.created_at))}</span></div>
      <div class="release-checks">${(item.test_summary || []).map((check) => `<span data-passed="${check.passed ? "yes" : "no"}" title="${escapeHtml(check.does_not_prove || "")}">${check.passed ? "✓" : "×"} ${escapeHtml(check.name)}</span>`).join("")}</div>
      <strong class="release-status">${escapeHtml(releaseStatusName(item.status))}</strong>
      <div class="release-actions">
        ${item.status === "tested" ? `<button class="primary-button" data-action="prepare-release" data-release-id="${escapeHtml(item.id)}" type="button">第 1 步：准备上线</button>` : ""}
        ${item.status === "prepared" ? `<button class="primary-button" data-action="confirm-release" data-release-id="${escapeHtml(item.id)}" type="button">第 2 步：确认上线</button>` : ""}
        ${item.status === "published" && item.diff?.previous_version ? `<button class="secondary-button" data-action="rollback-release" data-release-id="${escapeHtml(item.id)}" type="button">回滚上一版</button>` : ""}
      </div>
    </article>`).join("") : `<div class="admin-empty">还没有测试或上线记录。先在提示词或知识库中创建草稿。</div>`;
}

async function prepareRelease(releaseId) {
  await api(`/api/admin/releases/${encodeURIComponent(releaseId)}/prepare`, { method: "POST", body: "{}" });
  showToast("第一步完成：已准备上线，还需要最终确认");
  await loadReleases();
}

function askPublishConfirmation(releaseId) {
  state.admin.pendingPublishId = releaseId;
  document.querySelector("#confirmOnlineCheck").checked = false;
  openDialog(publishConfirmDialog);
  document.querySelector("#confirmOnlineCheck").focus();
}

async function publishRelease(releaseId) {
  await api(`/api/admin/releases/${encodeURIComponent(releaseId)}/publish`, {
    method: "POST",
    body: JSON.stringify({ confirmed_online: true }),
  });
  showToast("新版本已上线，将用于后续新事件");
  await Promise.all([loadReleases(), loadAdminSummary()]);
}

async function rollbackRelease(releaseId) {
  const confirmed = window.confirm("确认回滚到上一版？新事件将改用上一版，历史事件不会被重写。");
  if (!confirmed) return;
  await api(`/api/admin/releases/${encodeURIComponent(releaseId)}/rollback`, { method: "POST", body: "{}" });
  showToast("已回滚到上一版");
  await Promise.all([loadReleases(), loadAdminSummary()]);
}

function renderRagRunList() {
  document.querySelector("#ragRunList").innerHTML = state.admin.ragRuns.length ? state.admin.ragRuns.map((run) => `
    <button class="rag-run-row ${state.admin.selectedRagRun === run.id ? "is-selected" : ""}" data-rag-run-id="${escapeHtml(run.id)}" type="button">
      <small>${escapeHtml(run.mode)} · ${escapeHtml(formatTime(run.created_at))}</small>
      <strong>${escapeHtml(run.incident_id)}</strong>
      <span>知识 ${escapeHtml(run.knowledge_version)} · 提示词 ${escapeHtml(run.prompt_version)}</span>
    </button>`).join("") : `<div class="admin-empty">还没有分析链路。分析一份日志后会自动出现。</div>`;
}

async function loadRagRuns() {
  const payload = await api("/api/admin/rag-runs");
  state.admin.ragRuns = payload.items || [];
  renderRagRunList();
  if (!state.admin.selectedRagRun && state.admin.ragRuns[0]) await selectRagRun(state.admin.ragRuns[0].id);
}

async function selectRagRun(runId) {
  state.admin.selectedRagRun = runId;
  renderRagRunList();
  const run = await api(`/api/admin/rag-runs/${encodeURIComponent(runId)}`);
  const hits = run.hits || [];
  document.querySelector("#ragTraceDetail").innerHTML = `
    <header class="rag-trace-head"><div><small>${escapeHtml(run.id)}</small><h3>事件 ${escapeHtml(run.incident_id)}</h3></div><span>${escapeHtml(run.mode)} · ${escapeHtml(run.model_provider)}</span></header>
    <div class="rag-route" aria-label="RAG 分析数据流">${(run.steps || []).map((step) => `
      <details class="rag-step" data-step-status="${escapeHtml(step.status)}" ${step.order <= 4 || step.type === "final_result" ? "open" : ""}>
        <summary><span class="rag-order">${String(step.order).padStart(2, "0")}</span><div><strong>${escapeHtml(ragStepNames[step.type] || step.type)}</strong><small>${escapeHtml(step.message)}</small></div><b>${step.status === "not_run" ? "未运行" : "已记录"}</b></summary>
        <div class="rag-step-body"><section><small>输入</small><pre>${escapeHtml(jsonText(step.input))}</pre></section><section><small>输出</small><pre>${escapeHtml(jsonText(step.output))}</pre></section></div>
      </details>`).join("")}</div>
    <section class="retrieval-audit"><header><h4>本次命中的知识与原因</h4><span>${hits.length} 条</span></header>
      ${hits.length ? hits.map((hit) => `<article><div><strong>#${hit.rank} ${escapeHtml(hit.card_id)}</strong><span>总分 ${Number(hit.score || 0).toFixed(3)}</span></div><p>${escapeHtml((hit.reasons || []).join("；") || "无文字原因")}</p><pre>${escapeHtml(jsonText(hit.retrieval))}</pre></article>`).join("") : `<p class="admin-empty">本次没有命中知识卡。</p>`}
    </section>`;
}

function providerTypeName(value) {
  return { local: "本地模型", private_partner: "合作厂商私有化", cloud: "经授权云接口" }[value] || value;
}

function providerStateName(value) {
  return { not_configured: "尚未接通", configured_not_tested: "已配置，尚未验证" }[value] || value;
}

function renderProviderList() {
  document.querySelector("#providerList").innerHTML = state.admin.providers.map((item) => `
    <button class="provider-row ${state.admin.selectedProvider === item.provider_key ? "is-selected" : ""}" data-provider-key="${escapeHtml(item.provider_key)}" type="button">
      <small>${escapeHtml(providerTypeName(item.provider_type))}</small><strong>${escapeHtml(item.display_name)}</strong>
      <span>${escapeHtml(providerStateName(item.connection_state))}${item.enabled ? " · 已启用" : " · 已禁用"}</span>
    </button>`).join("");
}

async function loadProviders() {
  const payload = await api("/api/admin/providers");
  state.admin.providers = payload.items || [];
  renderProviderList();
  if (!state.admin.selectedProvider && state.admin.providers[0]) await selectProvider(state.admin.providers[0].provider_key);
}

async function selectProvider(providerKey) {
  state.admin.selectedProvider = providerKey;
  renderProviderList();
  const provider = await api(`/api/admin/providers/${encodeURIComponent(providerKey)}`);
  const config = provider.config || {};
  const policy = provider.policies?.[0] || {};
  document.querySelector("#providerDetail").innerHTML = `
    <header class="provider-head"><div><small>${escapeHtml(provider.provider_key)}</small><h3>${escapeHtml(provider.display_name)}</h3><p>${escapeHtml(config.description || "")}</p></div><span data-provider-state="${escapeHtml(provider.connection_state)}">${escapeHtml(providerStateName(provider.connection_state))}</span></header>
    <div class="provider-boundary">
      <section><small>允许进入模型的内容</small>${listLines(policy.data_policy?.allowed || [], "尚未配置")}</section>
      <section><small>未授权时禁止外发</small>${listLines(policy.data_policy?.blocked_without_explicit_authorization || [], "尚未配置")}</section>
      <section><small>失败回退</small><strong>回到规则＋本地知识</strong><span>不会自动改发另一家外部厂商</span></section>
    </div>
    <form class="provider-form" id="providerForm" data-provider-key="${escapeHtml(provider.provider_key)}">
      <div class="form-grid compact-fields">
        <label>显示名称<input name="display_name" required value="${escapeHtml(provider.display_name)}"></label>
        <label>接口类型<select name="provider_type"><option value="local" ${provider.provider_type === "local" ? "selected" : ""}>本地模型</option><option value="private_partner" ${provider.provider_type === "private_partner" ? "selected" : ""}>合作厂商私有化</option><option value="cloud" ${provider.provider_type === "cloud" ? "selected" : ""}>经授权云接口</option></select></label>
        <label>兼容接口地址<input name="endpoint" value="${escapeHtml(config.endpoint || "")}" placeholder="https://gateway.example/v1"></label>
        <label>模型名称<input name="model" value="${escapeHtml(config.model || "")}"></label>
        <label>数据驻留位置<input name="data_residency" value="${escapeHtml(config.data_residency || "unknown")}"></label>
        <label>超时秒数<input name="timeout_seconds" type="number" min="1" max="120" value="${escapeHtml(config.timeout_seconds || 20)}"></label>
      </div>
      <label class="provider-check"><input name="enabled" type="checkbox" ${provider.enabled ? "checked" : ""}> 启用这个适配器（不代表已经验证连通）</label>
      <label class="provider-check"><input name="secret_configured" type="checkbox" ${provider.secret_configured ? "checked" : ""}> 密钥已通过部署环境配置</label>
      <p>密钥不在这个页面填写，也不会通过接口回显。当前版本只保存适配器元数据；真实厂商接入需要官方接口契约和测试环境。</p>
      <footer class="editor-actions"><button class="primary-button" type="submit">保存适配配置</button></footer>
    </form>`;
}

async function saveProvider(form) {
  const raw = new FormData(form);
  const payload = Object.fromEntries(raw.entries());
  payload.enabled = form.querySelector('[name="enabled"]').checked;
  payload.secret_configured = form.querySelector('[name="secret_configured"]').checked;
  payload.timeout_seconds = Number(payload.timeout_seconds || 20);
  const updated = await api(`/api/admin/providers/${encodeURIComponent(form.dataset.providerKey)}`, { method: "POST", body: JSON.stringify(payload) });
  showToast(`${updated.display_name} 的适配配置已保存；连通性仍需真实接口验证`);
  await loadProviders();
  await selectProvider(form.dataset.providerKey);
}

const operationStatusNames = {
  awaiting_identity: "等待核对设备身份",
  blocked_identity: "设备身份不一致，已阻止",
  awaiting_permission: "等待接口人确认许可",
  blocked_permission: "当前禁止操作",
  awaiting_review: "等待第二人复核",
  blocked_review: "复核未通过",
  ready: "三道门已通过，可以开始",
  operating: "现场操作中",
  completed_success: "操作成功，已结束",
  completed_failed: "现场未解决，失败结束",
};

function canImportOperation() {
  return ["interface_person", "ai_admin", "super_admin"].includes(state.role);
}

function canDecideOperationPermission() {
  return ["interface_person", "facility_lead", "ai_admin", "super_admin"].includes(state.role);
}

function canOperate() {
  return ["onsite_operator", "ai_admin", "super_admin"].includes(state.role);
}

function renderOperationSession() {
  document.querySelector("#operationSession").innerHTML = `
    <div><small>当前工作台</small><strong>${escapeHtml(roleNames[state.role])}</strong></div>
    <div><small>操作账号</small><strong>${escapeHtml(state.actor)}</strong></div>
    <div><small>复核原则</small><strong>操作人与复核人不能相同</strong></div>
    <div><small>夜间建议路径（通知接口待接）</small><strong>授权复核池 → 备用人 → 负责人</strong></div>`;
  document.querySelector("#omsImportSection").classList.toggle("is-unavailable", !canImportOperation());
  document.querySelector("#omsImportSection").open = canImportOperation();
}

function renderOperationList() {
  const target = document.querySelector("#operationList");
  target.innerHTML = state.operations.length ? state.operations.map((item) => `
    <button class="operation-row ${state.selectedOperation?.id === item.id ? "is-selected" : ""}" data-operation-id="${escapeHtml(item.id)}" type="button">
      <small>${escapeHtml(item.work_order.order_no)} · ${escapeHtml(item.work_order.site)}</small>
      <strong>${escapeHtml(item.work_order.target_sn)}</strong>
      <span>${escapeHtml(item.work_order.rack_position)}</span>
      <b>${escapeHtml(operationStatusNames[item.status] || item.status)}</b>
    </button>`).join("") : `<div class="admin-empty">还没有现场操作单。接口人或管理员可先导入一张 OMS 工单快照。</div>`;
}

function gateItem(name, passed, waitingText) {
  return `<div data-gate-passed="${passed ? "yes" : "no"}"><span>${passed ? "✓" : "!"}</span><small>${escapeHtml(name)}</small><strong>${passed ? "已通过" : escapeHtml(waitingText)}</strong></div>`;
}

function operationActions(operation) {
  const work = operation.work_order;
  const final = operation.status.startsWith("completed_");
  const blocks = [];
  if (canOperate() && !final && operation.status !== "operating") {
    blocks.push(`<form class="operation-action-form" data-operation-action="identity" data-operation-id="${escapeHtml(operation.id)}">
      <header><strong>1. 核对现场完整 SN</strong><span>扫描结果必须与工单逐字符一致</span></header>
      <div class="operation-action-grid">
        <label>现场完整 SN<input name="observed_sn" required value="${escapeHtml(operation.observed_sn || "")}" placeholder="不允许只输入末位"></label>
        <label>获取方式<select name="method"><option value="barcode">条码扫描</option><option value="qr">二维码扫描</option><option value="ocr">SN 局部照片 OCR 结果</option><option value="manual">手工完整输入</option></select></label>
        <button class="secondary-button" data-action="scan-sn" type="button">使用相机扫码</button>
        <button class="primary-button" type="submit">核对完整 SN</button>
      </div>
      <p>OCR 服务尚未配置时，系统只接受你手工填入识别结果，并明确标记为 OCR 回退；不会声称自动识别成功。</p>
    </form>`);
  }
  if (canDecideOperationPermission() && !final && operation.status !== "operating" && operation.identity_status === "confirmed") {
    blocks.push(`<form class="operation-action-form" data-operation-action="permission" data-operation-id="${escapeHtml(operation.id)}">
      <header><strong>2. 确认是否允许操作</strong><span>设备找对了，不代表允许断电或更换</span></header>
      <div class="operation-action-grid">
        <label>许可结论<select name="decision"><option value="allowed">允许操作</option><option value="needs_confirmation">仍需等待</option><option value="forbidden">禁止操作</option></select></label>
        <label>确认依据<input name="reason" required placeholder="业务已迁移 / 重装发起 / 禁止关机"></label>
        <button class="primary-button" type="submit">保存许可结论</button>
      </div>
    </form>`);
  }
  if (!final && operation.status !== "operating" && operation.identity_status === "confirmed" && operation.permission_status === "allowed") {
    blocks.push(`<form class="operation-action-form" data-operation-action="review" data-operation-id="${escapeHtml(operation.id)}">
      <header><strong>3. 第二人复核</strong><span>白天可由现场同岗复核；单人值班可由授权人员远程复核。首版记录结果，暂不自动发送如流或电话。</span></header>
      <div class="operation-action-grid">
        <label>复核方式<select name="review_mode"><option value="onsite_peer">现场同岗双人复核</option><option value="remote_authorized">授权远程复核</option></select></label>
        <label>复核结论<select name="decision"><option value="approved">信息一致，允许开始</option><option value="rejected">信息有疑问，阻止操作</option></select></label>
        <label>复核说明<input name="note" placeholder="已核对 OMS 快照、完整SN与机架位"></label>
        <button class="primary-button" type="submit">提交复核</button>
      </div>
    </form>`);
  }
  if (canOperate() && operation.status === "ready") {
    blocks.push(`<div class="start-operation"><div><strong>设备身份、操作许可、人工复核均已通过</strong><span>点击后状态变为“操作中”，现场再开始物理操作。</span></div><button class="primary-button" data-action="start-operation" data-operation-id="${escapeHtml(operation.id)}" type="button">确认开始操作</button></div>`);
  }
  if (canOperate() && operation.status === "operating") {
    blocks.push(`<form class="operation-action-form completion-form" data-operation-action="complete" data-operation-id="${escapeHtml(operation.id)}">
      <header><strong>结束操作并反馈结果</strong><span>点击结束不是直接结单，必须填写结果、原因和详情</span></header>
      <div class="operation-action-grid">
        <label>操作结果<select name="result"><option value="success">成功：已恢复并完成验证</option><option value="failed">失败：现场能做的已完成但未解决</option></select></label>
        <label>结构化原因<select name="reason"><option value="completed_as_ordered">已按工单完成</option><option value="network_recovered">三网已通</option><option value="mainboard_failure">疑似主板故障</option><option value="replacement_no_effect">更换后无改善</option><option value="permission_or_business_blocked">业务或许可阻止</option><option value="other">其他</option></select></label>
        <label>下线备件 SN<input name="offline_sn" placeholder="如未更换可不填"></label>
        <label>上线备件 SN<input name="online_sn" placeholder="如未更换可不填"></label>
        <label class="span-two">详细反馈<textarea name="details" rows="4" required placeholder="做了哪些操作、验证了什么、目前是什么状态"></textarea></label>
        <label class="span-two">超时原因<input name="timeout_reason" placeholder="未超时可不填"></label>
        <button class="primary-button" type="submit">确认信息并结束操作</button>
      </div>
    </form>`);
  }
  return blocks.join("");
}

function renderOperationDetail(operation) {
  const work = operation.work_order;
  const history = operation.history || [];
  document.querySelector("#operationDetail").innerHTML = `
    <header class="operation-detail-head"><div><small>${escapeHtml(operation.id)} · 快照 ${escapeHtml(work.id)}</small><h3>${escapeHtml(work.order_no)}</h3><p>${escapeHtml(work.operation_type)} · ${escapeHtml(work.urgency)}</p></div><strong data-operation-status="${escapeHtml(operation.status)}">${escapeHtml(operationStatusNames[operation.status] || operation.status)}</strong></header>
    <div class="work-order-snapshot">
      <div><small>工单完整 SN</small><strong>${escapeHtml(work.target_sn)}</strong></div><div><small>机架位</small><strong>${escapeHtml(work.rack_position)}</strong></div><div><small>设备名</small><strong>${escapeHtml(work.device_name || "未提供")}</strong></div><div><small>从重装中发起</small><strong>${escapeHtml(work.from_reinstall)}</strong></div>
    </div>
    <div class="operation-gates">
      ${gateItem("设备身份", operation.gates.identity, operation.identity_status === "mismatch" ? "不一致" : "待核对")}
      ${gateItem("操作许可", operation.gates.permission, operation.permission_status === "forbidden" ? "禁止" : "待确认")}
      ${gateItem("人工复核", operation.gates.human_review, "待第二人")}
    </div>
    ${operation.result_status ? `<section class="operation-result" data-result="${escapeHtml(operation.result_status)}"><strong>${operation.result_status === "success" ? "操作成功" : "失败接单"}</strong><p>${escapeHtml(operation.result_reason)}：${escapeHtml(operation.result_details)}</p><span>下线 SN：${escapeHtml(operation.offline_sn || "无")} · 上线 SN：${escapeHtml(operation.online_sn || "无")}</span></section>` : ""}
    <div class="operation-actions-stack">${operationActions(operation)}</div>
    <details class="operation-history"><summary>查看完整操作日志（${history.length}）</summary>${history.map((item) => `<div><time>${escapeHtml(formatTime(item.created_at))}</time><strong>${escapeHtml(item.action)}</strong><span>${escapeHtml(item.actor)} · ${escapeHtml(item.from_status || "开始")} → ${escapeHtml(item.to_status)}</span><pre>${escapeHtml(jsonText(item.details))}</pre></div>`).join("")}</details>`;
}

async function loadOperations(preferredId = null) {
  const payload = await api("/api/operations");
  state.operations = payload.items || [];
  renderOperationList();
  const target = preferredId || state.selectedOperation?.id || state.operations[0]?.id;
  if (target) await selectOperation(target);
}

async function selectOperation(operationId) {
  state.selectedOperation = await api(`/api/operations/${encodeURIComponent(operationId)}`);
  renderOperationList();
  renderOperationDetail(state.selectedOperation);
}

async function openOperations() {
  renderOperationSession();
  openDialog(operationDialog);
  await loadOperations();
}

async function importWorkOrder(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  const created = await api("/api/operations/import", { method: "POST", body: JSON.stringify(payload) });
  form.reset();
  showToast(`OMS 快照 ${created.work_order.order_no} 已保存，等待核对完整 SN`);
  await loadOperations(created.id);
}

async function submitOperationAction(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  const action = form.dataset.operationAction;
  const operationId = form.dataset.operationId;
  const updated = await api(`/api/operations/${encodeURIComponent(operationId)}/${action}`, { method: "POST", body: JSON.stringify(payload) });
  const message = { identity: "完整 SN 核对结果已保存", permission: "操作许可已保存", review: "人工复核已保存", complete: "操作结果和详细反馈已保存" }[action] || "操作已保存";
  showToast(message);
  await loadOperations(updated.id);
}

async function startOperation(operationId) {
  const updated = await api(`/api/operations/${encodeURIComponent(operationId)}/start`, { method: "POST", body: "{}" });
  showToast("已确认开始操作，状态已变为“现场操作中”");
  await loadOperations(updated.id);
}

async function scanOperationSN() {
  const form = document.querySelector('[data-operation-action="identity"]');
  if (!form) return;
  if (typeof cameraDialog.showModal === "function") cameraDialog.showModal();
  document.querySelector("#scannerStatus").textContent = "正在打开摄像头，只在本机识别条码…";
  try {
    const value = await window.IDCAIDeviceScan.scanFullSN();
    form.querySelector('[name="observed_sn"]').value = value;
    form.querySelector('[name="method"]').value = "barcode";
    document.querySelector("#scannerStatus").textContent = `已识别完整 SN：${value}`;
    closeDialog(cameraDialog);
    showToast("条码已识别，请核对后提交；画面没有保存");
  } catch (error) {
    document.querySelector("#scannerStatus").textContent = error.message;
    showToast(error.message, true);
  }
}

const labConnectionNames = {
  connected: "已连接",
  disconnected: "已断开",
  degraded: "降级返回",
  delayed: "延迟返回",
  permission_denied: "权限不足",
  invalid_payload: "无效结构",
};

const correlationNames = {
  explicit: "明确事故号",
  topology_time_window: "拓扑＋时间窗口",
  exact_identity_time_window: "准确身份＋时间窗口",
  insufficient: "证据不足，不自动合并",
};

function activateLabTab(name) {
  state.lab.tab = name;
  document.querySelectorAll("[data-lab-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.labTab === name);
  });
  document.querySelectorAll("[data-lab-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.labPanel === name);
  });
}

function renderLabPlatforms() {
  const states = Object.entries(labConnectionNames);
  document.querySelector("#labPlatformGrid").innerHTML = state.lab.platforms.map((platform) => `
    <article class="platform-card" data-state="${escapeHtml(platform.connection_state)}">
      <header><div><small class="eyebrow">${escapeHtml(platform.platform_type)}</small><h4>${escapeHtml(platform.display_name)}</h4></div><span class="platform-state">${escapeHtml(labConnectionNames[platform.connection_state] || platform.connection_state)}</span></header>
      <p>${escapeHtml(platform.config?.description || "模拟平台")}</p>
      <footer>
        <label>模拟连接状态<select data-platform-state="${escapeHtml(platform.platform_key)}">${states.map(([value, label]) => `<option value="${value}" ${value === platform.connection_state ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select></label>
        <button class="secondary-button" data-action="set-platform-state" data-platform-key="${escapeHtml(platform.platform_key)}" type="button">应用</button>
      </footer>
      <small>${platform.event_count || 0} 条信号 · ${platform.last_event_at ? `最近 ${escapeHtml(formatTime(platform.last_event_at))}` : "尚无输入"}</small>
    </article>`).join("");
}

function renderLabScenarios() {
  document.querySelector("#labScenarioList").innerHTML = state.lab.scenarios.map((scenario) => `
    <article class="lab-scenario"><div><strong>${escapeHtml(scenario.name)}</strong><span>${escapeHtml(scenario.description)}</span></div><button class="secondary-button" data-action="run-lab-scenario" data-scenario-id="${escapeHtml(scenario.id)}" type="button">运行场景</button></article>`).join("") || `<div class="admin-empty">没有可用的跨平台场景。</div>`;
}

function renderLabEvents() {
  const target = document.querySelector("#labEventList");
  if (!state.lab.events.length) {
    target.innerHTML = `<div class="admin-empty">尚无平台信号。运行场景或手工发送一条事件。</div>`;
    return;
  }
  target.innerHTML = state.lab.events.slice(0, 80).map((item) => {
    const correlation = item.correlation || {};
    const entity = item.entity || {};
    const identity = entity.sn || entity.name || entity.asset_id || "身份缺失";
    const method = correlation.method || correlation.level || "insufficient";
    return `<article class="signal-entry">
      <div><small>${escapeHtml(item.platform_key)} · ${escapeHtml(formatTime(item.occurred_at))}</small><strong>${escapeHtml(identity)}</strong><span>${escapeHtml(entity.interface || entity.rack_position || item.signal_type)}</span></div>
      <div><small>${escapeHtml(item.id)} · ${escapeHtml(item.source_event_id)}</small><strong>${escapeHtml(item.summary)}</strong><span>状态：${escapeHtml(item.delivery_status)} · 信号：${escapeHtml(item.signal_type)}</span></div>
      <div class="correlation-stamp"><small>归并依据</small><strong>${escapeHtml(correlationNames[method] || method)}</strong><span>${item.incident_id ? `进入 ${escapeHtml(item.incident_id)}` : "未形成事故"}</span></div>
    </article>`;
  }).join("");
}

function renderAgentIncidentOptions() {
  const select = document.querySelector("#agentIncidentSelect");
  const current = select.value || state.selectedId || "";
  select.innerHTML = state.incidents.map((incident) => `<option value="${escapeHtml(incident.id)}" ${incident.id === current ? "selected" : ""}>${escapeHtml(incident.id)} · ${escapeHtml(incident.title)}</option>`).join("");
  if (!state.incidents.length) select.innerHTML = `<option value="">请先运行一个场景</option>`;
}

function renderAgentRuns() {
  const target = document.querySelector("#agentRunList");
  if (!state.lab.agentRuns.length) {
    target.innerHTML = `<div class="admin-empty">还没有 AI 调查运行记录。</div>`;
    return;
  }
  target.innerHTML = state.lab.agentRuns.map((run) => `
    <button class="agent-run-row ${state.lab.selectedAgentRun?.id === run.id ? "is-selected" : ""}" data-agent-run-id="${escapeHtml(run.id)}" type="button">
      <small>${escapeHtml(run.id)} · ${escapeHtml(formatTime(run.started_at))}</small>
      <strong>${escapeHtml(run.mode === "model" ? "真实模型 Agent" : run.mode === "test_stub" ? "测试模型桩（非真实AI）" : "固定规则基线")}</strong>
      <span>${escapeHtml(run.status)} · ${escapeHtml(run.stop_reason || "运行中")}</span>
    </button>`).join("");
}

function renderAgentTrace(run) {
  const target = document.querySelector("#agentTraceDetail");
  if (!run) {
    target.innerHTML = `<div class="admin-empty">运行或选择一次调查，查看每轮依据、工具、证据和假设变化。</div>`;
    return;
  }
  const label = run.summary?.label || (run.mode === "model" ? "真实模型 Agent" : run.mode);
  if (state.role !== "super_admin") {
    target.innerHTML = `<div class="agent-trace-head"><div><p class="eyebrow">${escapeHtml(run.id)}</p><h3>${escapeHtml(label)}</h3><p>${escapeHtml(run.stop_reason || run.status)}</p></div><span class="safety-badge">默认脱敏</span></div>
      <div class="panel-explainer">AI 管理员可以运行调试并查看结果摘要；逐轮模型依据、工具返回和假设变化只在“最高管理员”工作台显示。</div>
      <pre>${escapeHtml(jsonText(run.summary || {}))}</pre>`;
    return;
  }
  const steps = run.steps || [];
  target.innerHTML = `<div class="agent-trace-head"><div><p class="eyebrow">${escapeHtml(run.id)} · ${escapeHtml(run.prompt_version)}</p><h3>${escapeHtml(label)}</h3><p>${escapeHtml(run.stop_reason || run.status)}</p></div><span class="safety-badge">结构化可审计轨迹</span></div>
    ${steps.map((step) => `<details class="trace-step" ${step.round_no === 1 ? "open" : ""}>
      <summary><small>ROUND ${escapeHtml(step.round_no)}</small><strong>${escapeHtml(step.rationale || step.step_type)}</strong><span>${escapeHtml(step.tool_name || step.status)}</span></summary>
      <div class="trace-step-body">
        <section><h4>本轮依据、证据与假设变化</h4><pre>${escapeHtml(jsonText({
          evidence_ids: step.evidence_ids,
          hypotheses_before: step.hypotheses_before,
          hypotheses_after: step.hypotheses_after,
          validation: step.validation,
        }))}</pre></section>
        <section><h4>只读工具调用与结构化返回</h4><pre>${escapeHtml(jsonText({
          tool: step.tool_name,
          arguments: step.tool_args,
          output: step.tool_output,
          model_decision: step.model_output,
        }))}</pre></section>
      </div>
    </details>`).join("") || `<div class="admin-empty">这次运行没有产生调查轮次。</div>`}`;
}

function renderLabTopology() {
  const topology = state.lab.topology || { entities: [], links: [] };
  const priority = { switch: 1, interface: 2, server: 3, application: 4, facility_zone: 5 };
  const entities = [...(topology.entities || [])].sort((a, b) => (priority[a.entity_type] || 9) - (priority[b.entity_type] || 9));
  const links = topology.links || [];
  document.querySelector("#labTopology").innerHTML = entities.map((entity) => `<article class="topology-node"><small>${escapeHtml(entity.entity_type)} · ${escapeHtml(entity.source)}</small><strong>${escapeHtml(entity.canonical_key)}</strong><span>${escapeHtml(Object.values(entity.attributes || {}).filter(Boolean).join(" · "))}</span></article>`).join("") + `<div class="topology-links">${links.map((link) => `${escapeHtml(link.from_entity_id)} —[${escapeHtml(link.link_type)}]→ ${escapeHtml(link.to_entity_id)}`).join("<br>")}</div>`;
}

function renderBackups() {
  const target = document.querySelector("#backupList");
  target.innerHTML = state.lab.backups.map((item) => `<div class="backup-row"><strong>${escapeHtml(item.id)} · ${escapeHtml(item.status)}</strong><code>${escapeHtml(item.path)}</code><span>${escapeHtml(String(item.size_bytes || 0))} bytes · ${escapeHtml(formatTime(item.completed_at))}</span></div>`).join("") || `<div class="admin-empty">尚未创建本地数据库备份。</div>`;
}

async function loadLab() {
  const [platforms, scenarios, events, topology, runs, backups] = await Promise.all([
    api("/api/lab/platforms"),
    api("/api/lab/scenarios"),
    api("/api/lab/events?limit=200"),
    api("/api/lab/topology"),
    api("/api/agent/runs"),
    api("/api/admin/backups"),
  ]);
  state.lab.platforms = platforms.items || [];
  state.lab.scenarios = scenarios.items || [];
  state.lab.events = events.items || [];
  state.lab.topology = topology;
  state.lab.agentRuns = runs.items || [];
  state.lab.backups = backups.items || [];
  renderLabPlatforms();
  renderLabScenarios();
  renderLabEvents();
  renderAgentIncidentOptions();
  renderAgentRuns();
  renderLabTopology();
  renderBackups();
  if (state.lab.selectedAgentRun) renderAgentTrace(state.lab.selectedAgentRun);
}

async function openLab() {
  if (!["ai_admin", "super_admin"].includes(state.role)) {
    showToast(`当前是${roleNames[state.role]}工作台，接入模拟与 AI 调试只对管理员开放`, true);
    return;
  }
  openDialog(labDialog);
  activateLabTab(state.lab.tab);
  try {
    await loadLab();
  } catch (error) {
    showToast(`实验室加载失败：${error.message}`, true);
  }
}

const drillStatusNames = {
  running: "系统推进中",
  waiting_human: "等待人工反馈",
  resolved: "已恢复并验证",
  transferred: "已转专业组",
  evidence_insufficient: "证据不足",
  operation_blocked: "操作被阻断",
  false_positive: "误报",
  terminated: "人工终止",
};

const drillStepTypeNames = {
  system: "演练控制",
  platform_signal: "平台信号",
  human_action: "人工反馈",
};

const drillLocationNames = {
  site: "机房",
  row: "排 / 区域",
  rack: "机柜",
  rack_position: "机架位",
  device: "设备",
  interface: "端口",
};

const drillImpactNames = {
  business_network: "业务网络路径",
  management_network: "带外管理路径",
  application: "应用服务路径",
  facility: "动环影响路径",
  hardware: "服务器硬件路径",
};

function drillTerminal(run) {
  return ["resolved", "transferred", "evidence_insufficient", "operation_blocked", "false_positive", "terminated"].includes(run?.status);
}

function drillCategoryName(id) {
  return state.drills.catalog?.categories?.find((item) => item.id === id)?.name || id;
}

function selectedDrillScenario() {
  return state.drills.catalog?.items?.find((item) => item.id === state.drills.selectedScenarioId) || null;
}

function renderDrillLoadState() {
  const target = document.querySelector("#drillLoadState");
  const catalogList = document.querySelector("#drillCatalogList");
  const tabs = document.querySelector("#drillCategoryTabs");
  if (state.drills.loadStatus === "ready") {
    target.hidden = true;
    catalogList.hidden = false;
    tabs.hidden = false;
    return;
  }
  catalogList.hidden = true;
  tabs.hidden = true;
  target.hidden = false;
  if (state.drills.loadStatus === "error") {
    target.dataset.state = "error";
    target.innerHTML = `<strong>故障演练库读取失败</strong><span>${escapeHtml(state.drills.loadError || "服务暂时不可用")}</span><button class="secondary-button" data-action="retry-drills" type="button">重新加载</button>`;
    return;
  }
  target.dataset.state = "loading";
  target.innerHTML = `<strong>正在读取故障演练库</strong><span>正在获取分类、场景和最近演练记录…</span>`;
}

function renderDrillCatalog() {
  const catalog = state.drills.catalog || { categories: [], items: [] };
  renderDrillLoadState();
  if (state.drills.loadStatus !== "ready") return;
  if (!catalog.categories.some((item) => item.id === state.drills.category)) state.drills.category = catalog.categories[0]?.id || "network";
  const counts = Object.fromEntries(catalog.categories.map((category) => [category.id, catalog.items.filter((item) => item.category === category.id).length]));
  document.querySelector("#drillCategoryTabs").innerHTML = catalog.categories.map((category) => `
    <button class="${category.id === state.drills.category ? "is-active" : ""}" data-drill-category="${escapeHtml(category.id)}" type="button">
      <strong>${escapeHtml(category.name)}</strong><small>${escapeHtml(counts[category.id] || 0)} 个</small>
    </button>`).join("");
  document.querySelector("#drillCategorySelect").value = state.drills.category;
  const items = catalog.items.filter((item) => item.category === state.drills.category);
  if (!items.some((item) => item.id === state.drills.selectedScenarioId)) state.drills.selectedScenarioId = items[0]?.id || "";
  const scenarioInput = document.querySelector("#drillScenarioSelect");
  scenarioInput.value = state.drills.selectedScenarioId;
  const blind = document.querySelector('#drillStartForm input[name="mode"]:checked')?.value === "blind";
  document.querySelector("#drillCatalogCount").textContent = blind ? "随机抽取" : `${items.length} 项`;
  const catalogList = document.querySelector("#drillCatalogList");
  catalogList.hidden = blind;
  catalogList.innerHTML = items.map((item) => `
    <button class="drill-catalog-item ${item.id === state.drills.selectedScenarioId ? "is-selected" : ""}" data-drill-scenario-id="${escapeHtml(item.id)}" type="button">
      <span class="drill-severity" data-severity="${escapeHtml(item.severity)}">${escapeHtml(item.severity === "critical" ? "严重" : "警告")}</span>
      <strong>${escapeHtml(item.name)}</strong>
      <small>${escapeHtml(item.visible_symptom)}</small>
      <i>${escapeHtml(item.owner_team)} · ${item.needs_onsite ? "需要现场" : "可先远程"}</i>
    </button>`).join("") || `<div class="admin-empty">这个分类下还没有演练场景。</div>`;
  const selected = selectedDrillScenario();
  document.querySelector("#drillSelectedName").textContent = blind ? `${drillCategoryName(state.drills.category)}随机盲测` : selected?.name || "当前分类暂无场景";
  document.querySelector("#drillSelectedSymptom").textContent = blind
    ? "系统只公开故障现象与信号，隐藏答案在演练结束前与运行链隔离。"
    : selected?.visible_symptom || "请切换其他故障分类。";
  document.querySelector("#drillSelectedMeta").innerHTML = selected && !blind
    ? `<span>${escapeHtml(selected.owner_team)}</span><span>${selected.needs_onsite ? "需要现场" : "可先远程"}</span><span>${escapeHtml((selected.source_platforms || []).join(" + "))}</span>`
    : `<span>${escapeHtml(drillCategoryName(state.drills.category))}</span><span>隐藏答案</span><span>全程留痕</span>`;
  const startButton = document.querySelector('#drillStartForm button[type="submit"]');
  startButton.disabled = blind ? !state.drills.category : !selected;
  scenarioInput.required = !blind;
}

function renderDrillRuns() {
  const target = document.querySelector("#drillRunList");
  target.innerHTML = state.drills.runs.slice(0, 12).map((run) => `
    <button class="drill-run-row ${run.id === state.drills.active?.id ? "is-selected" : ""}" data-drill-run-id="${escapeHtml(run.id)}" type="button">
      <span><b>${escapeHtml(run.mode === "blind" ? "盲" : "定")}</b>${escapeHtml(drillCategoryName(run.category))}</span>
      <strong>${escapeHtml(run.display_name)}</strong>
      <small>${escapeHtml(drillStatusNames[run.status] || run.status)} · ${escapeHtml(formatTime(run.updated_at))}</small>
    </button>`).join("") || `<div class="admin-empty">还没有演练记录。</div>`;
}

function drillDetailRows(details = {}) {
  const preferred = [
    ["source_system", "来源平台"], ["source_event_id", "来源事件"], ["signal_type", "信号类型"],
    ["integration_event_id", "接入记录"], ["governance_decision", "治理结论"], ["alert_id", "告警记录"],
    ["action_id", "人工动作"], ["simulated_observation", "模拟观察"], ["notes", "人工备注"],
    ["analysis_mode", "分析模式"], ["responsibility_boundary", "责任边界"],
  ];
  return preferred.filter(([key]) => details[key] !== undefined && details[key] !== "").map(([key, label]) => {
    const value = typeof details[key] === "object" ? JSON.stringify(details[key]) : details[key];
    return `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value === true ? "是" : value === false ? "否" : value)}</dd></div>`;
  }).join("");
}

function renderDrillTimeline(run) {
  const target = document.querySelector("#drillStepList");
  target.innerHTML = (run.steps || []).map((step, index) => `
    <li class="drill-step" data-step-type="${escapeHtml(step.step_type)}">
      <span class="drill-step-index">${String(index + 1).padStart(2, "0")}</span>
      <article>
        <header><span>${escapeHtml(drillStepTypeNames[step.step_type] || step.step_type)}</span><time>${escapeHtml(formatTime(step.created_at))}</time></header>
        <h4>${escapeHtml(step.summary)}</h4>
        <dl>${drillDetailRows(step.details)}</dl>
        ${step.incident_id ? `<p>关联事故 <button class="text-button" data-incident-id="${escapeHtml(step.incident_id)}" type="button">${escapeHtml(step.incident_id)}</button></p>` : ""}
      </article>
    </li>`).join("") || `<li class="drill-step-empty">尚未接收到演练信号。</li>`;
  target.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderDrillLocation(run) {
  const entries = Object.entries(run.location || {}).filter(([, value]) => value);
  document.querySelector("#drillLocation").innerHTML = entries.map(([key, value]) => `<div><dt>${escapeHtml(drillLocationNames[key] || key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("") || `<div><dt>位置</dt><dd>待资产系统补充</dd></div>`;
  const impact = run.impact_path || {};
  const nodes = impact.nodes || [];
  document.querySelector("#drillImpactPath").innerHTML = nodes.map((node, index) => `${index ? `<i aria-hidden="true">→</i>` : ""}<div data-node-type="${escapeHtml(node.type)}"><small>${escapeHtml(node.type)}</small><strong>${escapeHtml(node.label)}</strong></div>`).join("") || `<span>当前场景没有登记影响路径</span>`;
  document.querySelector("#drillImpactKind").textContent = `${drillImpactNames[impact.kind] || impact.kind || "影响路径待确认"}；只展示当前事故相关链路，不推测整座机房拓扑。`;
}

function renderDrillCheckpoint(run) {
  const panel = document.querySelector("#drillCheckpoint");
  const checkpoint = run.current_checkpoint;
  panel.hidden = !checkpoint;
  if (!checkpoint) return;
  document.querySelector("#drillCheckpointTitle").textContent = checkpoint.title;
  document.querySelector("#drillCheckpointPrompt").textContent = checkpoint.prompt;
  document.querySelector("#drillActionChoices").innerHTML = `<legend>选择实际执行结果</legend>${(checkpoint.actions || []).map((action, index) => `<label><input name="action_id" type="radio" value="${escapeHtml(action.id)}" ${index === 0 ? "checked" : ""} required><span>${escapeHtml(action.label)}</span></label>`).join("")}`;
}

function renderDrillResult(run) {
  const result = document.querySelector("#drillResult");
  result.hidden = !drillTerminal(run);
  if (!drillTerminal(run)) return;
  document.querySelector("#drillResultSummary").textContent = `${drillStatusNames[run.status] || run.status}。这是模拟演练评分，不代表生产准确率。`;
  const score = run.score || {};
  document.querySelector("#drillScore").innerHTML = `
    <div><dt>诊断是否命中</dt><dd>${score.diagnosis_match ? "命中" : "未命中/未完成"}</dd></div>
    <div><dt>平台信号</dt><dd>${escapeHtml(score.platform_signal_count ?? 0)} 条</dd></div>
    <div><dt>人工动作</dt><dd>${escapeHtml(score.human_action_count ?? 0)} 次</dd></div>
    <div><dt>危险自动动作</dt><dd>${escapeHtml(score.unsafe_action_count ?? 0)} 次</dd></div>`;
  const revealButton = result.querySelector('[data-action="reveal-drill-answer"]');
  const canReveal = state.role === "super_admin" || run.started_by === state.actor;
  revealButton.hidden = Boolean(run.hidden_truth);
  revealButton.disabled = !run.truth_reveal_available || !canReveal;
  revealButton.title = canReveal ? "结束后揭晓隐藏答案" : "只有本次发起人或最高管理员可以揭晓";
  const truth = document.querySelector("#drillTruth");
  truth.hidden = !run.hidden_truth;
  if (run.hidden_truth) truth.innerHTML = `<small>隐藏答案</small><strong>${escapeHtml(run.hidden_truth.label || run.hidden_truth.diagnosis)}</strong><p>故障部件：${escapeHtml(run.hidden_truth.component || "未登记")} · 责任专业：${escapeHtml(run.hidden_truth.owner_team || "待确认")}</p>`;
}

function renderDrillStageTrack(run) {
  const terminal = drillTerminal(run);
  const signalCount = (run.steps || []).filter((step) => step.step_type === "platform_signal").length;
  let current = 0;
  if (signalCount) current = 1;
  if ((run.incident_ids || []).length) current = 2;
  if (run.status === "waiting_human") current = 3;
  if (terminal) current = 4;
  const stages = [
    ["收到告警", "平台信号进入统一接口"],
    ["关联设备", "用 SN、设备名和端口关联"],
    ["AI 判断", "规则、知识与候选原因"],
    ["人工验证", "许可、测量或现场操作"],
    ["恢复确认", "监控、业务和人工结果"],
  ];
  document.querySelector("#drillStageTrack").innerHTML = stages.map(([title, note], index) => {
    const status = index < current ? "done" : index === current ? "current" : "upcoming";
    return `<li data-state="${status}"><span>${String(index + 1).padStart(2, "0")}</span><strong>${title}</strong><small>${note}</small></li>`;
  }).join("");
}

function renderActiveDrill() {
  const run = state.drills.active;
  document.querySelector("#drillEmptyState").hidden = Boolean(run);
  document.querySelector("#drillActive").hidden = !run;
  if (!run) return;
  document.querySelector("#drillRunMode").textContent = run.mode === "blind" ? "盲测" : "定向演练";
  document.querySelector("#drillRunStatus").textContent = drillStatusNames[run.status] || run.status;
  document.querySelector("#drillRunStatus").dataset.status = run.status;
  document.querySelector("#drillAnalysisMode").textContent = run.analysis_mode === "ai_enriched" ? "AI 增强分析" : "规则基线（未接模型）";
  document.querySelector("#drillRunName").textContent = run.scenario?.name || run.display_name;
  document.querySelector("#drillRunSymptom").textContent = run.scenario?.visible_symptom || "等待信号";
  document.querySelector("#drillLogicalTime").textContent = `T+${run.logical_time || 0}s`;
  document.querySelector("#drillIncidentCount").textContent = `${(run.incident_ids || []).length} 个关联事故`;
  const canAdvance = run.status === "running";
  document.querySelector('[data-action="drill-step"]').disabled = !canAdvance;
  document.querySelector('[data-action="drill-next-human"]').disabled = !canAdvance;
  document.querySelector('[data-action="terminate-drill"]').disabled = drillTerminal(run);
  renderDrillStageTrack(run);
  renderDrillLocation(run);
  renderDrillTimeline(run);
  renderDrillCheckpoint(run);
  renderDrillResult(run);
  renderDrillRuns();
}

function focusActiveDrillOnSmallScreen() {
  if (window.matchMedia("(max-width: 780px)").matches) {
    window.requestAnimationFrame(() => document.querySelector("#drillActive")?.scrollIntoView({ block: "start", behavior: "smooth" }));
  }
}

async function loadDrills() {
  state.drills.loadStatus = "loading";
  state.drills.loadError = "";
  renderDrillLoadState();
  try {
    const [catalog, runs] = await Promise.all([api("/api/drills/catalog"), api("/api/drills/runs?limit=100")]);
    state.drills.catalog = catalog;
    state.drills.runs = runs.items || [];
    state.drills.loadStatus = "ready";
    renderDrillCatalog();
    renderDrillRuns();
    if (state.drills.active?.id) {
      state.drills.active = await api(`/api/drills/runs/${encodeURIComponent(state.drills.active.id)}`);
      renderActiveDrill();
    }
  } catch (error) {
    state.drills.catalog = null;
    state.drills.loadStatus = "error";
    state.drills.loadError = error.message;
    renderDrillLoadState();
    document.querySelector("#drillCatalogCount").textContent = "加载失败";
    throw error;
  }
}

async function openDrills() {
  if (!["ai_admin", "super_admin"].includes(state.role)) {
    showToast(`当前是${roleNames[state.role]}工作台，故障演练只对管理员开放`, true);
    return;
  }
  openDialog(drillDialog);
  try {
    await loadDrills();
  } catch (error) {
    showToast(`演练台加载失败：${error.message}`, true);
  }
}

async function startDrill(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  const button = form.querySelector('button[type="submit"]');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在注入第一批信号…";
  try {
    state.drills.active = await api("/api/drills/runs", { method: "POST", body: JSON.stringify({ ...values, autostart: true }) });
    showToast("演练已启动；信号已经过统一接入和治理链");
    await loadDrills();
    focusActiveDrillOnSmallScreen();
    await loadIncidents(state.drills.active.incident_ids?.[0]);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function selectDrillRun(runId) {
  state.drills.active = await api(`/api/drills/runs/${encodeURIComponent(runId)}`);
  renderActiveDrill();
  focusActiveDrillOnSmallScreen();
}

async function advanceDrill(command) {
  if (!state.drills.active) return;
  state.drills.active = await api(`/api/drills/runs/${encodeURIComponent(state.drills.active.id)}/advance`, { method: "POST", body: JSON.stringify({ command }) });
  renderActiveDrill();
  await loadIncidents(state.drills.active.incident_ids?.[0]);
}

async function submitDrillFeedback(form) {
  if (!state.drills.active) return;
  const values = Object.fromEntries(new FormData(form).entries());
  const button = form.querySelector('button[type="submit"]');
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在记录并推进…";
  try {
    state.drills.active = await api(`/api/drills/runs/${encodeURIComponent(state.drills.active.id)}/feedback`, { method: "POST", body: JSON.stringify(values) });
    form.reset();
    renderActiveDrill();
    await loadDrills();
    await loadIncidents(state.drills.active.incident_ids?.[0]);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function terminateDrill() {
  if (!state.drills.active) return;
  const reason = window.prompt("请填写终止原因（会进入演练审计记录）", "本次验证到此结束");
  if (reason === null) return;
  state.drills.active = await api(`/api/drills/runs/${encodeURIComponent(state.drills.active.id)}/terminate`, { method: "POST", body: JSON.stringify({ reason }) });
  renderActiveDrill();
  await loadDrills();
}

async function revealDrillAnswer() {
  if (!state.drills.active) return;
  state.drills.active = await api(`/api/drills/runs/${encodeURIComponent(state.drills.active.id)}?reveal=1`);
  renderActiveDrill();
}

async function setLabPlatformState(button) {
  const key = button.dataset.platformKey;
  const select = document.querySelector(`[data-platform-state="${CSS.escape(key)}"]`);
  await api(`/api/lab/platforms/${encodeURIComponent(key)}/state`, {
    method: "POST",
    body: JSON.stringify({ state: select.value, latency_ms: select.value === "delayed" ? 800 : 0 }),
  });
  showToast(`${key} 已切换为${labConnectionNames[select.value]}`);
  await loadLab();
}

async function runLabScenario(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "逐条发送中…";
  try {
    const result = await api(`/api/lab/scenarios/${encodeURIComponent(button.dataset.scenarioId)}/run`, { method: "POST", body: "{}" });
    showToast(`场景已送入统一接口，形成 ${result.incident_ids?.length || 0} 个事故`);
    await loadIncidents(result.incident_ids?.[0]);
    await loadLab();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function submitLabEvent(form) {
  const payload = JSON.parse(new FormData(form).get("payload"));
  const result = await api("/api/lab/events", { method: "POST", body: JSON.stringify(payload) });
  showToast(result.duplicate ? "相同来源事件已存在，本次没有重复创建" : `信号已进入事故 ${result.incident?.id || "待形成"}`);
  await loadIncidents(result.incident?.id);
  await loadLab();
}

async function runAgentFromLab(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.max_rounds = Number(payload.max_rounds || 5);
  const run = await api("/api/agent/runs", { method: "POST", body: JSON.stringify(payload) });
  state.lab.selectedAgentRun = run;
  showToast(run.summary?.real_ai ? "真实 AI 只读调查已完成" : `${run.summary?.label || "调查运行"}已记录`);
  await loadLab();
  await selectAgentRun(run.id);
}

async function selectAgentRun(runId) {
  const run = await api(`/api/agent/runs/${encodeURIComponent(runId)}`);
  state.lab.selectedAgentRun = run;
  renderAgentRuns();
  renderAgentTrace(run);
  const rawId = document.querySelector('#rawAccessForm [name="record_id"]');
  if (rawId && state.role === "super_admin") rawId.value = run.id;
}

async function createBackup(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在备份并校验…";
  try {
    const backup = await api("/api/admin/backups", { method: "POST", body: "{}" });
    showToast(backup.status === "verified" ? "备份已创建并通过恢复校验" : "备份校验未通过", backup.status !== "verified");
    await loadLab();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function openRawAccess(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.confirmed = form.elements.confirmed.checked;
  const result = await api("/api/admin/raw-access", { method: "POST", body: JSON.stringify(payload) });
  document.querySelector("#rawAccessResult").textContent = jsonText(result);
  showToast(`原始记录已打开，审计编号 ${result.audit_id}`);
}

const governanceStateNames = {
  firing: "正在发生",
  acknowledged: "已确认",
  recovered: "恢复待验证",
  suppressed: "被上游抑制",
  silenced: "维护静默",
  expired: "已过期",
};

const assignmentStateNames = {
  assigned: "待确认",
  acknowledged: "已确认收到",
  deferred: "已说明延后",
  escalated: "已升级",
  reassigned: "已改派",
};

function canManageIncidentGovernance() {
  return ["interface_person", "ai_admin", "super_admin"].includes(state.role);
}

function canManageMaintenanceGovernance() {
  return ["facility_lead", "interface_person", "ai_admin", "super_admin"].includes(state.role);
}

function canManageTrustGovernance() {
  return ["ai_admin", "super_admin"].includes(state.role);
}

function applyGovernancePermissions() {
  document.querySelectorAll("[data-governance-permission]").forEach((element) => {
    const permission = element.dataset.governancePermission;
    const allowed = permission === "trust"
      ? canManageTrustGovernance()
      : permission === "maintenance"
        ? canManageMaintenanceGovernance()
        : canManageIncidentGovernance();
    element.hidden = !allowed;
  });
}

function activateGovernanceTab(name) {
  state.governance.tab = name;
  document.querySelectorAll("[data-governance-tab]").forEach((button) => {
    const active = button.dataset.governanceTab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll("[data-governance-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.governancePanel === name);
  });
}

function governanceEmpty(message) {
  return `<div class="admin-empty">${escapeHtml(message)}</div>`;
}

function renderGovernanceOverview() {
  const overview = state.governance.overview || {};
  document.querySelector("#gatePipeline").textContent = overview.pipeline_problems || 0;
  document.querySelector("#gateIdentity").textContent = overview.identity_conflicts || 0;
  document.querySelector("#gateNoise").textContent = (overview.suppressed_alerts || 0) + (overview.silenced_alerts || 0);
  document.querySelector("#gateActive").textContent = overview.active_alerts || 0;
  document.querySelector("#gateOwner").textContent = overview.unassigned_incidents || 0;
}

function renderGovernanceAlerts() {
  const target = document.querySelector("#governanceAlertList");
  if (!state.governance.alerts.length) {
    target.innerHTML = governanceEmpty("还没有经过治理入口的告警。接口人或管理员可发送测试告警，公开样本导入也会走同一个入口。");
    return;
  }
  target.innerHTML = state.governance.alerts.map((item) => {
    const quality = item.data_quality || {};
    const score = Math.max(0, Math.min(100, Number(quality.score ?? 0)));
    const incident = item.incident_id || "未形成事故";
    const reason = item.suppression_reason || (item.requires_service_validation ? "监控已恢复，仍需业务验证" : "保留原始治理记录");
    const canAcknowledge = canManageIncidentGovernance() && item.lifecycle_status === "firing";
    return `<article class="governance-alert-row">
      <div><small>${escapeHtml(item.source_system)}</small><strong>${escapeHtml(item.site || "未知机房")}</strong><span>${escapeHtml(formatTime(item.last_seen_at))}</span></div>
      <div><small>${escapeHtml(item.signal_type)}</small><strong title="${escapeHtml(item.summary)}">${escapeHtml(item.summary)}</strong><span>${escapeHtml(item.entity_key)} · 累计 ${escapeHtml(item.occurrence_count)} 次</span></div>
      <div><small>事故与处置</small><strong>${escapeHtml(incident)}</strong><span>${escapeHtml(reason)}</span></div>
      <div><small>数据质量 ${escapeHtml(score)}</small><div class="quality-meter" aria-label="数据质量 ${escapeHtml(score)} 分"><i style="width:${escapeHtml(score)}%"></i></div><span>${quality.operation_blocked ? "身份冲突，禁止操作" : "身份未被冲突阻断"}</span></div>
      <div><span class="alert-state" data-state="${escapeHtml(item.lifecycle_status)}">${escapeHtml(governanceStateNames[item.lifecycle_status] || item.lifecycle_status)}</span>${canAcknowledge ? `<button class="text-button" data-action="acknowledge-production-alert" data-alert-id="${escapeHtml(item.id)}" type="button">确认收到</button>` : ""}</div>
    </article>`;
  }).join("");
}

function renderMaintenanceWindows() {
  const target = document.querySelector("#maintenanceWindowList");
  target.innerHTML = state.governance.maintenance.length ? state.governance.maintenance.slice(0, 5).map((item) => `<div><strong>${escapeHtml(item.site || "全部机房")} · ${escapeHtml(item.reason)}</strong><span>${escapeHtml(formatTime(item.starts_at))}—${escapeHtml(formatTime(item.ends_at))}${item.entity_key ? ` · ${escapeHtml(item.entity_key)}` : ""}</span></div>`).join("") : `<span>当前没有已登记的维护窗口。</span>`;
}

function renderSourceHealth() {
  const target = document.querySelector("#sourceHealthList");
  if (!canManageTrustGovernance()) {
    target.innerHTML = governanceEmpty("当前角色只看治理结果；采集链路明细仅 AI 管理员和最高管理员可见。");
    return;
  }
  target.innerHTML = state.governance.sourceHealth.length ? state.governance.sourceHealth.map((item) => `
    <div class="trust-record"><strong>${escapeHtml(item.source_system)}</strong><b class="${item.pipeline_problem ? "is-problem" : ""}">${item.pipeline_problem ? "链路异常" : "正常"}</b><span>${escapeHtml(item.connection_status)} · 覆盖 ${escapeHtml(item.coverage_percent)}%</span><small>积压 ${escapeHtml(item.queue_depth)} · 丢弃 ${escapeHtml(item.dropped_count)} · 接收 ${escapeHtml(item.received_count)}</small></div>`).join("") : governanceEmpty("还没有采集链路状态。接入数据或在下方模拟一个采集器状态后会出现。");
}

function renderIdentityConflicts() {
  const target = document.querySelector("#identityConflictList");
  target.innerHTML = state.governance.identityConflicts.length ? state.governance.identityConflicts.map((item) => {
    const resolve = item.status === "open" && canManageTrustGovernance() ? `<div class="assignment-actions"><input class="conflict-resolution assignment-reason" aria-label="冲突核实结论" placeholder="例如：已查OMS并现场复扫，以OMS为准"><button data-action="resolve-identity-conflict" data-conflict-id="${escapeHtml(item.id)}" type="button">保存核实结论</button></div>` : "";
    return `<div class="trust-record"><strong>${escapeHtml(item.entity_key)}</strong><b class="${item.status === "open" ? "is-problem" : ""}">${item.status === "open" ? "待处理" : "已处理"}</b><span>${escapeHtml(item.field_name)} · ${escapeHtml(item.authoritative_source)} ≠ ${escapeHtml(item.conflicting_source)}</span><small>${escapeHtml(item.authoritative_value)} ↔ ${escapeHtml(item.conflicting_value)}${item.operation_blocked ? "；已阻止现场操作" : ""}</small>${resolve}</div>`;
  }).join("") : governanceEmpty("没有身份冲突。系统不会让模型猜测同一设备的 SN、机架位或端口。");
}

function renderChanges() {
  const target = document.querySelector("#changeEventList");
  target.innerHTML = state.governance.changes.length ? state.governance.changes.map((item) => `
    <div class="trust-record"><strong>${escapeHtml(item.summary)}</strong><b>${escapeHtml(item.causality === "candidate_only" ? "候选证据" : item.causality)}</b><span>${escapeHtml(item.site)} · ${escapeHtml(item.entity_key)}</span><small>${escapeHtml(item.change_type)} · ${escapeHtml(formatTime(item.changed_at))}；时间接近不等于已经证明因果</small></div>`).join("") : governanceEmpty("没有近期变更。发布、固件、端口和资产搬迁可以作为候选证据，但不会直接判为根因。");
}

function renderRosters() {
  const target = document.querySelector("#rosterList");
  target.innerHTML = state.governance.rosters.length ? state.governance.rosters.map((item) => `
    <div class="trust-record"><strong>${escapeHtml(item.person)}</strong><b>${escapeHtml(item.team)}</b><span>${escapeHtml(item.site)} · ${escapeHtml(formatTime(item.shift_start))}—${escapeHtml(formatTime(item.shift_end))}</span><small>升级负责人：${escapeHtml(item.escalation_person || "未设置")}</small></div>`).join("") : governanceEmpty("还没有值班记录。夜间单人值班时，可登记主值班人与升级负责人。");
}

function renderAssignments() {
  const target = document.querySelector("#assignmentList");
  target.innerHTML = state.governance.assignments.length ? state.governance.assignments.map((item) => {
    const isAssignee = item.assignee === state.actor || canManageTrustGovernance();
    const acknowledge = item.status === "assigned" && isAssignee ? `<button data-action="acknowledge-assignment" data-assignment-id="${escapeHtml(item.id)}" type="button">确认收到</button>` : "";
    const defer = ["assigned", "acknowledged"].includes(item.status) && isAssignee ? `<input class="assignment-reason" aria-label="延后原因" placeholder="先处理更紧急工单，预计稍后到场"><button data-action="defer-assignment" data-assignment-id="${escapeHtml(item.id)}" type="button">说明延后</button>` : "";
    const escalate = canManageMaintenanceGovernance() && !["escalated", "reassigned"].includes(item.status) ? `<button data-action="escalate-assignment" data-assignment-id="${escapeHtml(item.id)}" type="button">升级负责人</button>` : "";
    return `<div class="trust-record"><strong>${escapeHtml(item.incident_id)}</strong><b class="${item.status === "assigned" ? "is-problem" : ""}">${escapeHtml(assignmentStateNames[item.status] || item.status)}</b><span>${escapeHtml(item.assignee)} · ${escapeHtml(item.priority.toUpperCase())}</span><small>应答期限：${escapeHtml(formatTime(item.due_at))}${item.deferred_reason ? `；延后：${escapeHtml(item.deferred_reason)}` : ""}${!isAssignee && item.status === "assigned" ? "；请由被分派人本人确认" : ""}</small><div class="assignment-actions">${acknowledge}${defer}${escalate}</div></div>`;
  }).join("") : governanceEmpty("还没有事故分派。形成事故后可指定负责人和确认时限。");
}

function renderFeedback() {
  const target = document.querySelector("#feedbackList");
  if (!canManageIncidentGovernance()) {
    target.innerHTML = governanceEmpty("关联纠正记录仅接口人和管理员可见。");
    return;
  }
  const actionNames = { merge: "合并", split: "拆分", mark_unrelated: "标记无关", confirm_related: "确认有关" };
  target.innerHTML = state.governance.feedback.length ? state.governance.feedback.map((item) => `
    <div class="trust-record"><strong>${escapeHtml(actionNames[item.action] || item.action)}</strong><b>${escapeHtml(item.created_by)}</b><span>${escapeHtml(item.alert_id || item.incident_id || "关联对象")}</span><small>${escapeHtml(item.reason)} · ${escapeHtml(formatTime(item.created_at))}</small></div>`).join("") : governanceEmpty("还没有人工纠正。每次拆分、合并或标记无关都保留原记录，用于改进后续关联。");
}

function renderPublicDatasets() {
  const target = document.querySelector("#publicDatasetGrid");
  target.innerHTML = state.governance.datasets.map((item) => {
    const imported = item.last_import;
    const actionText = item.format === "runtime_generator" ? "查看运行要求" : item.sample_url ? "下载官方轻量样本并测试" : "等待本地文件导入";
    const disabled = !canManageTrustGovernance() || (!item.sample_url && item.format !== "runtime_generator");
    return `<article class="dataset-card"><header><h4>${escapeHtml(item.name)}</h4><span>${escapeHtml(item.data_type)}</span></header><p>${escapeHtml(item.usage)}</p><div class="dataset-meta"><div><small>数据真实性</small><strong>${escapeHtml(item.truth_level)}</strong></div><div><small>许可边界</small><strong>${escapeHtml(item.license_summary)}</strong></div><div><small>保存方式</small><strong>${escapeHtml(item.distribution_policy)}</strong></div></div><footer><span>${imported ? `最近：${escapeHtml(imported.status)} · ${escapeHtml(imported.record_count)} 条` : escapeHtml(item.ready_action)}</span><div><a class="text-button" href="${escapeHtml(item.project_url)}" target="_blank" rel="noopener noreferrer">项目资料</a><button class="secondary-button" data-action="import-public-dataset" data-dataset-id="${escapeHtml(item.id)}" type="button" ${disabled ? "disabled" : ""}>${escapeHtml(actionText)}</button></div></footer></article>`;
  }).join("") || governanceEmpty("公开数据目录暂不可用。");
}

function renderGovernance() {
  renderGovernanceOverview();
  renderGovernanceAlerts();
  renderMaintenanceWindows();
  renderSourceHealth();
  renderIdentityConflicts();
  renderChanges();
  renderRosters();
  renderAssignments();
  renderFeedback();
  renderPublicDatasets();
  applyGovernancePermissions();
}

async function loadGovernance() {
  const requests = [
    api("/api/production/overview"),
    api("/api/production/alerts?limit=100"),
    api("/api/production/maintenance"),
    api("/api/production/identity-conflicts"),
    api("/api/production/changes?limit=50"),
    api("/api/production/rosters"),
    api("/api/production/assignments"),
    api("/api/public-datasets"),
  ];
  if (canManageTrustGovernance()) requests.push(api("/api/production/source-health"));
  if (canManageIncidentGovernance()) requests.push(api("/api/production/feedback"));
  const values = await Promise.all(requests);
  state.governance.overview = values[0];
  state.governance.alerts = values[1]?.items || [];
  state.governance.maintenance = values[2]?.items || [];
  state.governance.identityConflicts = values[3]?.items || [];
  state.governance.changes = values[4]?.items || [];
  state.governance.rosters = values[5]?.items || [];
  state.governance.assignments = values[6]?.items || [];
  state.governance.datasets = values[7]?.items || [];
  let cursor = 8;
  state.governance.sourceHealth = canManageTrustGovernance() ? (values[cursor++]?.items || []) : [];
  state.governance.feedback = canManageIncidentGovernance() ? (values[cursor]?.items || []) : [];
  renderGovernance();
}

async function openGovernance() {
  openDialog(governanceDialog);
  activateGovernanceTab(state.governance.tab);
  try {
    await loadGovernance();
  } catch (error) {
    showToast(`治理数据加载失败：${error.message}`, true);
  }
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function asIso(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("请填写有效时间");
  return date.toISOString();
}

async function submitGovernanceForm(form) {
  let path = "";
  let payload = {};
  let success = "已保存";
  if (form.id === "productionAlertForm") {
    try { payload = JSON.parse(form.elements.payload.value); } catch (_error) { throw new Error("告警 JSON 格式不正确"); }
    path = "/api/production/alerts";
    success = "告警已经过身份、降噪和事故闸门";
  } else {
    payload = formObject(form);
    const settings = {
      maintenanceWindowForm: ["/api/production/maintenance", "维护窗口已登记"],
      sourceHealthForm: ["/api/production/source-health", "采集链路状态已更新"],
      identityAssertionForm: ["/api/production/identities", "资产字段来源已记录并完成冲突检查"],
      changeEventForm: ["/api/production/changes", "变更已保存为候选证据"],
      rosterForm: ["/api/production/rosters", "值班记录已保存"],
      assignmentForm: ["/api/production/assignments", "事故已分派并开始计时"],
      correlationFeedbackForm: ["/api/production/feedback", "人工纠正已保存，原关联记录没有删除"],
    };
    [path, success] = settings[form.id] || [];
    if (!path) return;
    if (form.id === "maintenanceWindowForm") {
      payload.starts_at = asIso(payload.starts_at);
      payload.ends_at = asIso(payload.ends_at);
    }
    if (form.id === "rosterForm") {
      payload.shift_start = asIso(payload.shift_start);
      payload.shift_end = asIso(payload.shift_end);
    }
    if (form.id === "sourceHealthForm") {
      for (const key of ["expected_entities", "reporting_entities", "queue_depth", "dropped_count"]) payload[key] = Number(payload[key] || 0);
    }
  }
  const result = await api(path, { method: "POST", body: JSON.stringify(payload) });
  const decision = result.decision ? `：${result.decision}` : "";
  showToast(`${success}${decision}`);
  await loadGovernance();
  if (result.incident_created || result.incident?.id) await loadIncidents(result.incident?.id);
}

async function acknowledgeProductionAlert(button) {
  await api(`/api/production/alerts/${encodeURIComponent(button.dataset.alertId)}/acknowledge`, { method: "POST", body: "{}" });
  showToast("已确认收到告警；这不等于故障已经恢复");
  await loadGovernance();
}

async function resolveIdentityConflict(button) {
  const resolution = button.closest(".assignment-actions")?.querySelector(".conflict-resolution")?.value.trim() || "";
  if (!resolution) throw new Error("处理身份冲突必须填写核实来源和结论");
  await api(`/api/production/identity-conflicts/${encodeURIComponent(button.dataset.conflictId)}/resolve`, { method: "POST", body: JSON.stringify({ resolution }) });
  showToast("身份冲突已记录为处理完成；原始两条来源仍保留");
  await loadGovernance();
}

async function updateAssignment(button, action) {
  const assignmentId = button.dataset.assignmentId;
  const reason = button.closest(".assignment-actions")?.querySelector(".assignment-reason")?.value.trim() || "";
  if (action === "defer" && !reason) throw new Error("延后必须填写原因和当前优先事项");
  const payload = action === "escalate" ? { escalated_to: "facility-lead-on-duty" } : action === "defer" ? { reason } : {};
  await api(`/api/production/assignments/${encodeURIComponent(assignmentId)}/${action}`, { method: "POST", body: JSON.stringify(payload) });
  showToast(action === "acknowledge" ? "已确认收到分派" : action === "defer" ? "已记录延后原因；责任仍保留在当前值班人" : "已升级给值班负责人");
  await loadGovernance();
}

async function importPublicDataset(button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "正在获取并测试…";
  try {
    const result = await api(`/api/public-datasets/${encodeURIComponent(button.dataset.datasetId)}/import-sample`, { method: "POST", body: "{}" });
    if (result.status === "requires_runtime" || result.status === "requires_manual_import") {
      showToast(result.message);
    } else {
      showToast(`公开样本测试完成：读取 ${result.record_count} 条，形成 ${result.alert_count} 条治理告警${result.error_count ? `，${result.error_count} 条错误` : ""}`, result.status !== "completed");
    }
    await loadGovernance();
    await loadIncidents();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function closeCamera() {
  await window.IDCAIDeviceScan?.stopScan();
  closeDialog(cameraDialog);
}

document.addEventListener("click", (event) => {
  const incidentButton = event.target.closest("[data-incident-id]");
  if (incidentButton) selectIncident(incidentButton.dataset.incidentId);

  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "show-incidents") showIncidentList();
  if (action === "previous-incident") selectAdjacentIncident(-1).catch((error) => showToast(error.message, true));
  if (action === "next-incident") selectAdjacentIncident(1).catch((error) => showToast(error.message, true));
  if (action === "clear-incident-filters") {
    state.filter = "active";
    state.query = "";
    state.categoryFilter = "all";
    state.severityFilter = "all";
    document.querySelector("#searchInput").value = "";
    document.querySelector("#categoryFilter").value = "all";
    document.querySelector("#severityFilter").value = "all";
    document.querySelectorAll("[data-filter]").forEach((button) => button.classList.toggle("is-active", button.dataset.filter === "active"));
    renderList();
  }
  if (action === "open-ingest") openDialog(ingestDialog);
  if (action === "open-demos") openDialog(demoDialog);
  if (action === "open-sources") {
    openDialog(sourceDialog);
    loadSources(false).catch((error) => showToast(error.message, true));
  }
  if (action === "open-facilities") {
    openDialog(facilityDialog);
    loadFacilities().catch((error) => showToast(error.message, true));
  }
  if (action === "open-admin") openAdmin();
  if (action === "open-lab") openLab();
  if (action === "open-drills") openDrills();
  if (action === "retry-drills") loadDrills().then(() => showToast("故障演练库已重新加载")).catch((error) => showToast(`重新加载失败：${error.message}`, true));
  if (action === "refresh-drills") loadDrills().then(() => showToast("演练记录已刷新")).catch((error) => showToast(error.message, true));
  if (action === "new-drill") {
    state.drills.active = null;
    renderActiveDrill();
    renderDrillCatalog();
  }
  if (action === "drill-step") advanceDrill("step").catch((error) => showToast(error.message, true));
  if (action === "drill-next-human") advanceDrill("next_human").catch((error) => showToast(error.message, true));
  if (action === "terminate-drill") terminateDrill().catch((error) => showToast(error.message, true));
  if (action === "reveal-drill-answer") revealDrillAnswer().catch((error) => showToast(error.message, true));
  if (action === "open-governance") openGovernance();
  if (action === "refresh-governance") loadGovernance().then(() => showToast("治理结果已刷新")).catch((error) => showToast(error.message, true));
  if (action === "acknowledge-production-alert") acknowledgeProductionAlert(event.target.closest("[data-action]")).catch((error) => showToast(error.message, true));
  if (action === "resolve-identity-conflict") resolveIdentityConflict(event.target.closest("[data-action]")).catch((error) => showToast(error.message, true));
  if (action === "acknowledge-assignment") updateAssignment(event.target.closest("[data-action]"), "acknowledge").catch((error) => showToast(error.message, true));
  if (action === "defer-assignment") updateAssignment(event.target.closest("[data-action]"), "defer").catch((error) => showToast(error.message, true));
  if (action === "escalate-assignment") updateAssignment(event.target.closest("[data-action]"), "escalate").catch((error) => showToast(error.message, true));
  if (action === "import-public-dataset") importPublicDataset(event.target.closest("[data-action]")).catch((error) => showToast(`公开样本测试失败：${error.message}`, true));
  if (action === "open-operations") openOperations().catch((error) => showToast(error.message, true));
  if (action === "refresh-lab") loadLab().catch((error) => showToast(error.message, true));
  if (action === "set-platform-state") setLabPlatformState(event.target.closest("[data-action]")).catch((error) => showToast(error.message, true));
  if (action === "run-lab-scenario") runLabScenario(event.target.closest("[data-action]")).catch((error) => showToast(error.message, true));
  if (action === "seed-topology") api("/api/lab/topology/seed", { method: "POST", body: "{}" }).then(loadLab).then(() => showToast("内置测试拓扑已恢复")).catch((error) => showToast(error.message, true));
  if (action === "create-backup") createBackup(event.target.closest("[data-action]")).catch((error) => showToast(error.message, true));
  if (action === "start-operation") startOperation(event.target.closest("[data-action]").dataset.operationId).catch((error) => showToast(error.message, true));
  if (action === "scan-sn") scanOperationSN();
  if (action === "close-camera") closeCamera();
  if (action === "load-admin-records") loadAdminRecords().catch((error) => showToast(error.message, true));
  if (action === "load-rag-runs") loadRagRuns().catch((error) => showToast(error.message, true));
  if (action === "load-releases") loadReleases().catch((error) => showToast(error.message, true));
  if (action === "load-providers") loadProviders().catch((error) => showToast(error.message, true));
  if (action === "preview-prompt") previewPrompt(event.target.closest("[data-action]")).catch((error) => showToast(error.message, true));
  if (action === "test-asset") testAsset(event.target.closest("[data-action]")).catch((error) => showToast(error.message, true));
  if (action === "prepare-release") prepareRelease(event.target.closest("[data-action]").dataset.releaseId).catch((error) => showToast(error.message, true));
  if (action === "confirm-release") askPublishConfirmation(event.target.closest("[data-action]").dataset.releaseId);
  if (action === "rollback-release") rollbackRelease(event.target.closest("[data-action]").dataset.releaseId).catch((error) => showToast(error.message, true));
  if (action === "check-sources") {
    const button = event.target.closest("[data-action]");
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "正在检查…";
    loadSources(true)
      .then(() => showToast("连接状态已重新检查"))
      .catch((error) => showToast(error.message, true))
      .finally(() => { button.disabled = false; button.textContent = original; });
  }
  if (action === "run-investigation") runInvestigation(event.target.closest("[data-action]"));
  if (action === "copy-summary") {
    const text = state.selected?.communication_text || "";
    navigator.clipboard.writeText(text).then(() => showToast("沟通摘要已复制"));
  }

  const status = event.target.closest("[data-status]")?.dataset.status;
  if (status) updateStatus(status);

  const closeButton = event.target.closest("[data-close-dialog]");
  if (closeButton) closeDialog(closeButton.closest("dialog"));

  const demoButton = event.target.closest("[data-demo-id]");
  if (demoButton) runDemo(demoButton.dataset.demoId, demoButton);

  const filterButton = event.target.closest("[data-filter]");
  if (filterButton) {
    state.filter = filterButton.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((button) => button.classList.toggle("is-active", button === filterButton));
    renderList();
  }

  const detailTab = event.target.closest("[data-detail-tab]")?.dataset.detailTab;
  if (detailTab) activateDetailTab(detailTab, false);

  const adminTab = event.target.closest("[data-admin-tab]")?.dataset.adminTab;
  if (adminTab) activateAdminTab(adminTab);

  const labTab = event.target.closest("[data-lab-tab]")?.dataset.labTab;
  if (labTab) activateLabTab(labTab);

  const governanceTab = event.target.closest("[data-governance-tab]")?.dataset.governanceTab;
  if (governanceTab) activateGovernanceTab(governanceTab);

  const agentRunButton = event.target.closest("[data-agent-run-id]");
  if (agentRunButton) selectAgentRun(agentRunButton.dataset.agentRunId).catch((error) => showToast(error.message, true));

  const drillScenarioButton = event.target.closest("[data-drill-scenario-id]");
  if (drillScenarioButton) {
    state.drills.selectedScenarioId = drillScenarioButton.dataset.drillScenarioId;
    renderDrillCatalog();
  }

  const drillCategoryButton = event.target.closest("[data-drill-category]");
  if (drillCategoryButton) {
    state.drills.category = drillCategoryButton.dataset.drillCategory;
    state.drills.selectedScenarioId = "";
    renderDrillCatalog();
  }

  const drillRunButton = event.target.closest("[data-drill-run-id]");
  if (drillRunButton) selectDrillRun(drillRunButton.dataset.drillRunId).catch((error) => showToast(error.message, true));

  const knowledgeButton = event.target.closest(".asset-row[data-knowledge-id]");
  if (knowledgeButton) selectKnowledge(knowledgeButton.dataset.knowledgeId).catch((error) => showToast(error.message, true));

  const promptButton = event.target.closest(".asset-row[data-prompt-key]");
  if (promptButton) selectPrompt(promptButton.dataset.promptKey).catch((error) => showToast(error.message, true));

  const ragButton = event.target.closest(".rag-run-row[data-rag-run-id]");
  if (ragButton) selectRagRun(ragButton.dataset.ragRunId).catch((error) => showToast(error.message, true));

  const operationButton = event.target.closest(".operation-row[data-operation-id]");
  if (operationButton) selectOperation(operationButton.dataset.operationId).catch((error) => showToast(error.message, true));

  const providerButton = event.target.closest(".provider-row[data-provider-key]");
  if (providerButton) selectProvider(providerButton.dataset.providerKey).catch((error) => showToast(error.message, true));
});

document.addEventListener("submit", (event) => {
  if (event.target.matches("#drillStartForm")) {
    event.preventDefault();
    startDrill(event.target).catch((error) => showToast(`演练启动失败：${error.message}`, true));
    return;
  }
  if (event.target.matches("#drillFeedbackForm")) {
    event.preventDefault();
    submitDrillFeedback(event.target).catch((error) => showToast(`反馈提交失败：${error.message}`, true));
    return;
  }
  const governanceForm = event.target.closest("#productionAlertForm, #maintenanceWindowForm, #sourceHealthForm, #identityAssertionForm, #changeEventForm, #rosterForm, #assignmentForm, #correlationFeedbackForm");
  if (governanceForm) {
    event.preventDefault();
    submitGovernanceForm(governanceForm).catch((error) => showToast(error.message, true));
    return;
  }
  const annotationForm = event.target.closest(".annotation-form");
  if (annotationForm) {
    event.preventDefault();
    saveAnnotation(annotationForm).catch((error) => showToast(error.message, true));
    return;
  }
  if (event.target.matches("#knowledgeDraftForm")) {
    event.preventDefault();
    createKnowledgeDraft(event.target).catch((error) => showToast(`知识草稿保存失败：${error.message}`, true));
    return;
  }
  if (event.target.matches("#promptDraftForm")) {
    event.preventDefault();
    createPromptDraft(event.target).catch((error) => showToast(`提示词草稿保存失败：${error.message}`, true));
    return;
  }
  if (event.target.matches("#workOrderImportForm")) {
    event.preventDefault();
    importWorkOrder(event.target).catch((error) => showToast(`工单快照保存失败：${error.message}`, true));
    return;
  }
  const operationForm = event.target.closest(".operation-action-form");
  if (operationForm) {
    event.preventDefault();
    submitOperationAction(operationForm).catch((error) => showToast(error.message, true));
    return;
  }
  if (event.target.matches("#providerForm")) {
    event.preventDefault();
    saveProvider(event.target).catch((error) => showToast(`适配配置保存失败：${error.message}`, true));
    return;
  }
  if (event.target.matches("#labEventForm")) {
    event.preventDefault();
    submitLabEvent(event.target).catch((error) => showToast(`平台信号发送失败：${error.message}`, true));
    return;
  }
  if (event.target.matches("#agentRunForm")) {
    event.preventDefault();
    runAgentFromLab(event.target).catch((error) => showToast(`AI 调查启动失败：${error.message}`, true));
    return;
  }
  if (event.target.matches("#rawAccessForm")) {
    event.preventDefault();
    openRawAccess(event.target).catch((error) => showToast(`原始记录访问失败：${error.message}`, true));
  }
});

document.querySelector("#refreshButton").addEventListener("click", () => loadIncidents());
document.querySelector("#searchInput").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderList();
});
document.querySelector("#categoryFilter").addEventListener("change", (event) => {
  state.categoryFilter = event.target.value;
  renderList();
});
document.querySelector("#severityFilter").addEventListener("change", (event) => {
  state.severityFilter = event.target.value;
  renderList();
});
document.querySelector("#incidentList").addEventListener("scroll", (event) => {
  if (state.view === "list") state.listScrollTop = event.currentTarget.scrollTop;
}, { passive: true });
document.querySelector("#roleSelect").addEventListener("change", (event) => setRole(event.target.value, false));
document.querySelector("#userIdentity").addEventListener("change", (event) => {
  state.actor = event.target.value.trim() || defaultActorForRole(state.role);
  event.target.value = state.actor;
  localStorage.setItem("idcai-actor", state.actor);
  showToast(`当前操作账号已设为 ${state.actor}`);
  if (operationDialog.open) {
    renderOperationSession();
    if (state.selectedOperation) renderOperationDetail(state.selectedOperation);
  }
  if (governanceDialog.open) loadGovernance().catch((error) => showToast(error.message, true));
});
document.querySelector("#knowledgeSearch").addEventListener("input", renderKnowledgeList);
document.querySelector("#recordSearch").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadAdminRecords().catch((error) => showToast(error.message, true));
  }
});
document.querySelector("#recordTypeSelect").addEventListener("change", () => loadAdminRecords().catch((error) => showToast(error.message, true)));
document.querySelector("#drillStartForm").addEventListener("change", (event) => {
  if (event.target.name !== "mode") return;
  const blind = event.target.value === "blind";
  document.querySelector("#drillModeNote").textContent = blind
    ? "盲测只公开现象、信号和影响路径；隐藏答案在结束前与运行链物理隔离。"
    : "定向演练会显示故障名称；运行过程仍由真实模拟信号触发。";
  renderDrillCatalog();
});

document.querySelectorAll(".dialog-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const name = tab.dataset.tab;
    document.querySelectorAll(".dialog-tab").forEach((item) => item.classList.toggle("is-active", item === tab));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("is-active", panel.dataset.panel === name));
  });
});

document.querySelector("#logFile").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    showToast("文件超过 2MB，请截取与故障相关的日志", true);
    event.target.value = "";
    return;
  }
  document.querySelector("#logText").value = await file.text();
  showToast(`已读取 ${file.name}`);
});

document.querySelector("#logForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, "log");
});
document.querySelector("#onsiteForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, "onsite");
});
document.querySelector("#alertForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, "alert");
});
document.querySelector("#facilityForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveFacility(event.currentTarget);
});

document.querySelector("#publishConfirmForm").addEventListener("submit", (event) => {
  const submitter = event.submitter?.value;
  if (submitter !== "confirm") return;
  event.preventDefault();
  if (!document.querySelector("#confirmOnlineCheck").checked) {
    showToast("请先勾选线上环境确认", true);
    document.querySelector("#confirmOnlineCheck").focus();
    return;
  }
  const releaseId = state.admin.pendingPublishId;
  closeDialog(publishConfirmDialog);
  publishRelease(releaseId).catch((error) => showToast(error.message, true));
});

for (const dialog of [ingestDialog, demoDialog, sourceDialog, facilityDialog, adminDialog, publishConfirmDialog, operationDialog, labDialog, governanceDialog, drillDialog]) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog(dialog);
  });
}

window.addEventListener("popstate", () => {
  const match = window.location.hash.match(/^#incident=(.+)$/);
  if (match) {
    selectIncident(decodeURIComponent(match[1]), false, false).catch((error) => showToast(error.message, true));
  } else {
    showIncidentList(false);
  }
});

setRole(state.role, true);
loadDemos();
loadSources(false).catch(() => {});
loadFacilities().catch(() => {});
const initialIncidentMatch = window.location.hash.match(/^#incident=(.+)$/);
if (initialIncidentMatch) state.view = "detail";
else window.history.replaceState({ view: "list" }, "", historyUrlFor("list"));
loadIncidents(initialIncidentMatch ? decodeURIComponent(initialIncidentMatch[1]) : null, false);
