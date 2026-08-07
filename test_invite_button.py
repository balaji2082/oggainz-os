"""
Tests for the "Invite to app" button and the customer-info endpoint.

    python test_invite_button.py            (button logic, mocked app)
    OG_APP_URL=http://localhost:3000 OG_ADMIN_TOKEN=... python test_invite_button.py
                                            (also hits the real app)

Copies oggainz.db to a throwaway file first. Refuses to run against the live one.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_invitetest.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
if os.path.exists(LIVE_DB):
    shutil.copyfile(LIVE_DB, _tmp)
os.environ["OG_DB_PATH"] = _tmp
os.environ["OG_API_TOKEN"] = "test-service-token"
os.environ.setdefault("OG_PASSWORD", "test-owner-pw")
os.environ.setdefault("OG_SECRET_KEY", "test-secret")

# A stand-in for the app, so the button can be tested without Vercel or Neon.
CALLS = []


class FakeApp(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        CALLS.append({"path": self.path, "body": body,
                      "token": self.headers.get("X-OG-Admin-Token")})
        if FakeApp.mode == "conflict":
            payload, code = {"ok": False, "error": "already_linked",
                             "message": "This customer already has an account."}, 409
        elif FakeApp.mode == "boom":
            payload, code = {"ok": False, "error": "kaboom"}, 500
        else:
            payload, code = {"ok": True, "url": "https://oggainz.com/join/TESTTOKEN",
                             "expires_days": 7}, 200
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), FakeApp)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["OG_APP_URL"] = f"http://127.0.0.1:{server.server_port}"
os.environ["OG_ADMIN_TOKEN"] = "test-admin-token"

sys.path.insert(0, APP_DIR)
import db                                                          # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    sys.exit("REFUSING TO RUN: pointed at the live database.")

import app as A                                                    # noqa: E402

FAILS, N = [], 0


def check(label, cond, extra=""):
    global N
    N += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {extra}")
        FAILS.append(label)


A.app.config["TESTING"] = True
cl = A.app.test_client()
cl.post("/login", data={"password": "test-owner-pw"})

c = db.get_db()
cust = c.execute("SELECT id, name FROM customers LIMIT 1").fetchone()
CID, CNAME = cust["id"], cust["name"]
c.close()
print(f"testing with customer {CID} ({CNAME})\n")

print("== the customer-info endpoint ==")
r = cl.get(f"/api/v1/customer/{CID}",
           headers={"X-OG-Service-Token": "test-service-token"})
check("returns 200", r.status_code == 200)
j = r.get_json()
check("gives the name the app greets them with", j["customer"]["name"] == CNAME)
check("and nothing about money",
      not any(k in j["customer"] for k in ("total", "spent", "amount", "paid")),
      list(j["customer"]))
check("needs the token", cl.get(f"/api/v1/customer/{CID}").status_code == 401)
check("an unknown customer is a clean 404",
      cl.get("/api/v1/customer/NOPE",
             headers={"X-OG-Service-Token": "test-service-token"}).status_code == 404)

print("\n== the button ==")
CALLS.clear()
FakeApp.mode = "ok"
r = cl.post(f"/customers/{CID}/invite", follow_redirects=True)
check("the page comes back", r.status_code == 200)
check("the app was asked exactly once", len(CALLS) == 1, len(CALLS))
check("...at the invite endpoint", CALLS and CALLS[0]["path"] == "/api/admin/invite")
check("...with the admin token in a header",
      CALLS and CALLS[0]["token"] == "test-admin-token")
check("...sending ONLY the customer id",
      CALLS and list(CALLS[0]["body"]) == ["customer_id"], CALLS[0]["body"] if CALLS else None)
check("...and no personal data at all",
      CALLS and not any(k in json.dumps(CALLS[0]["body"]).lower()
                        for k in ("phone", "address", "name")))
body = r.get_data(as_text=True)
check("the link is shown on the page", "https://oggainz.com/join/TESTTOKEN" in body)
check("with a WhatsApp send button", "wa.me" in body)
check("and says it expires", "expires in 7 days" in body)

print("\n== when the customer already has an account ==")
FakeApp.mode = "conflict"
r = cl.post(f"/customers/{CID}/invite", follow_redirects=True)
body = r.get_data(as_text=True)
check("it says so plainly", "already has an account" in body)
check("and shows no link", "join/TESTTOKEN" not in body)

print("\n== when the app is unreachable ==")
FakeApp.mode = "boom"
r = cl.post(f"/customers/{CID}/invite", follow_redirects=True)
check("a server error is reported, not swallowed",
      "refused the request" in r.get_data(as_text=True))
real = A.app_invites.APP_URL
A.app_invites.APP_URL = "http://127.0.0.1:59998"      # nothing listening
r = cl.post(f"/customers/{CID}/invite", follow_redirects=True)
check("an unreachable app is reported, never left silent",
      "Could not reach the app" in r.get_data(as_text=True))
A.app_invites.APP_URL = real

print("\n== switched off unless configured ==")
saved_url, saved_tok = A.app_invites.APP_URL, A.app_invites.ADMIN_TOKEN
A.app_invites.APP_URL = None
check("with no app URL, enabled() is False", A.app_invites.enabled() is False)
r = cl.post(f"/customers/{CID}/invite", follow_redirects=True)
check("...and the button explains rather than failing",
      "not set up yet" in r.get_data(as_text=True))
A.app_invites.APP_URL, A.app_invites.ADMIN_TOKEN = saved_url, saved_tok

print("\n== nothing was written to the business database ==")
import sqlite3                                                     # noqa: E402
conn = sqlite3.connect(_tmp)
counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for t in ["customers", "subscriptions", "deliveries", "settings"]}
conn.close()
check("customers unchanged", counts["customers"] ==
      sqlite3.connect(LIVE_DB).execute("SELECT COUNT(*) FROM customers").fetchone()[0])
check("no invite table was created here",
      "invites" not in {r[0] for r in sqlite3.connect(_tmp).execute(
          "SELECT name FROM sqlite_master WHERE type='table'")})

print("\n== the Command Center still works ==")
for path in ["/today", "/customers", f"/customers/{CID}", "/revenue", "/subscriptions"]:
    check(f"GET {path}", cl.get(path).status_code == 200)

print(f"\n{N - len(FAILS)}/{N} checks passed")
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
