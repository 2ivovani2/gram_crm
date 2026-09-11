"""Progress probes, independent of PostgreSQL/Valkey availability."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic


class WorkerHealth:
    def __init__(self, slots: dict[str, float]) -> None:
        self.slots = slots
        self.progress = dict.fromkeys(slots, monotonic())
        self.dependencies = dict.fromkeys(slots, False)

    def mark(self, slot: str, *, healthy: bool | None = None) -> None:
        self.progress[slot] = monotonic()
        if healthy is not None:
            self.dependencies[slot] = healthy

    def live(self) -> bool:
        now = monotonic()
        return all(now - self.progress[slot] < timeout for slot, timeout in self.slots.items())

    def ready(self) -> bool:
        return self.live() and all(self.dependencies.values())

    def start(self, port: int = 9091) -> ThreadingHTTPServer:
        health = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path not in ("/health/live", "/health/ready"):
                    self.send_error(404)
                    return
                ok = health.live() if self.path == "/health/live" else health.ready()
                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok" if ok else b"unavailable")

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
