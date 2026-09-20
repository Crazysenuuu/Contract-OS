"""Fake SMS gateway for local end-to-end verification.

Serves the exact contract ``sms_sender_service.send_sms`` calls:

    POST /
    Authorization: Bearer <key>
    {"to": "<phone>", "from": "<sender>", "text": "<body>"}

Every accepted message is stored in memory and is visible via:
    GET  /messages          -> {"messages": [...]}
    POST /reset             -> clears the log
    GET  /health            -> liveness
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MESSAGES: list[dict] = []
LOCK = threading.Lock()
EXPECTED_KEY = "test-gateway-key-123"


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "stored": len(MESSAGES)})
        elif self.path == "/messages":
            with LOCK:
                self._json(200, {"messages": list(MESSAGES)})
        else:
            self._json(404, {"detail": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"detail": "bad json"})
            return

        if self.path == "/reset":
            with LOCK:
                MESSAGES.clear()
            self._json(200, {"ok": True})
            return

        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {EXPECTED_KEY}":
            self._json(401, {"detail": "bad gateway key"})
            return

        # Mirror the real gateway field names the backend sends.
        record = {
            "to": payload.get("to"),
            "from": payload.get("from"),
            "text": payload.get("text"),
            "received_at": time.time(),
        }
        with LOCK:
            MESSAGES.append(record)
        self._json(200, {"message_id": f"fake-{len(MESSAGES)}"})

    def log_message(self, fmt, *args):  # silence per-request stderr
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 9911), Handler)
    print("fake sms gateway listening on http://127.0.0.1:9911", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
