#!/usr/bin/env node
"use strict";

const { chromium } = require("playwright");

const baseUrl = process.env.IDCAI_BASE_URL || "http://127.0.0.1:8877";
const headers = {
  "Content-Type": "application/json",
  "X-IDCAI-Role": "ai_admin",
  "X-IDCAI-User": "ai-console-smoke",
};

async function incidentCount(page) {
  const response = await page.request.get(`${baseUrl}/api/incidents`, { headers });
  if (!response.ok()) throw new Error(`incident list failed: ${response.status()}`);
  return (await response.json()).items.length;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const consoleErrors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 980 } });
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.addInitScript(() => {
      localStorage.setItem("idcai-role", "ai_admin");
      localStorage.setItem("idcai-actor", "ai-console-smoke");
    });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: /AI控制台/ }).click();
    await page.locator("#adminView:not([hidden])").waitFor();
    if (!page.url().includes("#ai-control=")) throw new Error("AI console does not have an independent route");
    if ((await page.locator("[data-admin-tab]").count()) !== 11) throw new Error("AI console does not expose eleven modules");
    if (!(await page.locator("#adminSummary").innerText()).includes("48")) throw new Error("published knowledge count is missing");
    const rootSize = await page.evaluate(() => ({ scroll: document.documentElement.scrollHeight, client: document.documentElement.clientHeight }));
    if (rootSize.scroll !== rootSize.client) throw new Error(`AI console causes page scroll: ${JSON.stringify(rootSize)}`);
    if (process.env.IDCAI_READ_ONLY === "1") {
      await page.getByRole("button", { name: /RAG 知识库/ }).click();
      await page.locator("#knowledgeList .asset-row").first().waitFor();
      await page.screenshot({ path: "/tmp/idc-ai-control-console-live.png", fullPage: true });
      if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
      process.stdout.write("Live AI control console passed read-only checks: independent route, eleven modules, published knowledge, fixed viewport.\n");
      return;
    }

    const before = await incidentCount(page);
    await page.getByRole("button", { name: /检索测试/ }).click();
    await page.locator("#retrievalIndexStatus").getByText("48 条", { exact: true }).waitFor();
    await page.locator('#retrievalTestForm textarea[name="text"]').fill("nvme0: I/O timeout; blk_update_request I/O error");
    await page.locator('#retrievalTestForm select[name="domain"]').selectOption("storage");
    await page.getByRole("button", { name: "运行真实检索测试" }).click();
    await page.locator("#retrievalTestResult .retrieval-result-head").waitFor();
    const resultText = await page.locator("#retrievalTestResult").innerText();
    if (!resultText.includes("未创建故障") || !resultText.includes("STORAGE")) throw new Error("retrieval test result is incomplete");
    const after = await incidentCount(page);
    if (after !== before) throw new Error("retrieval test created a production incident");

    await page.getByRole("button", { name: /约束中心/ }).click();
    await page.locator("#hardGuardList .hard-guard-item").first().waitFor();
    if ((await page.locator("#hardGuardList .hard-guard-item").count()) < 5) throw new Error("hard guard list is incomplete");
    if ((await page.getByText("不可编辑", { exact: true }).count()) < 5) throw new Error("hard guards are not visibly read-only");
    const version = `browser-policy-${Date.now()}`;
    await page.locator('#constraintDraftForm input[name="version"]').fill(version);
    await page.getByRole("button", { name: "保存约束草稿" }).click();
    await page.locator("#constraintEditor details.raw-asset > summary").click();
    await page.locator(`#constraintEditor button[data-version="${version}"]`).waitFor();
    await page.locator(`button[data-version="${version}"]`).click();
    await page.locator('#releaseList .release-row', { hasText: version }).waitFor();
    if (!(await page.locator("#releaseList").innerText()).includes(version)) throw new Error("constraint release did not enter release center");

    await page.getByRole("button", { name: /审计记录/ }).click();
    await page.locator("#auditRoleNote").getByText("AI 运营视图", { exact: true }).waitFor();
    await page.locator("#aiAuditList .record-row").first().waitFor();
    const auditText = await page.locator("#aiAuditList").innerText();
    if (!auditText.includes("运行检索测试") || !auditText.includes(version)) throw new Error("AI activity timeline is incomplete");
    await page.screenshot({ path: "/tmp/idc-ai-control-console-desktop.png", fullPage: true });

    await page.getByRole("button", { name: "返回故障中心" }).click();
    await page.selectOption("#roleSelect", "onsite_operator");
    if (!(await page.locator("#adminRailButton").isHidden())) throw new Error("onsite role can still see the AI console entry");
    if (await page.locator("#adminView:not([hidden])").count()) throw new Error("onsite role opened the AI console");

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
    await mobile.addInitScript(() => localStorage.setItem("idcai-role", "ai_admin"));
    await mobile.goto(baseUrl, { waitUntil: "networkidle" });
    await mobile.getByRole("button", { name: /AI控制台/ }).click();
    await mobile.locator("#adminView:not([hidden])").waitFor();
    await mobile.getByRole("button", { name: /检索测试/ }).click();
    await mobile.locator("#retrievalTestForm").waitFor();
    const mobileWidth = await mobile.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    if (mobileWidth.scroll !== mobileWidth.client) throw new Error(`mobile horizontal overflow: ${JSON.stringify(mobileWidth)}`);
    await mobile.screenshot({ path: "/tmp/idc-ai-control-console-mobile.png", fullPage: true });
    await mobile.close();

    if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
    process.stdout.write("AI control console passed: independent route, ten modules, real retrieval, hard guards, release entry, role gate, desktop and mobile.\n");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
