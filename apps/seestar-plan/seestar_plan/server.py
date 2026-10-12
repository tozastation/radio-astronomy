"""計画の PNG と JSON を返す。SeeStar へは接続しない。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from seestar_plan.plan import JST, Site, build_plan, plan_as_dict
from seestar_plan.plot import render_page, render_png

PORT = int(os.environ.get("PORT", "30089"))


def load_site() -> Site:
    path = os.environ.get("SEESTAR_SITE_FILE")
    if path:
        return Site.from_file(path)
    return Site(
        latitude_deg=float(os.environ.get("OBSERVER_LATITUDE", "35.6895")),
        longitude_deg=float(os.environ.get("OBSERVER_LONGITUDE", "139.6917")),
        alt_ridge_deg=float(os.environ.get("ALT_RIDGE_DEG", "10")),
        alt_max_deg=float(os.environ.get("ALT_MAX_DEG", "85")),
    )


def current_plan(now: datetime | None = None):
    moment = now or datetime.now(JST)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    return build_plan(moment, load_site())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/healthz", "/health"):
            self._bytes(b"ok", "text/plain; charset=utf-8")
            return
        plan = current_plan()
        if path == "/plan.json":
            body = json.dumps(plan_as_dict(plan), ensure_ascii=False).encode()
            self._bytes(body, "application/json; charset=utf-8")
            return
        if path == "/plan.png":
            self._bytes(render_png(plan), "image/png")
            return
        if path in ("/", "/index.html"):
            self._bytes(render_page(plan).encode(), "text/html; charset=utf-8")
            return
        self.send_error(404)

    def _bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[seestar-plan] {self.address_string()} {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"seestar-plan listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
