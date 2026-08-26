#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const reports = path.join(root, "reports");
const baseUrl = process.env.IDCAI_BASE_URL || "http://127.0.0.1:8765";
fs.mkdirSync(reports, { recursive: true });

async function seed(page) {
  const stamp = Date.now();
  const sn = `UI-CENTER-SN-${stamp}`;
  const headers = { "Content-Type": "application/json", "X-IDCAI-Role": "ai_admin", "X-IDCAI-User": "ui-center-smoke" };
  const real = await page.request.post(`${baseUrl}/api/ingest/log`, {
    headers,
    data: {
      site: "BJYZ",
      sn,
      rack_position: "BJYZ-UI-A-01-01",
      device_name: "ui-center-smoke-host",
      device_type: "server",
      severity: "critical",
      facility_criticality: "normal",
      summary: "故障中心界面测试：磁盘I/O异常",
      log_text: "kernel: blk_update_request: I/O error, dev sdd",
    },
  });
  if (!real.ok()) throw new Error(`real incident seed failed: ${real.status()}`);
  const simulation = await page.request.post(`${baseUrl}/api/demos/network-optic/run`, { headers, data: {} });
  if (!simulation.ok()) throw new Error(`simulation seed failed: ${simulation.status()}`);
  return { sn };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const consoleErrors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    const { sn } = await seed(page);
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "故障中心" }).waitFor();
    await page.locator("#incidentList .incident-row").first().waitFor();

    const rootSize = await page.evaluate(() => ({ scroll: document.documentElement.scrollHeight, client: document.documentElement.clientHeight }));
    if (rootSize.scroll !== rootSize.client) throw new Error(`page shell scrolls: ${JSON.stringify(rootSize)}`);
    if (await page.locator('#incidentList .incident-row[data-lane="simulation"]').count()) throw new Error("simulation leaked into active queue");
    const firstHeight = await page.locator("#incidentList .incident-row").first().evaluate((element) => element.getBoundingClientRect().height);
    if (firstHeight > 90) throw new Error(`desktop incident row is too tall: ${firstHeight}`);

    await page.getByRole("button", { name: /模拟演练/ }).click();
    await page.locator('#incidentList .incident-row[data-lane="simulation"]').first().waitFor();
    const simulationRows = await page.locator("#incidentList .incident-row").count();
    const correctSimulationRows = await page.locator('#incidentList .incident-row[data-lane="simulation"]').count();
    if (!simulationRows || simulationRows !== correctSimulationRows) throw new Error("simulation queue contains non-simulation incidents");

    await page.getByRole("button", { name: /进行中/ }).click();
    await page.locator("#searchInput").fill(sn);
    await page.getByText(sn, { exact: true }).waitFor();
    await page.getByText(sn, { exact: true }).click();
    await page.locator("#incidentDetailView:not([hidden])").waitFor();
    if (!(await page.locator(".identity-strip").innerText()).includes(sn)) throw new Error("detail identity is missing full SN");
    if ((await page.getByRole("tab").count()) !== 5) throw new Error("detail does not have five work tabs");
    if ((await page.locator(".incident-workbench-grid .workbench-card").count()) !== 3) throw new Error("field-first summary does not show judgment, evidence and collaboration");
    const detailRootSize = await page.evaluate(() => ({ scroll: document.documentElement.scrollHeight, client: document.documentElement.clientHeight }));
    if (detailRootSize.scroll !== detailRootSize.client) throw new Error(`detail shell scrolls: ${JSON.stringify(detailRootSize)}`);

    await page.getByRole("tab", { name: "AI 分析与证据" }).click();
    if ((await page.locator(".evidence-node").count()) < 7) throw new Error("auditable evidence route is missing");
    await page.getByRole("tab", { name: "处置步骤" }).click();
    await page.getByRole("button", { name: "进入现场操作" }).waitFor();
    await page.getByRole("tab", { name: "原始日志" }).click();
    if (!(await page.locator('[data-detail-panel="raw"] .intake-list').innerText()).includes("用户粘贴或上传日志")) throw new Error("raw intake is missing");
    await page.getByRole("button", { name: /返回故障列表/ }).click();
    if ((await page.locator("#searchInput").inputValue()) !== sn) throw new Error("list filters were not preserved after returning");
    await page.getByRole("button", { name: "清除筛选" }).click();
    await page.waitForTimeout(3200);
    await page.screenshot({ path: path.join(reports, "browser-incident-center.png"), fullPage: true });
    await page.selectOption("#roleSelect", "onsite_operator");
    await page.waitForTimeout(350);
    await page.locator("#incidentList .incident-row").first().click();
    await page.locator("#incidentDetailView:not([hidden])").waitFor();
    if ((await page.locator('[data-detail-tab="overview"]').getAttribute("aria-selected")) !== "true") throw new Error("onsite role did not open the action tab");
    await page.getByRole("button", { name: /返回故障列表/ }).click();
    await page.selectOption("#roleSelect", "ai_admin");

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
    await mobile.goto(baseUrl, { waitUntil: "networkidle" });
    await mobile.locator("#incidentList .incident-row").first().waitFor();
    const mobileWidth = await mobile.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    if (mobileWidth.scroll !== mobileWidth.client) throw new Error(`mobile horizontal overflow: ${JSON.stringify(mobileWidth)}`);
    await mobile.locator("#incidentList .incident-row").first().click();
    await mobile.locator("#incidentDetailView:not([hidden])").waitFor();
    if ((await mobile.getByRole("tab").count()) !== 5) throw new Error("mobile detail tabs missing");
    await mobile.getByRole("tab", { name: "AI 分析与证据" }).click();
    await mobile.locator(".evidence-node").first().waitFor();
    await mobile.getByRole("button", { name: /返回故障列表/ }).click();
    await mobile.locator("#incidentListView:not([hidden])").waitFor();
    await mobile.waitForTimeout(3200);
    await mobile.screenshot({ path: path.join(reports, "browser-incident-center-mobile.png"), fullPage: true });
    await mobile.close();

    if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
    process.stdout.write("Incident center passed: compact list, simulation isolation, field-first detail, five tabs, role defaults, desktop and mobile.\n");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
