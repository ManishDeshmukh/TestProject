"""Dashboard server: FastAPI when available, stdlib http.server fallback.

Production path uses FastAPI + uvicorn + WebSocket streaming.  When FastAPI is not
installed the *same* :class:`DashboardAPI` is served by a stdlib
``ThreadingHTTPServer`` with an HTTP ``POST /api/chat`` fallback for the chatbot.
Either way a single self-contained ``index.html`` is served — no CDN, no npm (P1).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .routes import DashboardAPI
from . import ws as ws_mod

STATIC_DIR = Path(__file__).with_name("static")
INDEX_HTML = STATIC_DIR / "index.html"

try:
    import uvicorn  # type: ignore
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # type: ignore
    from fastapi.responses import HTMLResponse, JSONResponse  # type: ignore
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app factory
# ─────────────────────────────────────────────────────────────────────────────
def build_fastapi_app(coordinator):
    api = DashboardAPI(coordinator)
    app = FastAPI(title="JobPilot Dashboard")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML.read_text()

    @app.get("/api/summary")
    def summary():
        return api.summary()

    @app.get("/api/queues")
    def queues():
        return api.queues()

    @app.get("/api/disks")
    def disks():
        return api.disks()

    @app.get("/api/licenses")
    def licenses():
        return api.licenses()

    @app.get("/api/jobs")
    def jobs(page: int = 1, status: str = None, queue: str = None):
        return api.jobs(page, status, queue)

    @app.get("/api/analysis")
    def analysis(limit: int = 8):
        return api.analysis(limit)

    @app.get("/api/warnings")
    def warnings():
        return api.warnings()

    @app.get("/api/agents")
    def agents():
        return api.agents()

    @app.get("/api/analytics")
    def analytics():
        return api.analytics()

    @app.get("/api/history")
    def history(page: int = 1, tool: str = None, status: str = None):
        return api.history(page, tool, status)

    @app.post("/api/chat")
    async def chat(payload: dict):
        return api.chat(payload.get("text", ""), payload.get("session", "default"))

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                await ws_mod.stream_reply(
                    websocket, coordinator.chatbot,
                    data.get("text", ""), data.get("session", "default"))
        except WebSocketDisconnect:
            return

    return app


# ─────────────────────────────────────────────────────────────────────────────
# stdlib fallback server
# ─────────────────────────────────────────────────────────────────────────────
def _make_handler(api):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default logging
            pass

        def _send(self, code, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.read_text(), "text/html")
                return
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            result = api.dispatch(parsed.path, params)
            if result is None:
                self._send(404, json.dumps({"error": "not found"}))
            else:
                self._send(200, json.dumps(result))

        def do_POST(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                payload = {}
            if parsed.path == "/api/chat":
                res = api.chat(payload.get("text", ""), payload.get("session", "default"))
                self._send(200, json.dumps(res))
            else:
                self._send(404, json.dumps({"error": "not found"}))

    return Handler


class DashboardServer:
    def __init__(self, coordinator):
        self.co = coordinator
        self.api = DashboardAPI(coordinator)
        self.host = coordinator.cfg.get("coordinator", "dashboard_host", "localhost")
        self.port = coordinator.cfg.getint("coordinator", "dashboard_port", 8765)
        self.backend = "fastapi" if _HAVE_FASTAPI else "stdlib"
        self._httpd = None
        self._thread = None

    def serve_background(self):
        """Start the server in a daemon thread; returns immediately."""
        self._thread = threading.Thread(target=self.serve, daemon=True)
        self._thread.start()
        return self._thread

    def serve(self):
        if _HAVE_FASTAPI:
            app = build_fastapi_app(self.co)
            uvicorn.run(app, host=self.host, port=self.port, log_level="warning")
        else:
            from http.server import ThreadingHTTPServer
            self._httpd = ThreadingHTTPServer((self.host, self.port), _make_handler(self.api))
            self._httpd.serve_forever()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
