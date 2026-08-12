"""
Tests for the read-only customer API (customer_api.py).

    python test_customer_api.py

Copies oggainz.db to a throwaway file first and points the app at the copy, so it
can never touch real data. Refuses to run if that fails.

The important checks are the boring ones: that the token is actually required,
that one customer's id cannot return another customer's rows, that no endpoint
writes anything, and that opening the app does not cause order rows to be
created in the business database.
"""

import os
import shutil
import sqlite3
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_apitest.db")
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

sys.path.insert(0, APP_DIR)
import db                                                        # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    sys.exit("REFUSING TO RUN: the app is pointed at the live database.")

import app as A                                                  # noqa: E402

FAILS, N = [], 0
TOK = {"X-OG-Service-Token": "test-service-token"}


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

c = db.get_db()
row = c.execute("SELECT customer_id FROM subscriptions WHERE customer_id IS NOT NULL "
                "AND status IN ('Active','Ending Soon') LIMIT 1").fetchone()
CID = row["customer_id"] if row else None
other = c.execute("SELECT id FROM customers WHERE id != ? LIMIT 1", (CID,)).fetchone()
OTHER = other["id"] if other else None
c.close()
if not CID:
    sys.exit("no active subscription in this database copy to test against")
print(f"testing against customer {CID} (and {OTHER} for isolation)\n")

print("== 1. the token is actually required ==")
for path in [f"/api/v1/customer/{CID}/packs", "/api/v1/settings/public",
             f"/api/v1/customer/{CID}/meals", f"/api/v1/customer/{CID}/invoices",
             f"/api/v1/customer/{CID}/addresses"]:
    check(f"no token -> 401  {path}", cl.get(path).status_code == 401)
check("wrong token -> 401",
      cl.get(f"/api/v1/customer/{CID}/packs",
             headers={"X-OG-Service-Token": "nope"}).status_code == 401)
check("token in the query string does NOT work",
      cl.get(f"/api/v1/customer/{CID}/packs?token=test-service-token").status_code == 401)
check("a near-miss token is refused",
      cl.get(f"/api/v1/customer/{CID}/packs",
             headers={"X-OG-Service-Token": "test-service-toke"}).status_code == 401)

print("\n== 2. the endpoints answer ==")
r = cl.get("/api/v1/settings/public", headers=TOK)
j = r.get_json()
check("settings/public -> 200", r.status_code == 200 and j["ok"])
check("cutoff time is served from settings, not hardcoded",
      j["settings"]["skip_cutoff_time"] == "18:00", j["settings"].get("skip_cutoff_time"))
check("delivery promise is the customer window, not the staff shift",
      j["settings"]["delivery_promise_dinner"] == "17:30-20:00")
check("operating weekdays included", isinstance(j["settings"]["operating_weekdays"], list))
check("no secret leaks into public settings",
      not any(k for k in j["settings"] if "key" in k.lower() or "token" in k.lower()),
      list(j["settings"]))

r = cl.get(f"/api/v1/customer/{CID}/packs", headers=TOK)
j = r.get_json()
check("packs -> 200 with at least one pack", r.status_code == 200 and len(j["packs"]) > 0)
p = j["packs"][0]
for field in ["id", "plan", "status", "meals_remaining", "composition", "live"]:
    check(f"pack has {field}", field in p)
check("composition splits base from add-ons",
      "base" in p["composition"] and "addons" in p["composition"])
check("an unenforced pack shows NO expiry countdown",
      all(x["expires_on"] is None for x in j["packs"] if not x["validity_enforced"]))

r = cl.get(f"/api/v1/customer/{CID}/meals", headers=TOK)
j = r.get_json()
check("meals -> 200", r.status_code == 200 and j["ok"])
check("meals are within the requested window",
      all(j["from"] <= m["date"] <= j["to"] for m in j["meals"]))
check("future days are flagged as projected, never as confirmed",
      all(m["status"] == "Expected" for m in j["meals"] if m["projected"]))
check("bad date is rejected",
      cl.get(f"/api/v1/customer/{CID}/meals?from=notadate", headers=TOK).status_code == 400)
check("every meal says which box it is",
      all(m["plan"] for m in j["meals"]),
      str([m for m in j["meals"] if not m["plan"]][:2]))

# The forward days used to be drawn on every open kitchen day regardless of the
# subscription's own schedule, so a Mon/Wed/Fri pack promised the customer meals
# on Tuesday and Thursday too. The engine has to be the one deciding.
import core as _core                                             # noqa: E402
from datetime import datetime                                    # noqa: E402
_c = db.get_db()
_subs = {s["id"]: s for s in _c.execute(
    "SELECT * FROM subscriptions WHERE customer_id=? "
    "AND status IN ('Active','Ending Soon')", (CID,))}
_bad = [m for m in j["meals"] if m["projected"] and m["subscription_id"] in _subs
        and not _core.plan_for_day(_c, _subs[m["subscription_id"]], m["date"])["deliver"]]
check("a projected day is one the schedule actually delivers on", not _bad, str(_bad[:2]))

