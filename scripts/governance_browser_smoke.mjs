import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseURL = process.env.IDCAI_SMOKE_URL || "http://127.0.0.1:8766";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

try {
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.selectOption("#roleSelect", "super_admin");
  await page.getByRole("button", { name: /告警治理/ }).click();
  await page.locator("#governanceDialog").waitFor({ state: "visible" });
  await page.locator("#productionAlertForm button[type=submit]").click();
  await page.locator(".governance-alert-row").first().waitFor({ state: "visible" });
  const alertText = await page.locator(".governance-alert-row").first().innerText();
  if (!alertText.includes("TOR上联端口中断")) throw new Error("治理告警没有出现在列表首行");
  const activeCount = Number(await page.locator("#gateActive").innerText());
  if (activeCount < 1) throw new Error("事故闸门没有显示活动告警");

  await page.getByRole("button", { name: "可信数据" }).click();
  if (!(await page.locator("#sourceHealthForm").isVisible())) throw new Error("最高管理员看不到可信数据维护入口");
  await page.screenshot({ path: "/tmp/idc-ai-ops-governance-admin.png", fullPage: true });

  await page.selectOption("#roleSelect", "onsite_operator");
  await page.waitForTimeout(300);
  if ((await page.locator("#roleSelect").inputValue()) !== "onsite_operator") throw new Error("角色没有切换到现场人员");
  if (await page.locator("#sourceHealthForm").isVisible()) throw new Error("现场人员不应看到采集链路修改入口");
  if (await page.locator('[data-action="acknowledge-production-alert"]').count()) throw new Error("现场人员不应确认平台告警");
  await page.getByRole("button", { name: "公开数据测试" }).click();
  const enabledDatasetButtons = await page.locator("#publicDatasetGrid button:not([disabled])").count();
  if (enabledDatasetButtons !== 0) throw new Error("现场人员不应能导入公开测试数据");

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  mobile.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`mobile: ${message.text()}`);
  });
  mobile.on("pageerror", (error) => consoleErrors.push(`mobile: ${error.message}`));
  await mobile.goto(baseURL, { waitUntil: "networkidle" });
  await mobile.selectOption("#roleSelect", "onsite_operator");
  await mobile.waitForTimeout(200);
  if ((await mobile.locator("#roleSelect").inputValue()) !== "onsite_operator") throw new Error("移动端没有切换到现场人员角色");
  await mobile.getByRole("button", { name: /告警治理/ }).click();
  await mobile.locator("#governanceDialog").waitFor({ state: "visible" });
  const dialogBox = await mobile.locator("#governanceDialog").boundingBox();
  if (!dialogBox || dialogBox.width > 390) throw new Error("移动端治理窗口超出视口");
  if (await mobile.locator('[data-action="acknowledge-production-alert"]').count()) throw new Error("移动端现场人员不应确认平台告警");
  await mobile.screenshot({ path: "/tmp/idc-ai-ops-governance-mobile.png", fullPage: false });
  await mobile.close();

  if (consoleErrors.length) throw new Error(`页面控制台错误：${consoleErrors.join(" | ")}`);
  console.log(JSON.stringify({
    status: "passed",
    active_alerts: activeCount,
    admin_screenshot: "/tmp/idc-ai-ops-governance-admin.png",
    mobile_screenshot: "/tmp/idc-ai-ops-governance-mobile.png",
    onsite_dataset_buttons_enabled: enabledDatasetButtons,
  }));
} finally {
  await browser.close();
}
