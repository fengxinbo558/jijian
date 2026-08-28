#!/usr/bin/env node
"use strict";

const { chromium } = require("playwright");

const baseUrl = process.env.IDCAI_BASE_URL || "http://127.0.0.1:8765";

async function openSandbox(page, role = "ai_admin") {
  await page.addInitScript(({ selectedRole }) => {
    localStorage.setItem("idcai-role", selectedRole);
    localStorage.setItem("idcai-actor", `${selectedRole}-sandbox-smoke`);
  }, { selectedRole: role });
  await page.goto(`${baseUrl}/#ai-control=sandbox`, { waitUntil: "networkidle" });
  await page.locator('[data-admin-panel="sandbox"].is-active').waitFor();
  await page.locator("#sandboxLiveStatus").waitFor();
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const failures = [];
  const consoleErrors = [];
  const pageErrors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => failures.push(`${request.method()} ${request.url()} ${request.failure()?.errorText || "failed"}`));
    await openSandbox(page);

    const rootSize = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    if (rootSize.scrollHeight !== rootSize.clientHeight) {
      throw new Error(`桌面沙盒导致整页纵向滚动：${JSON.stringify(rootSize)}`);
    }
    if (rootSize.scrollWidth !== rootSize.clientWidth) {
      throw new Error(`桌面沙盒导致横向滚动：${JSON.stringify(rootSize)}`);
    }

    const runButton = page.getByRole("button", { name: "运行完整 120 题盲测" });
    await runButton.waitFor();
    const previousRun = await page.locator("#sandboxRunSelect").inputValue();
    page.once("dialog", (dialog) => dialog.accept());
    await runButton.click();
    await page.waitForFunction(
      ({ previous }) => {
        const button = document.querySelector('#sandboxRunForm button[type="submit"]');
        const selected = document.querySelector("#sandboxRunSelect")?.value || "";
        const verdict = document.querySelector("#sandboxVerdict")?.textContent || "";
        return button && !button.disabled && selected && selected !== previous && verdict.includes(selected);
      },
      { previous: previousRun },
      { timeout: 60000 },
    );
    await page.locator("#sandboxCaseList .sandbox-case-row").first().waitFor({ timeout: 60000 });

    const selectedRun = await page.locator("#sandboxRunSelect").inputValue();
    if (!selectedRun.startsWith("SBX-")) throw new Error("运行完成后没有选择新沙盒记录");
    const verdictText = await page.locator("#sandboxVerdict").innerText();
    if (!verdictText.includes("120 / 120")) throw new Error(`结论没有显示完整120题：${verdictText}`);
    if (!verdictText.includes("未接入，未运行")) throw new Error("未配置模型时没有如实显示AI未运行");

    const firstCase = page.locator("#sandboxCaseList .sandbox-case-row").first();
    await firstCase.focus();
    await firstCase.click();
    await page.locator("#sandboxCaseDialog[open]").waitFor();
    const drawerText = await page.locator("#sandboxCaseDetail").innerText();
    if (!drawerText.includes("实际送进了什么") || !drawerText.includes("规则基线结果") || !drawerText.includes("独立评分器")) {
      throw new Error("逐题证据抽屉缺少输入、正式结果或独立评分");
    }
    if (drawerText.includes("已揭晓隐藏答案")) throw new Error("AI管理员意外读取到了隐藏答案");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector("#sandboxCaseDialog")?.open);
    const focusReturned = await firstCase.evaluate((element) => document.activeElement === element);
    if (!focusReturned) throw new Error("关闭逐题抽屉后键盘焦点没有返回原题目");

    await page.screenshot({ path: "reports/browser-sandbox-validation.png", fullPage: false });
    await context.close();

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const mobile = await mobileContext.newPage();
    mobile.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(`mobile: ${message.text()}`);
    });
    mobile.on("pageerror", (error) => pageErrors.push(`mobile: ${error.message}`));
    await openSandbox(mobile);
    const mobileSize = await mobile.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    if (mobileSize.scrollWidth > mobileSize.clientWidth) {
      throw new Error(`手机界面出现横向滚动：${JSON.stringify(mobileSize)}`);
    }
    await mobile.locator("#sandboxCaseList .sandbox-case-row").first().waitFor();
    await mobile.screenshot({ path: "reports/browser-sandbox-validation-mobile.png", fullPage: false });
    await mobileContext.close();

    if (consoleErrors.length) throw new Error(`浏览器控制台错误：${consoleErrors.join(" | ")}`);
    if (pageErrors.length) throw new Error(`页面脚本错误：${pageErrors.join(" | ")}`);
    if (failures.length) throw new Error(`请求失败：${failures.join(" | ")}`);
    process.stdout.write(JSON.stringify({
      ok: true,
      run_id: selectedRun,
      desktop: rootSize,
      mobile: mobileSize,
      checks: [
        "完整120题运行",
        "真实AI未接入状态不造假",
        "逐题输入/结果/评分可见",
        "隐藏答案保持隔离",
        "Escape关闭与焦点返回",
        "桌面固定视口与手机无横向滚动",
      ],
    }, null, 2) + "\n");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
});
