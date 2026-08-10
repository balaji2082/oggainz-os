#!/usr/bin/env python3
"""
Editable finance dashboard — tiny local server.

Why this exists: a dashboard opened as a file:// page CANNOT write to Notion. Notion's
API sends no CORS headers for browser origins, so the fetch is blocked outright, and a
page holding the integration token would be a secret sitting in a file anyone could open.
So the page talks to this server on localhost, and this server — which already has the
token — talks to Notion.

Run it with:  Edit-Dashboard.bat   (or:  python finance/serve_dashboard.py)

Stop it with Ctrl+C, or just close the window. While it is NOT running, dashboard.html
still opens normally as a plain file — it just hides the editing controls.

Listens on the local network as well as localhost, so a phone on the same wifi can use it.
There is no password: anyone on your wifi who knows the address can read and change your
finance rows while this is running. On home wifi that's fine. On shared or public wifi,
start it with  --local-only .
"""

import argparse
import json
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions  # noqa: E402
import build_finance as bf  # noqa: E402

PORT = 8770

# Everything this server can DO lives in actions.py, because the Money tab in the
# Command Center serves the same dashboard to a phone and has to behave
# identically — two copies of the category rules would drift the first time one of
# them was fixed. This file is now only: HTTP in, JSON out.
STATE = actions.STATE


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stdout.write("  %s %s\n" % (self.command, self.path))

    # ---- helpers
    def send_json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

    def send_html(self):
        path = bf.OUT_HTML
        if not os.path.exists(path):
            self.send_json({"error": "dashboard.html not built yet"}, 500)
            return
        with open(path, "rb") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    # ---- routes
    # Deliberately thin: every action returns (payload, status), which is exactly
    # send_json's signature, so these just splat it straight through.
    def do_GET(self):
        route = self.path.split("?")[0]
        if route in ("/", "/index.html", "/dashboard.html"):
            return self.send_html()
        if route == "/api/schema":
            return self.send_json(*(actions.schema()))
        if route == "/api/lists":
            db = "business"
            if "?" in self.path:
                from urllib.parse import parse_qs
                db = (parse_qs(self.path.split("?", 1)[1]).get("db") or ["business"])[0]
            return self.send_json(*(actions.lists(db)))
        if route == "/api/ping":
            return self.send_json({"ok": True, "editable": True})
        if route == "/api/rows":
            return self.send_json(*(actions.rows()))
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        route = self.path.split("?")[0]
        try:
            body = self.read_json()
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "bad JSON"}, 400)

        if route == "/api/rebuild":
            return self.send_json(*(actions.rebuild()))
        name = route[len("/api/"):] if route.startswith("/api/") else ""
        fn = actions.POST_ACTIONS.get(name)
        if not fn:
            return self.send_json({"error": "not found"}, 404)
        return self.send_json(*fn(body))


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="Serve the finance dashboard, editable.")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--local-only", action="store_true",
                    help="bind to localhost only — nothing on the wifi can reach it")
    ap.add_argument("--no-build", action="store_true",
                    help="serve the existing dashboard.html without refreshing first")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not bf.get_token():
        raise SystemExit(
            "\n  No .notion_token found — run Connect-Notion.bat first.\n")

    if not args.no_build:
        print("\n  Refreshing from Notion first ...")
        bf.main()

    # One call finds the databases (including appa's, if it has been shared) and
    # loads the live select options. The Command Center calls the same function.
    ok, why = actions.connect()
    if not ok:
        raise SystemExit(f"\n  {why}\n")

    host = "127.0.0.1" if args.local_only else "0.0.0.0"
    httpd = ThreadingHTTPServer((host, args.port), Handler)
    httpd.daemon_threads = True

    url = f"http://localhost:{args.port}/"
    print("\n  " + "-" * 52)
    print("   OG GAINZ MONEY — editable dashboard is running")
    print("  " + "-" * 52)
    print(f"   On this laptop : {url}")
    if not args.local_only:
        ip = lan_ip()
        if ip:
            print(f"   On your phone  : http://{ip}:{args.port}/")
            print("                    (same wifi, laptop must stay awake)")
        print("\n   No password — anyone on your wifi can open this while it runs.")
        print("   Use  --local-only  on shared wifi.")
    print("\n   Edits save straight to Notion. Close this window to stop.")
    print("  " + "-" * 52 + "\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")


if __name__ == "__main__":
    main()
