#!/usr/bin/env python3
"""Real-browser product flow used by the final verification pass."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)
BASE_URL = os.getenv("IDCAI_BASE_URL", "http://127.0.0.1:8765")


def main() -> None:
    console_errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 980})
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto(BASE_URL, wait_until="networkidle")
        page.get_by_role("heading", name="故障队列").wait_for()

        page.get_by_role("button", name="运行演练").first.click()
        page.get_by_role("button", name="运行这个场景").first.click()
        page.locator(".incident-row").first.wait_for()
        page.locator(".cc-alert").wait_for()
        assert "请立即按现有 CC 流程拨打电话" in page.locator(".cc-alert").inner_text()
        assert "G3M02179543" in page.locator(".identity-strip").inner_text()
        assert "规则＋知识" in page.locator(".capability-banner").inner_text()
        assert "模拟数据" in page.locator(".simulation-badge").inner_text()
        assert page.locator(".evidence-node").count() >= 7
        assert "外部输入提供了相同 incident_key" in page.locator(".correlation-panel").text_content()
        assert "%" not in page.locator(".hypothesis-list").inner_text()
        page.wait_for_timeout(3200)
        page.screenshot(path=str(REPORTS / "browser-desktop.png"), full_page=True)

        page.get_by_role("button", name="接入数据").first.click()
        page.locator("#logForm input[name='sn']").fill("BROWSER-FULL-SN-20260823")
        page.locator("#logForm input[name='rack_position']").fill("BJYZD9-C-23-01")
        page.locator("#logForm input[name='device_name']").fill("bjyz-browser-check")
        page.locator("#logForm input[name='summary']").fill("浏览器测试磁盘错误")
        page.locator("#logText").fill("kernel: blk_update_request: I/O error, dev sdd")
        page.get_by_role("button", name="分析这份日志").click()
        page.get_by_text("BROWSER-FULL-SN-20260823", exact=True).first.wait_for()
        assert "BROWSER-FULL-SN-20260823" in page.locator(".identity-strip").inner_text()
        assert "用户粘贴或上传日志" in page.locator(".intake-list").first.inner_text()
        assert "STORAGE-IO-001" in page.locator(".investigation-section").text_content()
        assert "尚未通过真实工具或人工检查确认" in page.locator(".conclusion-board").inner_text()
        page.get_by_role("button", name="开始处理").click()
        page.get_by_text("处理中", exact=True).last.wait_for()

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(BASE_URL, wait_until="networkidle")
        mobile.locator(".incident-row").first.wait_for()
        mobile.locator(".capability-banner").wait_for()
        assert mobile.locator(".evidence-node").count() >= 7
        mobile.screenshot(path=str(REPORTS / "browser-mobile.png"), full_page=True)
        browser.close()

    if console_errors:
        raise AssertionError("Browser console errors: " + " | ".join(console_errors))
    print("Browser flow passed: capability disclosure, trace, correlation, knowledge, CC boundary, ingestion, full SN, status, mobile")


if __name__ == "__main__":
    main()
