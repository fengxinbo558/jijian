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

function candidateList(candidates = []) {
  if (!candidates.length) return `<p class="list-empty">还没有可展示的根因候选。</p>`;
  return `<ul class="candidate-list">${candidates.map((candidate) => `
    <li class="candidate-item">
      <div class="candidate-top">
        <span class="candidate-title">${escapeHtml(candidate.title)}</span>
        <span class="confidence">${Math.round(Number(candidate.confidence || 0) * 100)}%</span>
      </div>
      <p class="candidate-meta">证据：${escapeHtml((candidate.evidence_ids || []).join("、") || "暂无直接证据")} · ${escapeHtml(candidate.status || "候选")}</p>
      <p class="candidate-meta">反证检查：${escapeHtml(candidate.counter_evidence || "未提供")}</p>
    </li>`).join("")}</ul>`;
}

function evidenceList(evidence = []) {
  if (!evidence.length) return `<p class="list-empty">当前没有证据。</p>`;
  return `<ol class="evidence-list">${evidence.map((item) => `
    <li class="evidence-item">
      <span class="evidence-id">${escapeHtml(item.id)}</span>
      <p class="evidence-text">${escapeHtml(item.text)}</p>
    </li>`).join("")}</ol>`;
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
  const analysis = incident.analysis || {};
  const device = incident.devices?.[0] || {};
  const moreDevices = (incident.devices || []).slice(1);
  document.querySelector("#workspaceKicker").textContent = incident.id;
  document.querySelector("#workspaceTitle").textContent = `${incident.site || "机房待确认"} · ${incident.title}`;
  detailEl.innerHTML = `
    <article class="incident-layout">
      <header class="incident-heading">
        <div>
          <p class="eyebrow">EVIDENCE-BACKED INCIDENT</p>
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
      ${moreDevices.length ? `<p class="multi-device-note">已关联另外 ${moreDevices.length} 台设备：${moreDevices.map((item) => escapeHtml(item.sn || item.name || item.rack_position)).join("、")}</p>` : ""}

      ${incident.cc_reminder?.required ? `
        <aside class="cc-alert" role="alert">
          <span class="cc-mark">CC</span>
          <div><strong>${escapeHtml(incident.cc_reminder.message)}</strong><p>${escapeHtml(incident.cc_reminder.reason || "输入已明确标记需要通报")}</p></div>
        </aside>` : ""}

      <div class="detail-grid">
        <div class="detail-column">
          <section class="section-block">
            <div class="section-heading"><h3>根因候选</h3><small>${analysis.ai_mode === "model_enhanced" ? "模型增强" : "规则分析"}</small></div>
            ${candidateList(analysis.candidate_causes)}
          </section>
          <section class="section-block">
            <div class="section-heading"><h3>关键证据</h3><small>${(incident.evidence || []).length} 条</small></div>
            ${evidenceList(incident.evidence)}
          </section>
          <section class="section-block">
            <div class="section-heading"><h3>接口沟通摘要</h3><small>可复制</small></div>
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
            <div class="section-heading"><h3>证据来源</h3><small>${(incident.inputs || []).length} 次输入</small></div>
            <ul class="evidence-list">${(incident.inputs || []).map((input) => `
              <li class="evidence-item"><span class="evidence-id">${escapeHtml(translated("source", input.source))}</span><p class="evidence-text">${escapeHtml(formatTime(input.event_time))} · ${escapeHtml(input.payload?.summary || "补充证据")}</p></li>
            `).join("") || `<li class="list-empty">暂无输入记录</li>`}</ul>
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