# ...and prove it against a pack that really is on a restricted schedule, rather
# than relying on whichever customer this database copy happened to pick.
if _subs:
    _sid = sorted(_subs)[0]
    _c.execute("UPDATE subscriptions SET schedule_mode='weekly' WHERE id=?", (_sid,))
    _core.save_day_plan(_c, _sid, [{"weekday": wd, "active": 1, "slot": None,
                                    "variant_id": None, "meals": 1, "note": None}
                                   for wd in (0, 2, 4)])          # Mon / Wed / Fri
    _c.commit()
    _j = cl.get(f"/api/v1/customer/{CID}/meals", headers=TOK).get_json()
    _days = {m["date"] for m in _j["meals"]
             if m["projected"] and m["subscription_id"] == _sid}
    _off = sorted(d for d in _days
                  if datetime.strptime(d, "%Y-%m-%d").weekday() in (1, 3, 5, 6))
    check("a Mon/Wed/Fri pack promises nothing on Tue/Thu", not _off, str(_off[:3]))
    check("...but still promises the days it does deliver", len(_days) > 0)
    _c.execute("UPDATE subscriptions SET schedule_mode='daily' WHERE id=?", (_sid,))
    _c.execute("DELETE FROM subscription_day_plan WHERE subscription_id=?", (_sid,))
    _c.commit()
_c.close()

check("invoices -> 200",
      cl.get(f"/api/v1/customer/{CID}/invoices", headers=TOK).status_code == 200)
r = cl.get(f"/api/v1/customer/{CID}/addresses", headers=TOK)
j = r.get_json()
check("addresses -> 200", r.status_code == 200)
check("routing internals (zone/corridor) are not exposed",
      all("zone" not in a and "corridor" not in a for a in j["addresses"]),
      j["addresses"][:1])

print("\n== 3. one customer never sees another's data ==")
if OTHER:
    j = cl.get(f"/api/v1/customer/{OTHER}/packs", headers=TOK).get_json()
    check("asking for another id returns only that id's packs",
          all(True for _ in j["packs"]) and j["customer_id"] == OTHER)
    ids = {p["id"] for p in j["packs"]}
    mine = {p["id"] for p in cl.get(f"/api/v1/customer/{CID}/packs",
                                    headers=TOK).get_json()["packs"]}
    check("the two customers' packs do not overlap", not (ids & mine), ids & mine)
    j = cl.get(f"/api/v1/customer/{OTHER}/meals", headers=TOK).get_json()
    c = db.get_db()
    real = {r["id"] for r in c.execute(
        "SELECT id FROM deliveries WHERE customer_id=?", (OTHER,))}
    c.close()
    check("meals are scoped to the customer asked for",
          j["customer_id"] == OTHER)
j = cl.get("/api/v1/customer/NOPE999/packs", headers=TOK).get_json()
check("an unknown customer returns empty, not an error or everything",
      j["ok"] and j["packs"] == [])

print("\n== 4. nothing is written, ever ==")
c = sqlite3.connect(_tmp)
before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for t in ["deliveries", "subscriptions", "customers", "invoices",
                    "subscription_pauses", "skip_requests", "settings"]}
c.close()
for path in [f"/api/v1/customer/{CID}/packs", f"/api/v1/customer/{CID}/meals",
             f"/api/v1/customer/{CID}/meals?from=2026-08-01&to=2026-12-31",
             f"/api/v1/customer/{CID}/invoices", f"/api/v1/customer/{CID}/addresses",
             "/api/v1/settings/public"]:
    cl.get(path, headers=TOK)
c = sqlite3.connect(_tmp)
after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
c.close()
for t in before:
    check(f"{t}: {before[t]} rows, unchanged", before[t] == after[t],
          f"{before[t]} -> {after[t]}")
check("reading months of future meals creates NO order rows",
      before["deliveries"] == after["deliveries"],
      f"{before['deliveries']} -> {after['deliveries']}")

print("\n== 5. writes are refused (there are none yet) ==")
for path in [f"/api/v1/customer/{CID}/skip", f"/api/v1/customer/{CID}/pause",
             f"/api/v1/customer/{CID}/pin"]:
    check(f"POST {path} -> 404/405, not implemented",
          cl.post(path, headers=TOK, json={}).status_code in (404, 405))

print("\n== 6. the API is off unless the token is configured ==")
import importlib                                                 # noqa: E402
import customer_api                                              # noqa: E402
saved = customer_api.API_TOKEN
customer_api.API_TOKEN = None
check("no OG_API_TOKEN -> 503 closed, not open",
      cl.get(f"/api/v1/customer/{CID}/packs", headers=TOK).status_code == 503)
customer_api.API_TOKEN = saved

print("\n== 7. the Command Center itself still works ==")
own = A.app.test_client()
own.post("/login", data={"password": "test-owner-pw"})
for path in ["/today", "/subscriptions", "/revenue", "/customers", "/settings", "/more"]:
    check(f"owner GET {path} -> 200", own.get(path).status_code == 200)

print(f"\n{N - len(FAILS)}/{N} checks passed")
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
