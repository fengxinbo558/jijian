#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const reports = path.join(root, "reports");
const baseUrl = process.env.IDCAI_BASE_URL || "http://127.0.0.1:8765";
fs.mkdirSync(reports, { recursive: true });

(async () => {
  const consoleErrors = [];
  const browserSn = `BROWSER-FULL-SN-${Date.now()}`;
  const facilityCode = `UIT${String(Date.now()).slice(-7)}`;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "故障中心" }).waitFor();

    await page.getByRole("button", { name: "运行模拟案例" }).click();
    await page.getByRole("button", { name: "运行这个场景" }).first().click();
    await page.locator(".incident-heading .incident-summary").getByText("冷通道温度持续升高，服务器风扇进入高速", { exact: true }).waitFor();
    await page.getByRole("tab", { name: "处置步骤" }).click();
    await page.locator(".cc-alert").waitFor();
    if (!(await page.locator(".cc-alert").innerText()).includes("请立即按现有 CC 流程拨打电话")) throw new Error("CC boundary missing");
    if (!(await page.locator(".identity-strip").innerText()).includes("G3M02179543")) throw new Error("full SN missing");
    if (!(await page.locator(".facility-assessment").innerText()).includes("为什么这样判断")) throw new Error("facility decision explanation missing");
    await page.getByRole("tab", { name: "审计记录" }).click();
    if (!(await page.locator(".capability-banner").innerText()).includes("规则＋知识")) throw new Error("analysis mode missing");
    if (!(await page.locator(".capability-banner .simulation-badge").innerText()).includes("模拟数据")) throw new Error("simulation badge missing");
    if (!(await page.locator(".signal-route").innerText()).includes("模拟数据不会查询真实设备")) throw new Error("data route disclosure missing");
    await page.getByRole("tab", { name: "AI 分析与证据" }).click();
    if ((await page.locator(".evidence-node").count()) < 7) throw new Error("trace nodes missing");
    if (!(await page.locator(".correlation-panel").textContent()).includes("外部输入提供了相同 incident_key")) throw new Error("correlation reason missing");
    if ((await page.locator(".hypothesis-list").first().innerText()).includes("%")) throw new Error("fake confidence visible");
    await page.screenshot({ path: path.join(reports, "browser-desktop.png"), fullPage: true });

    await page.getByRole("button", { name: "查看数据来源" }).click();
    await page.locator("#sourceGrid .source-card").first().waitFor();
    if (!(await page.locator("#sourceGrid").innerText()).includes("SigNoz 监控底座")) throw new Error("SigNoz source missing");
    if (!(await page.locator("#sourceGrid").innerText()).includes("网络 syslog 与 NMS")) throw new Error("network log source missing");
    if (!(await page.locator("#sourceGrid").innerText()).includes("尚未连接")) throw new Error("honest unconfigured state missing");
    await page.screenshot({ path: path.join(reports, "browser-sources.png"), fullPage: false });
    await page.locator("#sourceDialog [data-close-dialog]").click();

    await page.getByRole("button", { name: "机房等级" }).click();
    await page.locator("#facilityForm input[name='site']").fill(facilityCode);
    await page.locator("#facilityForm input[name='display_name']").fill("浏览器测试核心机房");
    await page.locator("#facilityForm select[name='criticality']").selectOption("core");
    await page.locator("#facilityForm").getByRole("button", { name: "保存标注" }).click();
    await page.getByRole("heading", { name: facilityCode }).waitFor();
    if (!(await page.locator("#facilityGrid").innerText()).includes("核心机房")) throw new Error("facility profile not saved");
    await page.locator("#facilityDialog [data-close-dialog]").click();

    await page.getByRole("button", { name: "模拟案例", exact: true }).click();
    await page.locator(".demo-card", { hasText: "核心机房单路掉电" }).getByRole("button", { name: "运行这个场景" }).click();
    await page.locator(".incident-heading .incident-summary").getByText("核心机房A路供电中断", { exact: true }).waitFor();
    await page.getByRole("tab", { name: "处置步骤" }).click();
    await page.locator('.facility-assessment[data-decision="required"]').waitFor();
    const coreAssessment = await page.locator(".facility-assessment").innerText();
    if (!coreAssessment.includes("核心机房") || !coreAssessment.includes("需要CC") || !coreAssessment.includes("CC-CORE-SINGLE-FEED")) throw new Error("core facility CC matrix incorrect");

    await page.getByRole("button", { name: "模拟案例", exact: true }).click();
    await page.locator(".demo-card", { hasText: "普通机房单路掉电" }).getByRole("button", { name: "运行这个场景" }).click();
    await page.locator(".incident-heading .incident-summary").getByText("普通机房A路供电中断", { exact: true }).waitFor();
    await page.getByRole("tab", { name: "处置步骤" }).click();
    await page.locator('.facility-assessment[data-decision="not_required"]').waitFor();
    const normalAssessment = await page.locator(".facility-assessment").innerText();
    if (!normalAssessment.includes("普通机房") || !normalAssessment.includes("普通处理")) throw new Error("ordinary facility decision incorrect");
    await page.screenshot({ path: path.join(reports, "browser-facility-assessment.png"), fullPage: true });

    await page.getByRole("button", { name: "真实故障", exact: true }).click();
    await page.locator("#logForm input[name='sn']").fill(browserSn);
    await page.locator("#logForm input[name='rack_position']").fill("BJYZD9-C-23-01");
    await page.locator("#logForm input[name='device_name']").fill("bjyz-browser-check");
    await page.locator("#logForm input[name='summary']").fill("浏览器测试磁盘错误");
    await page.locator("#logText").fill("kernel: blk_update_request: I/O error, dev sdd");
    await page.getByRole("button", { name: "分析这份日志" }).click();
    await page.locator(".identity-strip").getByText(browserSn, { exact: true }).waitFor();
    await page.getByRole("tab", { name: "原始日志" }).click();
    if (!(await page.locator(".intake-list").first().innerText()).includes("用户粘贴或上传日志")) throw new Error("intake provenance missing");
    await page.getByRole("tab", { name: "AI 分析与证据" }).click();
    if (!(await page.locator(".investigation-section").textContent()).includes("STORAGE-IO-001")) throw new Error("knowledge card missing");
    if (!(await page.locator(".judgment-glance").innerText()).includes("尚未通过真实工具或人工检查确认")) throw new Error("uncertainty missing");
    await page.getByRole("tab", { name: "审计记录" }).click();
    await page.getByRole("button", { name: "查询监控并让 AI 补充调查" }).click();
    await page.getByRole("tab", { name: "AI 分析与证据" }).click();
    await page.waitForFunction(() => document.querySelector(".investigation-section")?.textContent?.includes("SigNoz 尚未连接，未执行自动日志查询"));
    await page.getByRole("tab", { name: "处置步骤" }).click();
    await page.getByRole("button", { name: "开始处理" }).click();
    await page.getByText("处理中", { exact: true }).last().waitFor();

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await mobile.goto(baseUrl, { waitUntil: "networkidle" });
    await mobile.locator(".incident-row").first().waitFor();
    await mobile.locator(".incident-row").first().click();
    await mobile.getByRole("tab", { name: "审计记录" }).click();
    await mobile.locator(".capability-banner").waitFor();
    await mobile.getByRole("tab", { name: "处置步骤" }).click();
    await mobile.locator(".facility-assessment").waitFor();
    await mobile.getByRole("tab", { name: "AI 分析与证据" }).click();
    if ((await mobile.locator(".evidence-node").count()) < 7) throw new Error("mobile trace missing");
    await mobile.screenshot({ path: path.join(reports, "browser-mobile.png"), fullPage: true });
    await mobile.close();

    if (consoleErrors.length) throw new Error(`Browser console errors: ${consoleErrors.join(" | ")}`);
    process.stdout.write("Browser flow passed: source state, facility profiles, three-state CC matrix, data route, trace, real incident, full SN, status, mobile\n");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
