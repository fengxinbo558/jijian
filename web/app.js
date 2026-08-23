const state = {
  incidents: [],
  selectedId: null,
  selected: null,
  filter: "all",
  query: "",
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
  unknown: { unknown: "未知", on: "已点亮", off: "未点亮", yes: "是", no: "否" },
};

const listEl = document.querySelector("#incidentList");
const detailEl = document.querySelector("#incidentDetail");
const toastEl = document.querySelector("#toast");
const ingestDialog = document.querySelector("#ingestDialog");
const demoDialog = document.querySelector("#demoDialog");

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
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
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
    listEl.innerHTML = `<div class="list-empty">当前筛选下没有事件。<br>可以接入数据或运行一次演练。</div>`;
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
  if (stage === "planned") return verificationPlan(investigation.verification_plan);
  return `<p class="muted-empty">该步骤没有详细输出。</p>`;
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

      <aside class="capability-banner" data-mode="${escapeHtml(investigation.mode)}">
        <div class="capability-mode"><span>当前分析模式</span><strong>${escapeHtml(modeLabel(investigation.mode))}</strong></div>
        <p>${escapeHtml(investigation.capability_notice || "当前能力状态未知")}</p>
        ${investigation.simulation ? `<span class="simulation-badge">模拟数据 · 不代表真实设备状态</span>` : `<span class="live-input-badge">外部/人工输入 · 尚未独立核验</span>`}
      </aside>

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
    closeDialog(ingestDialog);
    showToast(`已进入事件 ${incident.id}`);
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
    showToast("演练数据已完成分析并进入事件中心");
    await loadIncidents(incident?.id);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

document.addEventListener("click", (event) => {
  const incidentButton = event.target.closest("[data-incident-id]");
  if (incidentButton) selectIncident(incidentButton.dataset.incidentId);

  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "open-ingest") openDialog(ingestDialog);
  if (action === "open-demos") openDialog(demoDialog);
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
});

document.querySelector("#refreshButton").addEventListener("click", () => loadIncidents());
document.querySelector("#searchInput").addEventListener("input", (event) => {
  state.query = event.target.value;
  renderList();
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

for (const dialog of [ingestDialog, demoDialog]) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog(dialog);
  });
}

loadDemos();
loadIncidents();
