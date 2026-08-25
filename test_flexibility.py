"""
Tests for per-pack skip/pause control (flexibility.py).

    python test_flexibility.py

Runs against a COPY of oggainz.db and refuses to run otherwise. Mirrors
test_validity.py, because the feature deliberately mirrors validity.

The cases that matter are the ones that made Balaji ask for this:
  * every pack can skip and pause by default now, not just Elite and Athlete
  * turning ONE pack off leaves every other pack alone
  * the master switch does not overrule a pack he has decided about by hand
"""

import os
import shutil
import sys
import tempfile

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")
_tmp = os.path.join(tempfile.gettempdir(), "oggainz_flextest.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
shutil.copyfile(LIVE_DB, _tmp)
os.environ["OG_DB_PATH"] = _tmp
os.environ["OG_API_TOKEN"] = "test-service-token"
os.environ.setdefault("OG_PASSWORD", "test-owner-pw")
os.environ.setdefault("OG_SECRET_KEY", "test-secret")

sys.path.insert(0, APP_DIR)
import db                                                        # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    sys.exit("REFUSING TO RUN: the app is pointed at the live database.")

import flexibility                                               # noqa: E402
import app as A                                                  # noqa: E402

FAILS, N = [], 0
TOK = {"X-OG-Service-Token": "test-service-token"}
A.app.config["TESTING"] = True
cl = A.app.test_client()


def check(label, cond, extra=""):
    global N
    N += 1
    if cond:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + " " + str(extra))
        FAILS.append(label)


c = db.get_db()
db.migrate(c)
subs = c.execute(
    "SELECT * FROM subscriptions WHERE status IN ('Active','Ending Soon','Paused') "
    "ORDER BY id LIMIT 2").fetchall()
if len(subs) < 2:
    sys.exit("need two live packs in the copy")
A_SUB, B_SUB = subs[0], subs[1]
A_ID, A_CID = A_SUB["id"], A_SUB["customer_id"]
B_ID = B_SUB["id"]
c.close()
print("testing with packs " + str(A_ID) + " and " + str(B_ID) + "\n")


print("== 1. every plan may skip and pause by default ==")
c = db.get_db()
db.set_setting(c, "flex_enabled_globally", "1")
c.execute("UPDATE subscriptions SET flex_override=NULL")
c.commit()
# The whole point of Balaji's request: this used to be Elite/Athlete only.
blocked = [s["plan_name"] for s in c.execute(
    "SELECT * FROM subscriptions WHERE status IN ('Active','Ending Soon','Paused')"
).fetchall() if not flexibility.effective_enabled(c, s)]
check("EVERY live plan may skip/pause by default (was Elite/Athlete only)",
      not blocked, blocked)
c.close()


print("\n== 2. one pack off leaves the others alone ==")
c = db.get_db()
flexibility.set_override(c, A_ID, "off", changed_by="test", note="testing")
a = c.execute("SELECT * FROM subscriptions WHERE id=?", (A_ID,)).fetchone()
b = c.execute("SELECT * FROM subscriptions WHERE id=?", (B_ID,)).fetchone()
check("the pack turned off is off", flexibility.effective_enabled(c, a) is False)
check("the other pack is untouched", flexibility.effective_enabled(c, b) is True)
check("the note is stored", (a["flex_note"] or "") == "testing", a["flex_note"])
check("who changed it is recorded", a["flex_changed_by"] == "test", a["flex_changed_by"])
c.close()


print("\n== 3. the master switch does not overrule an explicit decision ==")
c = db.get_db()
flexibility.set_override(c, B_ID, "on", changed_by="test")
flexibility.set_globally_enabled(c, False, changed_by="test")
a = c.execute("SELECT * FROM subscriptions WHERE id=?", (A_ID,)).fetchone()
b = c.execute("SELECT * FROM subscriptions WHERE id=?", (B_ID,)).fetchone()
check("switch off: an explicitly-ON pack stays on", flexibility.effective_enabled(c, b) is True)
check("switch off: an explicitly-OFF pack stays off", flexibility.effective_enabled(c, a) is False)
undecided = c.execute(
    "SELECT * FROM subscriptions WHERE flex_override IS NULL AND status IN "
    "('Active','Ending Soon','Paused') LIMIT 1").fetchone()
