"""Tiny HTTP server: /healthz (liveness), /readyz (readiness) and /metrics (Prometheus)."""
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .metrics import stale

log = logging.getLogger(__name__)
ready = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/metrics":
            self._send(200, generate_latest(), CONTENT_TYPE_LATEST)
        elif path in ("/healthz", "/livez"):
            bad = stale()
            self._send(503 if bad else 200, (f"stale: {', '.join(bad)}" if bad else "ok").encode())
        elif path == "/readyz":
            self._send(200 if ready.is_set() else 503, b"ready" if ready.is_set() else b"starting")
        else:
            self._send(404, b"not found")

    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):          # keep probe noise out of the logs
        pass


def serve(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
    log.info("health + metrics on :%d", port)
    return server
