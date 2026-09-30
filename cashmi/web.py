"""A small HTTP server: static page plus a JSON snapshot endpoint.

Uses only the standard library, so the whole HMI needs one dependency
(`asyncua`) and works on a machine with no internet access.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from . import tags as T
from .opc import History

log = logging.getLogger("cashmi.web")

STATIC_DIR = Path(__file__).parent / "static"


def snapshot(history: History) -> Dict[str, Any]:
    """Everything the page needs, in one response."""
    return {
        "connected": history.connected,
        "error": history.error,
        "latest": history.latest,
        "fast": list(history.fast),
        "slow": list(history.slow),
        "meta": {
            "fast_window_s": T.FAST_WINDOW_S,
            "slow_window_s": T.SLOW_WINDOW_S,
            "tau_level_s": round(T.TAU_LEVEL_S, 1),
            "tau_oxygen_s": round(T.TAU_OXYGEN_S, 1),
            "n_inlets": T.N_INLETS,
            "pump_states": T.PUMP_STATE_NAMES,
            "units": {t.key: t.unit for t in T.ALL_TAGS},
            "labels": {t.key: t.label for t in T.ALL_TAGS},
        },
    }


def make_handler(history: History):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter default log
            log.debug(fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]

            if path == "/api/snapshot":
                body = json.dumps(snapshot(history)).encode("utf-8")
                self._send(200, body, "application/json")
                return

            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (STATIC_DIR / name).resolve()
            if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
                self._send(404, b"not found", "text/plain")
                return

            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), content_type)

    return Handler


def serve(history: History, host: str, port: int) -> ThreadingHTTPServer:
    """Start the web server on a background thread and return it."""
    server = ThreadingHTTPServer((host, port), make_handler(history))
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="web")
    thread.start()
    return server
