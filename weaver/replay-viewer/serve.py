#!/usr/bin/env python3
"""HeroBench replay viewer HTTP server.

Plain `python3 -m http.server` sends no `Cache-Control`, so browsers
heuristically cache `index.html` and `viewer.js`. After a viewer redeploy the
browser keeps reading the OLD `index.html` (with its old `?v=` query), loads
the OLD cached `viewer.js`, and the new code never runs — which is exactly the
recurring "still no fog of war" symptom: stale viewer.js has no
`fog_timeline`, so `currentFog()` returns null and nothing is dimmed.

This server stamps `Cache-Control: no-cache` on every response so the browser
revalidates each file on every load. The `?v=N` cache-bust in index.html then
becomes belt-and-suspenders rather than load-bearing.

ThreadingHTTPServer so a slow large-trace fetch (live-*.jsonl can be >100 MB)
doesn't block concurrent requests for world.json / viewer.js.

Usage:  ./serve.py [port] [host]   (defaults: 8085 127.0.0.1)
"""
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8085
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), NoCacheHandler)
    print(f"viewer serving http://{host}:{port}/ (no-cache) — Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
