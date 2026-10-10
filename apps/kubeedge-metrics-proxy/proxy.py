#!/usr/bin/env python3
"""
KubeEdge Edged Metrics Proxy (https://192.168.68.66:10350 -> http://0.0.0.0:10351)
Enables Prometheus to scrape KubeEdge cAdvisor metrics across cluster networks.
"""

import ssl
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

LISTEN_PORT = 19095
TARGET_URL_BASE = "https://192.168.68.66:10350"

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

class ProxyHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        target_url = f"{TARGET_URL_BASE}{self.path}"
        try:
            req = urllib.request.Request(target_url)
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
                data = resp.read()
                print(f"[Proxy] GET {self.path} -> {resp.status} ({len(data)} bytes)", flush=True)
                self.send_response(resp.status)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
        except Exception as e:
            print(f"[Proxy Error] GET {self.path}: {e}", flush=True)
            self.send_error(502, f"Bad Gateway: {e}")

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server_address = ("0.0.0.0", LISTEN_PORT)
    httpd = ThreadingHTTPServer(server_address, ProxyHandler)
    print(f"🚀 KubeEdge Metrics Proxy listening on 0.0.0.0:{LISTEN_PORT} -> {TARGET_URL_BASE}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()
