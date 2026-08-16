"""The view: a small HTTP server over the registry.

Stdlib only, so it starts even when the benchmark venv is half-built.
It reads the registry, derives progress from each arm's results file, and
polls each arm's backend for the character's most recent actions. All of
that happens server-side, which is what keeps the page free of the CORS
problem the old replay viewer had to work around.

    GET /            the page
    GET /api/state   everything the page renders, as JSON
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import env_server
import progress
import registry
from paths import STATIC_DIR


def build_state() -> dict:
    servers = []
    for record in registry.all_servers():
        record.pop("_path", None)
        port = record.get("port")
        record["healthy"] = bool(port) and env_server.health(port)
        servers.append(record)

    runs = []
    for record in registry.all_runs():
        record.pop("_path", None)
        results_file = record.get("results_file")
        record["progress"] = (
            progress.summarize(
                results_file,
                record.get("expected_tasks"),
                record.get("difficulties"),
            )
            if results_file
            else {}
        )
        if record.get("state") == "running" and record.get("port"):
            record["actions"] = env_server.character_log(record["port"], amount=10)
        else:
            record["actions"] = []
        runs.append(record)

    runs.sort(key=lambda r: (r.get("state") != "running", -(r.get("started_at") or 0)))
    return {"now": time.time(), "servers": servers, "runs": runs}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet; the terminal belongs to the operator
        pass

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path == "/api/state":
            try:
                body = json.dumps(build_state()).encode("utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}).encode("utf-8")
                self._send(body, "application/json", 500)
                return
            self._send(body, "application/json")
            return

        name = "index.html" if path in ("/", "/index.html") else Path(path).name
        target = STATIC_DIR / name
        if not target.is_file():
            self._send(b"not found", "text/plain", 404)
            return
        kind = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")
        self._send(target.read_bytes(), kind)


def serve(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    # All interfaces by default, with ufw scoping arrival to 192.168.0.0/24.
    # See the bind note in env_server.start: a subnet is a firewall's to
    # express and cannot be said in a bind.
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    serve(args.host, args.port)
