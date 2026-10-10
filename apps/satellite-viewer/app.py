#!/usr/bin/env python3
"""
🛰️ Satellite Observation Viewer (Garage S3 Dashboard)
Ultra-lightweight web dashboard for mobile and desktop browsing.
"""

import os
import json
import urllib.parse
import threading
import asyncio
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import boto3
from botocore.client import Config
from temporalio.client import Client

S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://garage-s3.storage.svc.cluster.local:3900")
S3_BUCKET = os.environ.get("S3_BUCKET", "satellite-recordings")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))
TEMPORAL_HOST = os.environ.get("TEMPORAL_HOST", "temporal-server.temporal.svc.cluster.local:7233")
TASK_QUEUE = "satellite-analysis"
ENABLE_AUTO_DISPATCH = os.environ.get("ENABLE_AUTO_DISPATCH", "true").lower() in ["true", "1", "yes"]

triggered_passes = set()

s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name="garage",
    config=Config(s3={"addressing_style": "path"})
)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>🛰️ 衛星電波観測ダッシュボード (Garage S3)</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.2);
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --success: #34d399;
      --warning: #fbbf24;
      --code-bg: #0b1120;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      padding: 16px;
      padding-bottom: 60px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border);
    }
    h1 {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--accent);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .badge-bucket {
      background: #0284c7;
      color: white;
      font-size: 0.75rem;
      padding: 3px 8px;
      border-radius: 999px;
      font-weight: 600;
    }
    .btn-refresh {
      background: var(--card-bg);
      color: var(--text);
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.85rem;
      cursor: pointer;
    }
    .btn-refresh:active { background: var(--border); }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
      margin-bottom: 16px;
    }
    .card-header {
      padding: 12px 16px;
      background: rgba(255, 255, 255, 0.03);
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
    }
    .sat-name { font-weight: 700; font-size: 1.1rem; color: #38bdf8; }
    .sat-header-left { display: flex; flex-direction: column; gap: 4px; }
    .sat-badges { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
    .badge-tag {
      font-size: 0.7rem;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 600;
    }
    .badge-spacestation { background: #0369a1; color: #e0f2fe; }
    .badge-weathersatellite { background: #047857; color: #d1fae5; }
    .badge-cubesat { background: #6d28d9; color: #ede9fe; }
    .badge-genericsatellite { background: #334155; color: #f1f5f9; }
    .badge-freq { background: #1e1b4b; color: #c7d2fe; border: 1px solid #3730a3; }
    .badge-signal { background: #312e81; color: #e0e7ff; }
    .summary-box {
      background: rgba(56, 189, 248, 0.08);
      border-left: 3px solid var(--accent);
      padding: 8px 12px;
      border-radius: 4px;
      font-size: 0.82rem;
      color: #bae6fd;
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .badge-status {
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(52, 211, 153, 0.15);
      color: var(--success);
      border: 1px solid rgba(52, 211, 153, 0.3);
    }
    .card-body { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
    .spectrogram-container {
      position: relative;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #000;
      text-align: center;
    }
    .spectrogram-img {
      width: 100%;
      height: auto;
      max-height: 280px;
      object-fit: contain;
      display: block;
      cursor: pointer;
    }
    .spectrogram-hint {
      position: absolute;
      bottom: 6px;
      right: 8px;
      background: rgba(0,0,0,0.7);
      color: #fff;
      font-size: 0.7rem;
      padding: 3px 8px;
      border-radius: 4px;
      pointer-events: none;
    }
    .meta-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      background: rgba(0, 0, 0, 0.2);
      padding: 10px;
      border-radius: 8px;
      font-size: 0.85rem;
    }
    .meta-item { display: flex; flex-direction: column; }
    .meta-label { color: var(--text-muted); font-size: 0.7rem; }
    .meta-val { font-weight: 600; margin-top: 2px; }

    /* JSON Details & Code View */
    details.data-accordion {
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }
    details.data-accordion summary {
      padding: 10px 12px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      color: var(--text);
      display: flex;
      justify-content: space-between;
      align-items: center;
      user-select: none;
    }
    details.data-accordion summary:hover {
      background: rgba(255, 255, 255, 0.03);
    }
    .accordion-content {
      padding: 12px;
      background: var(--code-bg);
      border-top: 1px solid var(--border);
    }
    pre.code-block {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.75rem;
      color: #38bdf8;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-all;
      max-height: 250px;
      padding: 4px 0;
    }

    /* File Actions */
    .files-section {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .files-title {
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--text-muted);
    }
    .files-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    @media (min-width: 640px) {
      .files-grid { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
    }
    .file-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 0.8rem;
    }
    .file-name {
      font-family: monospace;
      font-weight: 600;
      color: #e2e8f0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .file-actions {
      display: flex;
      gap: 6px;
    }
    .action-btn {
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: none;
    }
    .btn-view {
      background: #0284c7;
      color: white;
    }
    .btn-view:active { background: #0369a1; }
    .btn-download {
      background: #334155;
      color: #e2e8f0;
    }
    .btn-download:active { background: #475569; }

    /* Modals */
    .modal {
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.85);
      backdrop-filter: blur(4px);
      z-index: 9999;
      justify-content: center;
      align-items: center;
      padding: 16px;
    }
    .modal.active { display: flex; }
    .modal-box {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      width: 100%;
      max-width: 650px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
      overflow: hidden;
    }
    .modal-header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-title {
      font-size: 0.95rem;
      font-weight: 700;
      color: var(--accent);
      font-family: monospace;
    }
    .modal-close {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 24px;
      cursor: pointer;
      line-height: 1;
    }
    .modal-body {
      padding: 16px;
      overflow-y: auto;
      flex: 1;
    }
    .modal-img {
      max-width: 100%;
      max-height: 75vh;
      object-fit: contain;
      display: block;
      margin: 0 auto;
    }
    .modal-footer {
      padding: 10px 16px;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }
    .copy-btn {
      background: #334155;
      color: white;
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.8rem;
      cursor: pointer;
    }
    .empty-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--text-muted);
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>🛰️ 衛星電波観測ビューア</h1>
      <span class="badge-bucket">Garage S3: __BUCKET__</span>
    </div>
    <button class="btn-refresh" onclick="location.reload()">🔄 更新</button>
  </header>

  <main>
    __CONTENT__
  </main>

  <!-- Image Modal -->
  <div id="imageModal" class="modal" onclick="closeImageModal(event)">
    <div class="modal-box" style="background:transparent; border:none; box-shadow:none; max-width:95vw;">
      <div style="text-align:right; margin-bottom:8px;">
        <button class="modal-close" style="color:white; font-size:32px;" onclick="document.getElementById('imageModal').classList.remove('active')">&times;</button>
      </div>
      <img id="modalImg" class="modal-img" src="" alt="Spectrogram Full">
    </div>
  </div>

  <!-- Text / JSON Modal -->
  <div id="textModal" class="modal" onclick="closeTextModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
      <div class="modal-header">
        <span id="textModalTitle" class="modal-title">File Content</span>
        <button class="modal-close" onclick="closeTextModal()">&times;</button>
      </div>
      <div class="modal-body">
        <pre class="code-block"><code id="textModalContent">Loading...</code></pre>
      </div>
      <div class="modal-footer">
        <button class="copy-btn" onclick="copyModalContent()">📋 コピー</button>
        <button class="copy-btn" onclick="closeTextModal()">閉じる</button>
      </div>
    </div>
  </div>

  <script>
    function openImageModal(src) {
      document.getElementById('modalImg').src = src;
      document.getElementById('imageModal').classList.add('active');
    }
    function closeImageModal(e) {
      if (e.target.id === 'imageModal' || e.target.classList.contains('modal-close')) {
        document.getElementById('imageModal').classList.remove('active');
      }
    }

    async function openTextModal(filename, key) {
      const modal = document.getElementById('textModal');
      const title = document.getElementById('textModalTitle');
      const content = document.getElementById('textModalContent');
      title.textContent = filename;
      content.textContent = '読み込み中...';
      modal.classList.add('active');

      try {
        const res = await fetch('/content?key=' + encodeURIComponent(key));
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const text = await res.text();
        try {
          // JSON なら綺麗にインデント
          const parsed = JSON.parse(text);
          content.textContent = JSON.stringify(parsed, null, 2);
        } catch {
          content.textContent = text;
        }
      } catch (err) {
        content.textContent = 'エラー: ファイルを読み込めませんでした (' + err.message + ')';
      }
    }
    function closeTextModal() {
      document.getElementById('textModal').classList.remove('active');
    }
    function copyModalContent() {
      const text = document.getElementById('textModalContent').textContent;
      navigator.clipboard.writeText(text).then(() => {
        alert('クリップボードにコピーしました！');
      });
    }
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if path == "/api/pending-tasks":
            pending_count = 0
            try:
                resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="raw/")
                for item in resp.get("Contents", []):
                    if item["Key"].endswith(".wav"):
                        pending_count += 1
            except Exception as e:
                print(f"Error checking pending tasks: {e}", flush=True)

            body = json.dumps({"pending_count": pending_count}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/metrics":
            pending_count = 0
            try:
                resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="raw/")
                for item in resp.get("Contents", []):
                    if item["Key"].endswith(".wav"):
                        pending_count += 1
            except Exception:
                pass

            metrics_text = (
                f"# HELP satellite_pending_analysis_tasks Number of raw satellite recordings waiting for analysis\n"
                f"# TYPE satellite_pending_analysis_tasks gauge\n"
                f"satellite_pending_analysis_tasks {pending_count}\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(metrics_text)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(metrics_text)
            return

        if path == "/view":
            key = query.get("key", [None])[0]
            if not key:
                self.send_error(400, "Missing key parameter")
                return
            try:
                obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                content_type = obj.get("ContentType", "application/octet-stream")
                if key.endswith(".png"):
                    content_type = "image/png"
                elif key.endswith(".json"):
                    content_type = "application/json"

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(obj["ContentLength"]))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                
                body = obj["Body"]
                while chunk := body.read(65536):
                    self.wfile.write(chunk)
            except Exception as e:
                self.send_error(404, f"Object not found: {e}")
            return

        if path == "/content":
            key = query.get("key", [None])[0]
            if not key:
                self.send_error(400, "Missing key parameter")
                return
            try:
                obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                data = obj["Body"].read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(404, f"Object read error: {e}")
            return

        if path == "/download":
            key = query.get("key", [None])[0]
            if not key:
                self.send_error(400, "Missing key parameter")
                return
            try:
                obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
                filename = os.path.basename(key)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(obj["ContentLength"]))
                self.end_headers()
                
                body = obj["Body"]
                while chunk := body.read(65536):
                    self.wfile.write(chunk)
            except Exception as e:
                self.send_error(404, f"Object not found: {e}")
            return

        if path == "/" or path == "/index.html":
            self.render_dashboard()
            return

        self.send_error(404, "Not Found")

    def render_dashboard(self):
        try:
            # S3 内の results/ プレフィックスを走査
            paginator = s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=S3_BUCKET, Prefix="results/")
            
            passes = {}
            for page in pages:
                for item in page.get("Contents", []):
                    key = item["Key"]
                    parts = key.split("/")
                    # results/<satellite>/<pass_id>/<file>
                    if len(parts) >= 4:
                        sat = parts[1]
                        pass_id = parts[2]
                        filename = "/".join(parts[3:])
                        
                        if pass_id not in passes:
                            passes[pass_id] = {
                                "satellite": sat,
                                "pass_id": pass_id,
                                "last_modified": item["LastModified"].strftime("%Y-%m-%d %H:%M:%S UTC"),
                                "files": {},
                                "summary": None,
                                "packets": None
                            }
                        passes[pass_id]["files"][filename] = key

            # 各パスの summary.json と packets.json を読み込み
            for pass_id, pdata in passes.items():
                if "summary.json" in pdata["files"]:
                    try:
                        s_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=pdata["files"]["summary.json"])
                        pdata["summary"] = json.loads(s_obj["Body"].read().decode("utf-8"))
                    except Exception:
                        pass
                if "packets.json" in pdata["files"]:
                    try:
                        pk_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=pdata["files"]["packets.json"])
                        pdata["packets"] = json.loads(pk_obj["Body"].read().decode("utf-8"))
                    except Exception:
                        pass

            # 最新順にソート
            sorted_passes = sorted(passes.values(), key=lambda x: x["pass_id"], reverse=True)

            cards_html = []
            for p in sorted_passes:
                spectrogram_html = ""
                if "spectrogram.png" in p["files"]:
                    img_url = f"/view?key={urllib.parse.quote(p['files']['spectrogram.png'])}"
                    spectrogram_html = f"""
                    <div class="spectrogram-container">
                      <img class="spectrogram-img" src="{img_url}" alt="Spectrogram" onclick="openImageModal('{img_url}')">
                      <span class="spectrogram-hint">🔍 タップで拡大</span>
                    </div>
                    """

                summary = p.get("summary") or {}
                status = summary.get("status", "completed")
                packets_count = summary.get("packets_count", 0)
                sat = p['satellite']
                sat_upper = sat.upper()

                # メタデータのフォールバック解決
                sat_type = summary.get("satellite_type")
                signal_type = summary.get("signal_type")
                freq_label = summary.get("frequency_label")
                icon = summary.get("display_icon")
                summary_text = summary.get("summary_text", "")

                if not sat_type:
                    if "ISS" in sat_upper:
                        sat_type = "SpaceStation"
                        signal_type = "APRS / AX.25 (1200bps AFSK)"
                        freq_label = "145.825 MHz"
                        icon = "🚀"
                    elif "METEOR" in sat_upper:
                        sat_type = "WeatherSatellite"
                        signal_type = "LRPT (QPSK 72kbps)"
                        freq_label = "137.900 MHz"
                        icon = "🛰️"
                    elif "FUNCUBE" in sat_upper or "AO-73" in sat_upper:
                        sat_type = "CubeSat"
                        signal_type = "BPSK (1200bps Telemetry)"
                        freq_label = "145.935 MHz"
                        icon = "📻"
                    else:
                        sat_type = "GenericSatellite"
                        signal_type = "Audio / RF Spectrum"
                        freq_label = "-"
                        icon = "📡"

                summary_box_html = ""
                if summary_text:
                    summary_box_html = f"""
                    <div class="summary-box">
                      <span>{icon}</span>
                      <span>{summary_text}</span>
                    </div>
                    """

                # summary.json インラインアコーディオン
                summary_accordion = ""
                if summary:
                    pretty_summary = json.dumps(summary, indent=2, ensure_ascii=False)
                    summary_accordion = f"""
                    <details class="data-accordion">
                      <summary>📊 解析サマリ (summary.json) の中身を見る</summary>
                      <div class="accordion-content">
                        <pre class="code-block"><code>{pretty_summary}</code></pre>
                      </div>
                    </details>
                    """

                # packets.json インラインアコーディオン
                packets_accordion = ""
                packets_data = p.get("packets") or {}
                packet_items = packets_data.get("packets", [])
                if packet_items:
                    pretty_packets = "\n".join([str(item) for item in packet_items])
                    packets_accordion = f"""
                    <details class="data-accordion">
                      <summary>📡 受信パケット ({len(packet_items)} 件) の中身を見る</summary>
                      <div class="accordion-content">
                        <pre class="code-block"><code>{pretty_packets}</code></pre>
                      </div>
                    </details>
                    """
                elif "ISS" in sat_upper:
                    packets_accordion = f"""
                    <details class="data-accordion">
                      <summary>📡 APRS 受信パケット (0 件)</summary>
                      <div class="accordion-content">
                        <pre class="code-block"><code>パケットは検出されませんでした (0 packets)</code></pre>
                      </div>
                    </details>
                    """

                # 各ファイルの「👁️ 表示」＆「📥 保存」ボタン一覧
                file_rows = []
                for fname, fkey in p["files"].items():
                    dl_url = f"/download?key={urllib.parse.quote(fkey)}"
                    
                    if fname.endswith(".png"):
                        view_btn = f"""<button class="action-btn btn-view" onclick="openImageModal('/view?key={urllib.parse.quote(fkey)}')">👁️ 表示</button>"""
                    elif fname.endswith(".json") or fname.endswith(".txt"):
                        view_btn = f"""<button class="action-btn btn-view" onclick="openTextModal('{fname}', '{fkey}')">👁️ 中身を見る</button>"""
                    else:
                        view_btn = ""

                    file_row = f"""
                    <div class="file-row">
                      <span class="file-name">📄 {fname}</span>
                      <div class="file-actions">
                        {view_btn}
                        <a class="action-btn btn-download" href="{dl_url}">📥 保存</a>
                      </div>
                    </div>
                    """
                    file_rows.append(file_row)

                files_grid_html = "".join(file_rows)

                badge_class = f"badge-{sat_type.lower()}"

                card = f"""
                <div class="card">
                  <div class="card-header">
                    <div class="sat-header-left">
                      <div class="sat-name">{icon} {p['satellite']}</div>
                      <div class="sat-badges">
                        <span class="badge-tag {badge_class}">{sat_type}</span>
                        <span class="badge-tag badge-freq">{freq_label}</span>
                        <span class="badge-tag badge-signal">{signal_type}</span>
                      </div>
                      <div class="pass-id">{p['pass_id']}</div>
                    </div>
                    <span class="badge-status">{status}</span>
                  </div>
                  <div class="card-body">
                    {spectrogram_html}
                    {summary_box_html}
                    <div class="meta-grid">
                      <div class="meta-item">
                        <span class="meta-label">観測・処理時刻</span>
                        <span class="meta-val">{p['last_modified']}</span>
                      </div>
                      <div class="meta-item">
                        <span class="meta-label">受信信号 / パケット</span>
                        <span class="meta-val">{packets_count} 件検出</span>
                      </div>
                    </div>
                    {summary_accordion}
                    {packets_accordion}
                    <div class="files-section">
                      <div class="files-title">ファイル一覧 (中身プレビュー ＆ ダウンロード)</div>
                      <div class="files-grid">
                        {files_grid_html}
                      </div>
                    </div>
                  </div>
                </div>
                """
                cards_html.append(card)

            content = "".join(cards_html) if cards_html else '<div class="empty-state">まだ観測結果がありません。</div>'
            body = HTML_TEMPLATE.replace("__BUCKET__", S3_BUCKET).replace("__CONTENT__", content)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
        except Exception as e:
            self.send_error(500, f"Dashboard render error: {e}")

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


async def run_dispatcher():
    print(f"📡 [Dispatcher] Background dispatcher started (Temporal: {TEMPORAL_HOST})", flush=True)
    temporal_client = None

    while True:
        try:
            if temporal_client is None:
                try:
                    temporal_client = await Client.connect(TEMPORAL_HOST)
                    print(f"✅ [Dispatcher] Connected to Temporal Server at {TEMPORAL_HOST}", flush=True)
                except Exception as e:
                    await asyncio.sleep(5)
                    continue

            # S3 の raw/ プレフィックスをスキャン
            resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="raw/")
            for item in resp.get("Contents", []):
                key = item["Key"]
                if not key.endswith(".wav"):
                    continue
                parts = key.split("/")
                if len(parts) >= 3:
                    satellite = parts[1]
                    filename = parts[2]
                    pass_id = os.path.splitext(filename)[0]

                    if pass_id in triggered_passes:
                        continue

                    # 既に results/<satellite>/<pass_id>/summary.json が存在するか確認
                    summary_key = f"results/{satellite}/{pass_id}/summary.json"
                    try:
                        s3_client.head_object(Bucket=S3_BUCKET, Key=summary_key)
                        triggered_passes.add(pass_id)
                        continue
                    except Exception:
                        pass  # 未解析の生録音！

                    print(f"🚀 [Dispatcher] Found new raw recording: {key} ({satellite} / {pass_id})", flush=True)
                    print(f"⏳ [Dispatcher] Starting Temporal workflow for {pass_id}...", flush=True)

                    try:
                        import sys
                        if "/app/src" not in sys.path:
                            sys.path.insert(0, "/app/src")
                        from workflows import AnalyzeSatellitePassWorkflow, PassAnalysisParams
                        await temporal_client.start_workflow(
                            AnalyzeSatellitePassWorkflow.run,
                            PassAnalysisParams(
                                s3_key=key,
                                satellite=satellite,
                                pass_id=pass_id,
                                bucket_name=S3_BUCKET,
                                keep_raw=False
                            ),
                            id=f"analyze-{pass_id}",
                            task_queue=TASK_QUEUE
                        )
                        triggered_passes.add(pass_id)
                        print(f"🎉 [Dispatcher] Workflow started successfully for {pass_id}!", flush=True)
                    except Exception as wf_err:
                        if "already running" in str(wf_err).lower() or "already started" in str(wf_err).lower():
                            triggered_passes.add(pass_id)
                        print(f"⚠️ [Dispatcher] Failed to start workflow: {wf_err}", flush=True)

        except Exception as e:
            print(f"⚠️ [Dispatcher Error]: {e}", flush=True)

        await asyncio.sleep(8)


def start_background_dispatcher():
    if not ENABLE_AUTO_DISPATCH:
        print("ℹ️ [Dispatcher] Auto-dispatch is disabled by config.", flush=True)
        return

    def loop_runner():
        while True:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(run_dispatcher())
            except Exception as e:
                print(f"❌ [Dispatcher Thread Crashed]: {e}", flush=True)
                import traceback
                traceback.print_exc()
                import time
                time.sleep(5)

    t = threading.Thread(target=loop_runner, daemon=True)
    t.start()


if __name__ == "__main__":
    start_background_dispatcher()
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, DashboardHandler)
    print(f"🚀 Satellite Viewer listening on http://0.0.0.0:{PORT} (Bucket: {S3_BUCKET})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()