if undecided:
    check("switch off: an undecided pack follows the switch",
          flexibility.effective_enabled(c, undecided) is False)
flexibility.set_globally_enabled(c, True, changed_by="test")
a = c.execute("SELECT * FROM subscriptions WHERE id=?", (A_ID,)).fetchone()
check("switch back on: the explicitly-OFF pack is STILL off",
      flexibility.effective_enabled(c, a) is False)
c.close()


print("\n== 4. clearing the override returns the pack to the switch ==")
c = db.get_db()
flexibility.set_override(c, A_ID, None, changed_by="test")
a = c.execute("SELECT * FROM subscriptions WHERE id=?", (A_ID,)).fetchone()
check("override cleared", a["flex_override"] is None, a["flex_override"])
check("and it follows the switch again", flexibility.effective_enabled(c, a) is True)
c.close()


print("\n== 5. the write API refuses a pack that is switched off ==")
c = db.get_db()
flexibility.set_override(c, A_ID, "off", changed_by="test")
c.close()
r = cl.post("/api/v1/customer/" + str(A_CID) + "/skip",
            json={"date": "2099-01-01", "subscription_id": A_ID}, headers=TOK)
check("skip on a switched-off pack is refused", r.status_code >= 400, r.status_code)
r = cl.post("/api/v1/customer/" + str(A_CID) + "/pause",
            json={"start": "2099-01-01", "end": "2099-01-02",
                  "subscription_id": A_ID}, headers=TOK)
check("pause on a switched-off pack is refused", r.status_code >= 400, r.status_code)
body = r.get_json() or {}
check("...and says so in words a person can read",
      "switched off" in (body.get("error") or ""), body)


print("\n== 6. the pack payload tells the app, so the app never decides ==")
r = cl.get("/api/v1/customer/" + str(A_CID) + "/packs", headers=TOK)
packs = (r.get_json() or {}).get("packs", [])
mine = [p for p in packs if str(p["id"]) == str(A_ID)]
check("the pack is in the payload", len(mine) == 1, [p["id"] for p in packs])
if mine:
    check("can_skip is false for the switched-off pack", mine[0].get("can_skip") is False, mine[0].get("can_skip"))
    check("can_pause is false for the switched-off pack", mine[0].get("can_pause") is False, mine[0].get("can_pause"))


print("\n== 7. the admin endpoints ==")
r = cl.get("/api/v1/admin/flex", headers=TOK)
rows = (r.get_json() or {}).get("subscriptions", [])
check("GET /admin/flex lists live packs", r.status_code == 200 and len(rows) > 0, r.status_code)
check("each row says whether it is enabled",
      all("enabled" in x and "override" in x for x in rows), rows[:1])
r = cl.post("/api/v1/admin/subscription/" + str(A_ID) + "/flex",
            json={"override": None, "by": "test"}, headers=TOK)
check("POST clears an override", r.status_code == 200 and
      (r.get_json() or {}).get("enabled") is True, r.get_json())
r = cl.post("/api/v1/admin/subscription/" + str(A_ID) + "/flex",
            json={"by": "test"}, headers=TOK)
check("POST with no `override` key is rejected, not treated as clear",
      r.status_code == 400, r.status_code)
r = cl.post("/api/v1/admin/subscription/" + str(A_ID) + "/flex",
            json={"override": "maybe"}, headers=TOK)
check("a nonsense override is rejected", r.status_code == 400, r.status_code)
r = cl.get("/api/v1/admin/flex/settings", headers=TOK)
check("GET /admin/flex/settings reports the switch",
      r.status_code == 200 and "enabled" in (r.get_json() or {}), r.get_json())


# --- leave the copy tidy ----------------------------------------------------
c = db.get_db()
c.execute("UPDATE subscriptions SET flex_override=NULL, flex_note=NULL")
db.set_setting(c, "flex_enabled_globally", "1")
c.commit()
c.close()

print("\n" + str(N - len(FAILS)) + "/" + str(N) + " passed")
if FAILS:
    print("FAILED:")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("all good")
