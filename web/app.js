const state = {
  incidents: [],
  selectedId: null,
  selected: null,
  sources: [],
  facilities: [],
  filter: "all",
  query: "",
  role: localStorage.getItem("idcai-role") || "ai_admin",
  admin: {
    tab: "database",
    summary: null,
    knowledge: [],
    prompts: [],
    releases: [],
    ragRuns: [],
    selectedKnowledge: null,
    selectedPrompt: null,
    selectedRagRun: null,
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
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-IDCAI-Role": state.role,
      "X-IDCAI-User": "local-admin",
      ...(options.headers || {}),
    },
  });
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

function filteredIncidents() {
  const query = state.query.trim().toLowerCase();
  return state.incidents.filter((incident) => {
    if (state.filter !== "all" && incident.status !== state.filter) return false;
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

function renderCounts(counts = {}) {
  const all = (counts.new || 0) + (counts.processing || 0) + (counts.resolved || 0);
  document.querySelector("#countAll").textContent = all;
  document.querySelector("#countNew").textContent = counts.new || 0;
  document.querySelector("#countProcessing").textContent = counts.processing || 0;
  document.querySelector("#countResolved").textContent = counts.resolved || 0;
}

function renderList() {
  const items = filteredIncidents();
  if (!items.length) {
    listEl.innerHTML = `<div class="list-empty">当前筛选下没有事件。<br>可以分析真实故障或查看模拟案例。</div>`;
    return;
  }
  listEl.innerHTML = items.map((incident) => {
    const device = incident.devices?.[0] || {};
    const position = device.rack_position || "位置待补充";
    return `
      <button class="incident-row ${incident.id === state.selectedId ? "is-selected" : ""}"
        data-incident-id="${escapeHtml(incident.id)}" data-severity="${escapeHtml(incident.severity)}" type="button">
        <div class="row-meta">
          <span class="row-site">${escapeHtml(incident.site || "SITE?")}</span>
          <span class="row-time">${escapeHtml(formatTime(incident.updated_at))}</span>
        </div>
        <h3>${escapeHtml(incident.title)}</h3>
        <p class="row-device">${escapeHtml(primaryIdentity(incident))}<br>${escapeHtml(position)}</p>
        <div class="row-footer">
          <span>${escapeHtml(translated("category", incident.category))} · ${incident.affected_count || 0}台</span>
          <span class="row-status">${escapeHtml(translated("status", incident.status))}</span>
        </div>
      </button>`;
  }).join("");
}

function identityStrip(device) {
  return `
    <div class="identity-strip">
      <div class="identity-field"><small>完整 SN</small><strong>${escapeHtml(device.sn || "待补充")}</strong></div>
      <div class="identity-field"><small>机架位</small><strong>${escapeHtml(device.rack_position || "待补充")}</strong></div>
      <div class="identity-field"><small>设备名</small><strong>${escapeHtml(device.name || "待补充")}</strong></div>
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

function renderDetail(incident) {
  if (!incident) return;
  const investigation = incident.investigation || {};
  const conclusion = investigation.conclusion || {};
  const device = incident.devices?.[0] || {};
  const moreDevices = (incident.devices || []).slice(1);
  document.querySelector("#workspaceKicker").textContent = incident.id;
  document.querySelector("#workspaceTitle").textContent = `${incident.site || "机房待确认"} · ${incident.title}`;
  detailEl.innerHTML = `
    <article class="incident-layout">
      <header class="incident-heading">
        <div>
          <p class="eyebrow">AUDITABLE INCIDENT INVESTIGATION</p>
          <h2>${escapeHtml(incident.title)}</h2>
          <p class="incident-summary">${escapeHtml(incident.summary)}</p>
        </div>
        <div class="incident-tags">
          <span class="severity-chip" data-value="${escapeHtml(incident.severity)}">${escapeHtml(translated("severity", incident.severity))}</span>
          <span class="category-chip">${escapeHtml(translated("category", incident.category))}</span>
          <span class="status-chip">${escapeHtml(translated("status", incident.status))}</span>
        </div>
      </header>

      ${identityStrip(device)}
      ${moreDevices.length ? `<p class="multi-device-note">事件中还有 ${moreDevices.length} 台设备：${moreDevices.map((item) => escapeHtml(item.sn || item.name || item.rack_position)).join("、")}。这不代表系统已经证明它们具有相同根因，请查看下方“事件关联”。</p>` : ""}

      ${dataPath(investigation, incident)}

      <aside class="capability-banner" data-mode="${escapeHtml(investigation.mode)}">
        <div class="capability-mode"><span>当前分析模式</span><strong>${escapeHtml(modeLabel(investigation.mode))}</strong></div>
        <p>${escapeHtml(investigation.capability_notice || "当前能力状态未知")}</p>
        ${investigation.simulation ? `<span class="simulation-badge">模拟数据 · 不代表真实设备状态</span>` : `<span class="live-input-badge">外部/人工输入 · 尚未独立核验</span>`}
      </aside>

      ${facilityAssessmentCard(incident.analysis?.facility_assessment)}

      ${incident.cc_reminder?.required ? `
        <aside class="cc-alert" role="alert">
          <span class="cc-mark">CC</span>
          <div><strong>${escapeHtml(incident.cc_reminder.message)}</strong><p>${escapeHtml(incident.cc_reminder.reason || "输入已明确标记需要通报")}</p></div>
        </aside>` : ""}

      <section class="conclusion-board" data-grade="${escapeHtml(conclusion.grade)}">
        <div class="conclusion-grade"><small>当前结论等级</small><strong>${escapeHtml(statusLabel(conclusion.grade))}</strong></div>
        <div class="conclusion-main"><small>目前最需要验证的候选</small><h3>${escapeHtml(conclusion.leading_hypothesis || "证据不足")}</h3><p>${escapeHtml(conclusion.uncertainty || "尚未说明不确定性")}</p></div>
        <div class="next-check"><small>下一项建议检查</small><strong>${escapeHtml(conclusion.next_step || "补充更多证据")}</strong><span>只读优先 · 结果回来后重新排序候选</span></div>
      </section>

      <section class="investigation-section">
        <div class="section-heading investigation-heading"><div><p class="eyebrow">EVIDENCE ROUTE</p><h3>数据怎样一步步变成当前判断</h3></div><small>点击每一步查看原文、依据和限制</small></div>
        ${traceLine(investigation)}
      </section>

      <div class="detail-grid lower-grid">
        <div class="detail-column">
          <section class="section-block">
            <div class="section-heading"><h3>原始证据</h3><small>${(investigation.evidence || []).length} 条</small></div>
            ${evidenceList(investigation.evidence)}
          </section>
          <section class="section-block">
            <div class="section-heading"><h3>接口沟通摘要</h3><small>只使用已保存结果</small></div>
            <div class="communication-box" id="communicationText">${escapeHtml(incident.communication_text)}</div>
            <div class="copy-row"><button class="secondary-button" data-action="copy-summary" type="button">复制摘要</button></div>
          </section>
        </div>
        <div class="detail-column">
          ${onsiteCard(incident.onsite_card)}
          <section class="section-block">
            <div class="section-heading"><h3>处理状态</h3><small>${escapeHtml(formatTime(incident.updated_at))}</small></div>
            <div class="status-actions">
              <button class="secondary-button" data-status="new" ${incident.status === "new" ? "disabled" : ""} type="button">新发现</button>
              <button class="secondary-button" data-status="processing" ${incident.status === "processing" ? "disabled" : ""} type="button">开始处理</button>
              <button class="primary-button" data-status="resolved" ${incident.status === "resolved" ? "disabled" : ""} type="button">标记解决</button>
            </div>
          </section>
          <section class="section-block">
            <div class="section-heading"><h3>调查输入</h3><small>${(investigation.intake || []).length} 次输入</small></div>
            ${intakeList(investigation.intake)}
          </section>
        </div>
      </div>
    </article>`;
}

async function loadIncidents(preferredId = null) {
  try {
    const payload = await api("/api/incidents");
    state.incidents = payload.items || [];
    renderCounts(payload.counts);
    const targetId = preferredId || state.selectedId || state.incidents[0]?.id;
    renderList();
    if (targetId && state.incidents.some((item) => item.id === targetId)) {
      await selectIncident(targetId, false);
    }
  } catch (error) {
    document.querySelector("#systemPulse").classList.add("is-offline");
    showToast(error.message, true);
  }
}

async function selectIncident(incidentId, rerenderList = true) {
  try {
    state.selectedId = incidentId;
    state.selected = await api(`/api/incidents/${encodeURIComponent(incidentId)}`);
    if (rerenderList) renderList();
    renderDetail(state.selected);
  } catch (error) {
    showToast(error.message, true);
  }
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
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closeDialog(dialog) {
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
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
  interface_engineer: "系统/网络接口人",
  ai_admin: "AI 管理员",
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

function setRole(role) {
  state.role = roleNames[role] ? role : "onsite_operator";
  localStorage.setItem("idcai-role", state.role);
  document.body.dataset.role = state.role;
  document.querySelector("#roleSelect").value = state.role;
  const adminButton = document.querySelector("#adminRailButton");
  const isAdmin = state.role === "ai_admin";
  adminButton.classList.toggle("is-locked", !isAdmin);
  adminButton.setAttribute("aria-description", isAdmin ? "打开数据与 AI 管理" : "仅 AI 管理员可以打开");
  showToast(`已切换到${roleNames[state.role]}工作台`);
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
  if (name === "releases") loadReleases().catch((error) => showToast(error.message, true));
}

async function openAdmin() {
  if (state.role !== "ai_admin") {
    showToast(`当前是${roleNames[state.role]}工作台，只有 AI 管理员能修改数据与 AI 资产`, true);
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
  showToast(`版本 ${release.version} 已通过发布前检查`);
  await loadReleases();
  activateAdminTab("releases");
}

function releaseStatusName(status) {
  return { tested: "测试通过", prepared: "等待最终确认", published: "已上线", rolled_back: "已回滚" }[status] || status;
}

async function loadReleases() {
  const payload = await api("/api/admin/releases");
  state.admin.releases = payload.items || [];
  document.querySelector("#releaseList").innerHTML = state.admin.releases.length ? state.admin.releases.map((item) => `
    <article class="release-row" data-release-status="${escapeHtml(item.status)}">
      <div><small>${escapeHtml(item.asset_type)} · ${escapeHtml(item.asset_key)}</small><strong>${escapeHtml(item.version)}</strong><span>${escapeHtml(item.id)} · ${escapeHtml(formatTime(item.created_at))}</span></div>
      <div class="release-checks">${(item.test_summary || []).map((check) => `<span data-passed="${check.passed ? "yes" : "no"}">${check.passed ? "✓" : "×"} ${escapeHtml(check.name)}</span>`).join("")}</div>
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

document.addEventListener("click", (event) => {
  const incidentButton = event.target.closest("[data-incident-id]");
  if (incidentButton) selectIncident(incidentButton.dataset.incidentId);

  const action = event.target.closest("[data-action]")?.dataset.action;
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
  if (action === "load-admin-records") loadAdminRecords().catch((error) => showToast(error.message, true));
  if (action === "load-rag-runs") loadRagRuns().catch((error) => showToast(error.message, true));
  if (action === "load-releases") loadReleases().catch((error) => showToast(error.message, true));
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

  const adminTab = event.target.closest("[data-admin-tab]")?.dataset.adminTab;
  if (adminTab) activateAdminTab(adminTab);

  const knowledgeButton = event.target.closest("[data-knowledge-id]");
  if (knowledgeButton) selectKnowledge(knowledgeButton.dataset.knowledgeId).catch((error) => showToast(error.message, true));

  const promptButton = event.target.closest("[data-prompt-key]");
  if (promptButton && !promptButton.dataset.action) selectPrompt(promptButton.dataset.promptKey).catch((error) => showToast(error.message, true));

  const ragButton = event.target.closest("[data-rag-run-id]");
  if (ragButton) selectRagRun(ragButton.dataset.ragRunId).catch((error) => showToast(error.message, true));
});

document.addEventListener("submit", (event) => {
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
  }
});

document.querySelector("#refreshButton").addEventListener("click", () => loadIncidents());
document.querySelector("#searchInput").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderList();
});
document.querySelector("#roleSelect").addEventListener("change", (event) => setRole(event.target.value));
document.querySelector("#knowledgeSearch").addEventListener("input", renderKnowledgeList);
document.querySelector("#recordSearch").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadAdminRecords().catch((error) => showToast(error.message, true));
  }
});
document.querySelector("#recordTypeSelect").addEventListener("change", () => loadAdminRecords().catch((error) => showToast(error.message, true)));

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

for (const dialog of [ingestDialog, demoDialog, sourceDialog, facilityDialog, adminDialog, publishConfirmDialog]) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog(dialog);
  });
}

setRole(state.role);
loadDemos();
loadSources(false).catch(() => {});
loadFacilities().catch(() => {});
loadIncidents();
