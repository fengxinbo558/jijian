import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseURL = process.env.IDCAI_SMOKE_URL || "http://127.0.0.1:8765";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

try {
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.selectOption("#roleSelect", "ai_admin");
  await page.getByRole("button", { name: /故障演练/ }).click();
  await page.locator("#drillDialog").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelectorAll("#drillCatalogList .drill-catalog-item").length === 5);
  if ((await page.locator("#drillCatalogList .drill-catalog-item").count()) !== 5) throw new Error("网络分类不是5个场景");

  await page.getByRole("button", { name: "触发这次演练" }).click();
  await page.waitForFunction(() => document.querySelector("#drillCheckpointTitle")?.textContent === "先核对端口服务与配置");
  const timeline = await page.locator("#drillStepList").innerText();
  if (!timeline.includes("接入记录") || !timeline.includes("治理结论")) throw new Error("审计时间线缺少接入或治理记录");
  if (!(await page.locator("#drillLocation").innerText()).includes("机架位")) throw new Error("物理定位卡缺少机架位");
  if ((await page.locator("#drillImpactPath > div").count()) < 2) throw new Error("影响路径没有形成节点链");

  const nextTitles = ["更换模块并观察", "恢复验证"];
  for (let index = 0; index < 3; index += 1) {
    await page.locator("#drillActionChoices input").first().check();
    await page.getByRole("button", { name: "提交反馈并继续" }).click();
    if (index < 2) await page.waitForFunction((expected) => document.querySelector("#drillCheckpointTitle")?.textContent === expected, nextTitles[index]);
  }
  await page.locator("#drillResult:not([hidden])").waitFor();
  await page.getByRole("button", { name: "揭晓隐藏答案" }).click();
  await page.locator("#drillTruth:not([hidden])").waitFor();
  if (!(await page.locator("#drillTruth").innerText()).includes("本端光模块性能退化")) throw new Error("定向演练答案与分支不一致");
  await page.screenshot({ path: "/tmp/idc-ai-ops-drill-desktop.png", fullPage: false });

  await page.locator('#drillStartForm input[value="blind"]').check();
  if (await page.locator("#drillCatalogList").isVisible()) throw new Error("盲测时仍显示故障候选列表");
  await page.getByRole("button", { name: "触发这次演练" }).click();
  await page.locator("#drillCheckpoint:not([hidden])").waitFor();
  if (!(await page.locator("#drillRunName").innerText()).includes("运行中隐藏")) throw new Error("盲测运行中显示了场景名称");
  if (await page.locator("#drillTruth").isVisible()) throw new Error("盲测运行中显示了隐藏答案");

  await page.selectOption("#roleSelect", "onsite_operator");
  if (await page.locator("#drillRailButton").isVisible()) throw new Error("现场角色不应看到故障演练入口");
  await page.selectOption("#roleSelect", "ai_admin");

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  mobile.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`mobile: ${message.text()}`);
  });
  mobile.on("pageerror", (error) => consoleErrors.push(`mobile: ${error.message}`));
  await mobile.goto(baseURL, { waitUntil: "networkidle" });
  await mobile.selectOption("#roleSelect", "ai_admin");
  await mobile.getByRole("button", { name: /故障演练/ }).click();
  await mobile.locator("#drillDialog").waitFor({ state: "visible" });
  const box = await mobile.locator("#drillDialog").boundingBox();
  if (!box || box.width > 390) throw new Error("移动端演练窗口超出视口");
  await mobile.locator("[data-drill-run-id]").first().click();
  await mobile.locator("#drillActive:not([hidden])").waitFor();
  await mobile.waitForTimeout(500);
  if (!(await mobile.locator("#drillLocation").isVisible())) throw new Error("移动端看不到物理定位卡");
  await mobile.screenshot({ path: "/tmp/idc-ai-ops-drill-mobile.png", fullPage: false });
  await mobile.close();

  if (consoleErrors.length) throw new Error(`页面控制台错误：${consoleErrors.join(" | ")}`);
  console.log(JSON.stringify({
    status: "passed",
    directed: "resolved_and_revealed",
    blind: "answer_hidden",
    role_gate: "passed",
    desktop_screenshot: "/tmp/idc-ai-ops-drill-desktop.png",
    mobile_screenshot: "/tmp/idc-ai-ops-drill-mobile.png",
  }));
} finally {
  await browser.close();
}
