#!/usr/bin/env python3
"""Grafana 衛星追尾ダッシュボードのスクリーンショットを自動撮影するスクリプト"""

import sys
import time
from playwright.sync_api import sync_playwright

def main():
    print("📸 Playwright を起動しています...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=2,  # Retina 高精細
        )
        page = context.new_page()

        # 1. Grafana ログイン
        print("🔑 Grafana にログイン中 (http://localhost:30080/login)...")
        page.goto("http://localhost:30080/login", wait_until="networkidle")
        
        # ログインフォーム入力
        page.fill("input[name='user']", "admin")
        page.fill("input[name='password']", "admin")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
        print("✅ ログイン成功！")

        # 2. ダッシュボードへ移動 (kiosk モードで余計なUIを非表示)
        dashboard_url = "http://localhost:30080/d/satellite-radio-tracker/c97e60f?kiosk"
        print(f"📊 ダッシュボードを開いています: {dashboard_url}")
        page.goto(dashboard_url, wait_until="networkidle")

        # 画面を少しずつスクロールして遅延読み込み(Lazy load)を完了させる
        print("⏳ 画面全体をスクロールしてパネルを描画中...")
        for y in range(0, 2500, 400):
            page.mouse.wheel(0, 400)
            time.sleep(1)

        # トップに戻る
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(3)

        # 3. スクリーンショット撮影
        # ① ファーストビュー
        overview_path = "docs/images/grafana_satellite_tracker_overview.png"
        print(f"📷 ファーストビューを撮影中: {overview_path}")
        page.screenshot(path=overview_path, full_page=False)

        # ② 全体フルページ
        full_path = "docs/images/grafana_satellite_tracker_full.png"
        print(f"📷 全体フルページを撮影中: {full_path}")
        page.screenshot(path=full_path, full_page=True)

        # ③ ドップラーS字カーブパネルのズーム
        try:
            doppler_panel = page.locator("div[data-panelid='6']")
            if doppler_panel.count() > 0:
                doppler_path = "docs/images/grafana_doppler_scurve.png"
                print(f"📷 ドップラーS字カーブパネルを撮影中: {doppler_path}")
                doppler_panel.first.screenshot(path=doppler_path)
        except Exception as e:
            print(f"⚠️ パネル拡大撮影スキップ: {e}")

        browser.close()
        print("🎉 すべてのスクリーンショット撮影が完了しました！")

if __name__ == "__main__":
    main()
