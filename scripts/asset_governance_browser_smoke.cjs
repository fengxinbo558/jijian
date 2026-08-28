#!/usr/bin/env node
"use strict";

const { chromium } = require("playwright");

const baseUrl = process.env.IDCAI_BASE_URL || "http://127.0.0.1:8877";
const headers = {
  "Content-Type": "application/json",
  "X-IDCAI-Role": "ai_admin",
  "X-IDCAI-User": "asset-governance-smoke",
};

async function seedCandidate(page) {
  const response = await page.request.get(`${baseUrl}/api/admin/knowledge/STORAGE-IO-001`, { headers });
  if (!response.ok()) throw new Error(`knowledge seed failed: ${response.status()}`);
  const detail = await response.json();
  return detail.versions[0].content;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const consoleErrors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.addInitScript(() => {
      localStorage.setItem("idcai-role", "ai_admin");
      localStorage.setItem("idcai-actor", "asset-governance-smoke");
    });
    await page.goto(`${baseUrl}/#ai-control=governance`, { waitUntil: "networkidle" });
    await page.locator('[data-admin-panel="governance"].is-active').waitFor();
    await page.locator("#assetGovernanceOverview .governance-metric").first().waitFor();
    if ((await page.locator("#assetGovernanceOverview .governance-metric").count()) !== 5) {
      throw new Error("governance overview metrics are incomplete");
    }
    const viewport = await page.evaluate(() => ({ scroll: document.documentElement.scrollHeight, client: document.documentElement.clientHeight }));
    if (viewport.scroll !== viewport.client) throw new Error(`governance console causes page scroll: ${JSON.stringify(viewport)}`);

    await page.getByRole("button", { name: "统一目录" }).click();
    await page.locator("#assetCatalogTable tbody tr").first().waitFor();
    const firstOpen = page.locator('[data-action="open-governance-asset"]').first();
    await firstOpen.focus();
    await firstOpen.click();
    await page.locator("#assetGovernanceDetailDialog[open]").waitFor();
    const drawerText = await page.locator("#assetGovernanceDetail").innerText();
    if (!drawerText.includes("来源") || !drawerText.includes("版本") || !drawerText.includes("使用结果")) {
      throw new Error("asset provenance drawer is incomplete");
    }
    await page.locator('#assetGovernanceDetailDialog [data-close-dialog]').click();
    const restored = await firstOpen.evaluate((element) => document.activeElement === element);
    if (!restored) throw new Error("asset detail drawer did not restore keyboard focus");

    if (process.env.IDCAI_READ_ONLY === "1") {
      await page.screenshot({ path: "/tmp/idc-ai-asset-governance-live.png", fullPage: true });
      if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
      process.stdout.write("Live asset governance UI passed read-only checks: overview, catalog, provenance drawer and focus return.\n");
      return;
    }

    const candidate = await seedCandidate(page);
    await page.getByRole("button", { name: "导入工作台" }).click();
    const sourceLabel = `浏览器重复检测-${Date.now()}`;
    await page.locator('#assetImportForm input[name="source_label"]').fill(sourceLabel);
    await page.locator('#assetImportForm textarea[name="content"]').fill(JSON.stringify(candidate));
    await page.getByRole("button", { name: "扫描这批内容" }).click();
    await page.waitForFunction(() => !document.querySelector('#assetImportForm button[type="submit"]')?.disabled);
    const latestBatch = page.locator("#assetImportResults .import-batch", { hasText: sourceLabel }).first();
    await latestBatch.waitFor();
    if (!(await latestBatch.evaluate((element) => element.open))) await latestBatch.locator("summary").click();
    const importText = await latestBatch.innerText();
    if (!importText.includes("完全重复") || !importText.includes("确认导入处理")) {
      throw new Error("exact duplicate import was not staged correctly");
    }
    await latestBatch.locator('[data-action="confirm-import-batch"]').click();
    await page.locator("#assetImportResults").getByText("导入完成", { exact: true }).first().waitFor();

    await page.screenshot({ path: "/tmp/idc-ai-asset-governance-desktop.png", fullPage: true });

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
    await mobile.addInitScript(() => localStorage.setItem("idcai-role", "ai_admin"));
    await mobile.goto(`${baseUrl}/#ai-control=governance`, { waitUntil: "networkidle" });
    await mobile.locator('[data-admin-panel="governance"].is-active').waitFor();
    await mobile.getByRole("button", { name: "统一目录" }).click();
    await mobile.locator("#assetCatalogTable tbody tr").first().waitFor();
    const width = await mobile.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    if (width.scroll !== width.client) throw new Error(`mobile horizontal overflow: ${JSON.stringify(width)}`);
    await mobile.screenshot({ path: "/tmp/idc-ai-asset-governance-mobile.png", fullPage: true });
    await mobile.close();

    if (consoleErrors.length) throw new Error(`browser console errors: ${consoleErrors.join(" | ")}`);
    process.stdout.write("Asset governance UI passed: compact overview, catalog, provenance drawer, focus return, staged duplicate import, desktop and mobile.\n");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
