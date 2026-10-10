#!/usr/bin/env python3
"""
🛰️ Satellite Observation Viewer (Garage S3 Dashboard)
Ultra-lightweight web dashboard for mobile and desktop browsing.
"""

import os
import json
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import boto3
from botocore.client import Config

S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "http://garage-s3.storage.svc.cluster.local:3900")
S3_BUCKET = os.environ.get("S3_BUCKET", "satellite-recordings")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
PORT = int(os.environ.get("PORT", "8080"))

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
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      padding: 16px;
      padding-bottom: 40px;
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
    .pass-list { display: flex; flex-direction: column; gap: 16px; }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
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
    .pass-id { font-size: 0.75rem; color: var(--text-muted); font-family: monospace; }
    .badge-status {
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(52, 211, 153, 0.15);
      color: var(--success);
      border: 1px solid rgba(52, 211, 153, 0.3);
    }
    .card-body { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
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
      background: rgba(0,0,0,0.6);
      color: #fff;
      font-size: 0.7rem;
      padding: 2px 6px;
      border-radius: 4px;
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
    .packets-box {
      background: rgba(0, 0, 0, 0.3);
      padding: 10px;
      border-radius: 8px;
      font-size: 0.8rem;
      border: 1px solid rgba(255,255,255,0.05);
    }
    .packets-title {
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 6px;
      display: flex;
      justify-content: space-between;
    }
    .packets-list {
      max-height: 120px;
      overflow-y: auto;
      font-family: monospace;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .packet-item {
      padding: 4px 6px;
      background: rgba(255,255,255,0.03);
      margin-bottom: 4px;
      border-radius: 4px;
      border-left: 2px solid var(--accent);
    }
    .files-list {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 4px;
    }
    .file-btn {
      background: #334155;
      color: #e2e8f0;
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 0.75rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .file-btn:active { background: #475569; }
    /* Modal */
    .modal {
      display: none;
      position: fixed;
      top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0,0,0,0.9);
      z-index: 9999;
      justify-content: center;
      align-items: center;
      padding: 10px;
    }
    .modal.active { display: flex; }
    .modal img { max-width: 100%; max-height: 95vh; object-fit: contain; }
    .modal-close {
      position: absolute;
      top: 15px; right: 20px;
      color: white; font-size: 32px; font-weight: bold; cursor: pointer;
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

  <div id="imageModal" class="modal" onclick="closeModal()">
    <span class="modal-close">&times;</span>
    <img id="modalImg" src="" alt="Spectrogram Full">
  </div>

  <script>
    function openModal(src) {
      document.getElementById('modalImg').src = src;
      document.getElementById('imageModal').classList.add('active');
    }
    function closeModal() {
      document.getElementById('imageModal').classList.remove('active');
    }
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
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
                      <img class="spectrogram-img" src="{img_url}" alt="Spectrogram" onclick="openModal('{img_url}')">
                      <span class="spectrogram-hint">🔍 タップで拡大</span>
                    </div>
                    """

                summary = p.get("summary") or {}
                status = summary.get("status", "completed")
                packets_count = summary.get("packets_count", 0)

                # パケット一覧
                packets_data = p.get("packets") or {}
                packet_items = packets_data.get("packets", [])
                packets_html = ""
                if packet_items:
                    p_list = "".join([f'<div class="packet-item">{p_txt}</div>' for p_txt in packet_items])
                    packets_html = f"""
                    <div class="packets-box">
                      <div class="packets-title"><span>APRS 受信パケット ({len(packet_items)} 件)</span></div>
                      <div class="packets-list">{p_list}</div>
                    </div>
                    """
                else:
                    packets_html = f"""
                    <div class="packets-box">
                      <div class="packets-title"><span>APRS パケット</span><span style="color:var(--text-muted)">0 件</span></div>
                    </div>
                    """

                # ダウンロードリンク
                file_links = []
                for fname, fkey in p["files"].items():
                    dl_url = f"/download?key={urllib.parse.quote(fkey)}"
                    file_links.append(f'<a class="file-btn" href="{dl_url}">📥 {fname}</a>')
                files_html = "".join(file_links)

                card = f"""
                <div class="card">
                  <div class="card-header">
                    <div>
                      <div class="sat-name">{p['satellite']}</div>
                      <div class="pass-id">{p['pass_id']}</div>
                    </div>
                    <span class="badge-status">{status}</span>
                  </div>
                  <div class="card-body">
                    {spectrogram_html}
                    <div class="meta-grid">
                      <div class="meta-item">
                        <span class="meta-label">観測・処理時刻</span>
                        <span class="meta-val">{p['last_modified']}</span>
                      </div>
                      <div class="meta-item">
                        <span class="meta-label">APRS パケット検出</span>
                        <span class="meta-val">{packets_count} 件</span>
                      </div>
                    </div>
                    {packets_html}
                    <div class="files-list">
                      {files_html}
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
        # アクセスログを簡潔に出力
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, DashboardHandler)
    print(f"🚀 Satellite Viewer listening on http://0.0.0.0:{PORT} (Bucket: {S3_BUCKET})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()
