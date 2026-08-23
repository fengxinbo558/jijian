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
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "故障队列" }).waitFor();

    await page.getByRole("button", { name: "运行演练" }).first().click();
    await page.getByRole("button", { name: "运行这个场景" }).first().click();
    await page.locator(".incident-row").first().waitFor();
    await page.locator(".cc-alert").waitFor();
    if (!(await page.locator(".cc-alert").innerText()).includes("请立即按现有 CC 流程拨打电话")) throw new Error("CC boundary missing");
    if (!(await page.locator(".identity-strip").innerText()).includes("G3M02179543")) throw new Error("full SN missing");
    if (!(await page.locator(".capability-banner").innerText()).includes("规则＋知识")) throw new Error("analysis mode missing");
    if (!(await page.locator(".simulation-badge").innerText()).includes("模拟数据")) throw new Error("simulation badge missing");
    if ((await page.locator(".evidence-node").count()) < 7) throw new Error("trace nodes missing");
    if (!(await page.locator(".correlation-panel").textContent()).includes("外部输入提供了相同 incident_key")) throw new Error("correlation reason missing");
    if ((await page.locator(".hypothesis-list").first().innerText()).includes("%")) throw new Error("fake confidence visible");
    await page.screenshot({ path: path.join(reports, "browser-desktop.png"), fullPage: true });

    await page.getByRole("button", { name: "接入数据" }).first().click();
    await page.locator("#logForm input[name='sn']").fill("BROWSER-FULL-SN-20260823");
    await page.locator("#logForm input[name='rack_position']").fill("BJYZD9-C-23-01");
    await page.locator("#logForm input[name='device_name']").fill("bjyz-browser-check");
    await page.locator("#logForm input[name='summary']").fill("浏览器测试磁盘错误");
    await page.locator("#logText").fill("kernel: blk_update_request: I/O error, dev sdd");
    await page.getByRole("button", { name: "分析这份日志" }).click();
    await page.getByText("BROWSER-FULL-SN-20260823", { exact: true }).first().waitFor();
    if (!(await page.locator(".intake-list").first().innerText()).includes("用户粘贴或上传日志")) throw new Error("intake provenance missing");
    if (!(await page.locator(".investigation-section").textContent()).includes("STORAGE-IO-001")) throw new Error("knowledge card missing");
    if (!(await page.locator(".conclusion-board").innerText()).includes("尚未通过真实工具或人工检查确认")) throw new Error("uncertainty missing");
    await page.getByRole("button", { name: "开始处理" }).click();
    await page.getByText("处理中", { exact: true }).last().waitFor();

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    await mobile.goto(baseUrl, { waitUntil: "networkidle" });
    await mobile.locator(".incident-row").first().waitFor();
    await mobile.locator(".capability-banner").waitFor();
    if ((await mobile.locator(".evidence-node").count()) < 7) throw new Error("mobile trace missing");
    await mobile.screenshot({ path: path.join(reports, "browser-mobile.png"), fullPage: true });
    await mobile.close();

    if (consoleErrors.length) throw new Error(`Browser console errors: ${consoleErrors.join(" | ")}`);
    process.stdout.write("Browser flow passed: capability disclosure, trace, correlation, knowledge, CC boundary, ingestion, full SN, status, mobile\n");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
