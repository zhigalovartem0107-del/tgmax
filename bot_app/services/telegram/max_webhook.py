"""Приём событий MAX и запись на диск до подтверждения доставки."""

import hmac
import json
import sqlite3
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

from .max_source import MAX_EVENTS

PATH = "/max-webhook"


@contextmanager
def receiver(path, port, secret):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *_):
            pass

        def respond(self, status):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self.respond(200 if self.path == "/health" else 404)

        def do_POST(self):
            if self.path != PATH:
                return self.respond(404)
            supplied = self.headers.get("X-Max-Bot-Api-Secret", "").encode()
            if not hmac.compare_digest(secret.encode(), supplied):
                return self.respond(403)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 512_000:
                    return self.respond(400)
                event = json.loads(self.rfile.read(length))
            except (OSError, ValueError):
                return self.respond(400)
            if not isinstance(event, dict) or event.get("update_type") not in MAX_EVENTS:
                return self.respond(200)
            if event["update_type"] == "message_created" and (
                    ((event.get("message") or {}).get("recipient") or {}).get("chat_type") != "channel"):
                return self.respond(200)
            db = sqlite3.connect(path, timeout=3)
            try:
                with db:
                    db.execute("INSERT INTO max_inbox(body) VALUES (?)", (json.dumps(event, ensure_ascii=False),))
            except sqlite3.Error:
                return self.respond(503)
            finally:
                db.close()
            self.respond(200)

    with HTTPServer(("0.0.0.0", port), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()
