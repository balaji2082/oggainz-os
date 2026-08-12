"""
Tests for validity.py — pack expiry, the global switch, and per-pack overrides.

    python test_validity.py

Copies oggainz.db to a throwaway file first. Refuses to run if that fails.

The checks that matter most: that the global switch defaults off and changes
nothing until it's flipped, that flipping it never overrides a pack Balaji has
explicitly decided about, and that a closed kitchen day genuinely pushes the
expiry date out rather than being decorative like `validity_days` always was.
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_validitytest.db")
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

import core                                                       # noqa: E402
import validity                                                   # noqa: E402
import app as A                                                   # noqa: E402

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
TOK = {"X-OG-Service-Token": "test-service-token"}

c = db.get_db()
row = c.execute(
    "SELECT * FROM subscriptions WHERE customer_id IS NOT NULL "
    "AND status IN ('Active','Ending Soon') AND remaining_meals > 0 AND start_date IS NOT NULL "
    "ORDER BY remaining_meals DESC LIMIT 1").fetchone()
if not row:
    c.close()
    sys.exit("no usable subscription in this database copy")
SID, PLAN = row["id"], row["plan_name"]
ORIG_SCHEDULED = row["total_meals_scheduled"]
ORIG_GLOBAL = db.get_setting(c, "validity_enforced_globally")
c.close()
print(f"testing against subscription {SID} ({PLAN})\n")


def reset_sub():
    c = db.get_db()
    c.execute("UPDATE subscriptions SET enforce_validity=0, expires_on=NULL, "
              "validity_override=NULL, validity_note=NULL, total_meals_scheduled=? "
              "WHERE id=?", (ORIG_SCHEDULED, SID))
    c.commit()
    core.recompute_subscription(c, SID)
    c.close()


reset_sub()


# ===========================================================================
print("== 1. off by default, and changing nothing until it's touched ==")
c = db.get_db()
check("the global switch is off by default", not validity.globally_enabled(c))
sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("an untouched pack does not enforce validity", not validity.effective_enabled(c, sub))
check("...and enforce_validity itself is 0", sub["enforce_validity"] == 0)
c.close()


print("\n== 2. kitchen-closed days genuinely extend the window ==")
c = db.get_db()
start = "2026-08-03"   # a Monday
naive_40 = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=40)).strftime("%Y-%m-%d")
computed = validity.compute_expiry(c, start, 40)
check("40 USABLE days lands later than 40 calendar days (weekends push it out)",
      computed > naive_40, (computed, naive_40))

# Count operating days by hand between start and the computed date, inclusive
# of the end, and check it's exactly 40 — proving the walk is exact, not
# approximate.
usable = sum(
    1 for i in range(1, 400)
    if core.is_operating_day(
        c, (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d"))
    and (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d") <= computed
)
check("...and it is exactly 40 operating days, not approximately",
      usable == 40, usable)
c.close()


print("\n== 3. turning validity on for one pack computes an expiry from its plan ==")
r = cl.post(f"/api/v1/admin/subscription/{SID}/validity", json={"override": "on"}, headers=TOK)
j = r.get_json()
check("the endpoint returns 200", r.status_code == 200, r.status_code)
check("it is now enforced", j.get("enforced") is True, j)
check("an expiry date was computed", j.get("expires_on"), j)

c = db.get_db()
sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("enforce_validity was written", sub["enforce_validity"] == 1)
check("validity_override is 'on'", sub["validity_override"] == "on")
check("who/when was recorded", sub["validity_changed_at"] and sub["validity_changed_by"])
c.close()


print("\n== 4. the global switch never overrides an explicit per-pack decision ==")
r = post = cl.post("/api/v1/admin/validity/settings", json={"enabled": True}, headers=TOK)
check("turning the global switch on succeeds", r.status_code == 200, r.status_code)
c = db.get_db()
check("the global switch reads back on", validity.globally_enabled(c))
sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("the explicitly-on pack is still on (unsurprising)", sub["enforce_validity"] == 1)
c.close()

# Now explicitly turn OFF for this one pack, with the global switch still ON.
r = cl.post(f"/api/v1/admin/subscription/{SID}/validity", json={"override": "off"}, headers=TOK)
check("explicit off is accepted", r.status_code == 200, r.status_code)
c = db.get_db()
sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("[LAW, docs/01 §7.2.0] an explicit off wins over the global on",
      sub["enforce_validity"] == 0, sub["enforce_validity"])
c.close()

# Turning the global switch off again must not touch this pack's explicit
# decision either — only un-decided packs move.
cl.post("/api/v1/admin/validity/settings", json={"enabled": False}, headers=TOK)
c = db.get_db()
sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("the explicit override survives the global switch flipping back off",
      sub["validity_override"] == "off" and sub["enforce_validity"] == 0)
c.close()

reset_sub()

print("\n== 5. flipping the global switch on sweeps only UN-decided packs ==")
c = db.get_db()
# Give a second pack (if one exists) an explicit decision first.
other = c.execute(
    "SELECT id FROM subscriptions WHERE id != ? AND status IN ('Active','Ending Soon') LIMIT 1",
    (SID,)).fetchone()
c.close()
if other:
    cl.post(f"/api/v1/admin/subscription/{other['id']}/validity",
            json={"override": "off"}, headers=TOK)
    r = cl.post("/api/v1/admin/validity/settings", json={"enabled": True}, headers=TOK)
    j = r.get_json()
    c = db.get_db()
    ours = c.execute("SELECT enforce_validity FROM subscriptions WHERE id=?", (SID,)).fetchone()
    theirs = c.execute("SELECT enforce_validity, validity_override FROM subscriptions WHERE id=?",
                       (other["id"],)).fetchone()
    check("an un-decided pack picks up the global on", ours["enforce_validity"] == 1)
    check("a pack explicitly set off stays off despite the sweep",
          theirs["enforce_validity"] == 0 and theirs["validity_override"] == "off")
    c.close()
    cl.post("/api/v1/admin/validity/settings", json={"enabled": False}, headers=TOK)
    cl.post(f"/api/v1/admin/subscription/{other['id']}/validity",
            json={"override": None}, headers=TOK)
    reset_sub()
else:
    print("  .... skipped: only one live subscription in this copy")


print("\n== 6. an expired pack stops generating orders and gets its own status ==")
c = db.get_db()
past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
c.execute("UPDATE subscriptions SET enforce_validity=1, expires_on=?, remaining_meals=5 "
          "WHERE id=?", (past, SID))
c.commit()
core.recompute_subscription(c, SID)
sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("[docs/01 §7.2.1] status becomes Expired", sub["status"] == "Expired", sub["status"])

before = c.execute("SELECT COUNT(*) n FROM deliveries WHERE subscription_id=? AND date=?",
                   (SID, datetime.now().strftime("%Y-%m-%d"))).fetchone()["n"]
created = core.ensure_day_rows(c, datetime.now().strftime("%Y-%m-%d"))
after = c.execute("SELECT COUNT(*) n FROM deliveries WHERE subscription_id=? AND date=?",
                  (SID, datetime.now().strftime("%Y-%m-%d"))).fetchone()["n"]
check("no new order was generated for an expired pack", after == before, (before, after, created))
c.close()

print("\n== 7. Completed always wins over Expired — using everything is not a problem ==")
# recompute_subscription derives delivered/remaining FROM the deliveries table,
# so reaching a genuine Completed state means shrinking total_meals_scheduled
# to match what has really been delivered — not fabricating the counters.
c = db.get_db()
real_delivered = c.execute(
    "SELECT COUNT(*) n FROM deliveries WHERE subscription_id=? AND status='Delivered'",
    (SID,)).fetchone()["n"]
c.execute("UPDATE subscriptions SET enforce_validity=1, expires_on=?, "
          "total_meals_scheduled=? WHERE id=?", (past, max(real_delivered, 1), SID))
c.commit()
core.recompute_subscription(c, SID)
sub = c.execute("SELECT status, delivered_count, total_meals_scheduled FROM subscriptions "
                "WHERE id=?", (SID,)).fetchone()
check("a fully-used pack shows Completed, never Expired", sub["status"] == "Completed", dict(sub))
c.close()

reset_sub()

print("\n== 8. a direct date edit needs no reason, and is never silently recomputed ==")
c = db.get_db()
future = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
c.close()
r = cl.post(f"/api/v1/admin/subscription/{SID}/validity",
            json={"override": "on", "expires_on": future, "note": "agreed 90 days by hand"},
            headers=TOK)
j = r.get_json()
check("a manual date is accepted with no reason required", r.status_code == 200, r.status_code)
check("it is used as-is, not recomputed from the plan", j.get("expires_on") == future, j)
c = db.get_db()
sub = c.execute("SELECT validity_note FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("the note is kept", sub["validity_note"] == "agreed 90 days by hand")
c.close()

# Re-syncing (e.g. via another global toggle sweep) must not silently overwrite
# a date Balaji already set by hand.
c = db.get_db()
validity.sync(c, SID)
sub = c.execute("SELECT expires_on FROM subscriptions WHERE id=?", (SID,)).fetchone()
check("re-syncing leaves a manually-set date untouched", sub["expires_on"] == future)
c.close()

reset_sub()

print("\n== 9. bad input is refused ==")
check("an invalid override value is refused",
      cl.post(f"/api/v1/admin/subscription/{SID}/validity", json={"override": "maybe"},
              headers=TOK).status_code == 400)
check("a malformed date is refused",
      cl.post(f"/api/v1/admin/subscription/{SID}/validity",
              json={"override": "on", "expires_on": "not-a-date"}, headers=TOK).status_code == 400)
check("a non-existent subscription is refused",
      cl.post("/api/v1/admin/subscription/S999999/validity", json={"override": "on"},
              headers=TOK).status_code == 404)

print("\n== 10. everything here needs the token ==")
for path, method in [
    ("/api/v1/admin/validity/settings", "post"),
    ("/api/v1/admin/validity", "get"),
    (f"/api/v1/admin/subscription/{SID}/validity", "post"),
]:
    r = getattr(cl, method)(path, json={} if method == "post" else None)
    check(f"{path} without a token", r.status_code == 401, r.status_code)

print("\n== 11. the admin summary lists it, with days left ==")
c = db.get_db()
future2 = (datetime.now() + timedelta(days=12)).strftime("%Y-%m-%d")
c.execute("UPDATE subscriptions SET enforce_validity=1, expires_on=? WHERE id=?", (future2, SID))
c.commit()
c.close()
j = cl.get("/api/v1/admin/validity", headers=TOK).get_json()
mine = [r for r in j.get("subscriptions", []) if r["subscription_id"] == SID]
check("the pack appears in the summary", len(mine) == 1, j)
if mine:
    check("days_left is close to 12", 10 <= mine[0]["days_left"] <= 12, mine[0])
    check("it is not flagged expired", mine[0]["expired"] is False, mine[0])


# --- put everything back so the copy is reusable -----------------------------
reset_sub()
c = db.get_db()
db.set_setting(c, "validity_enforced_globally", ORIG_GLOBAL or "0")
c.close()

print(f"\n{N - len(FAILS)}/{N} passed")
if FAILS:
    print("FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("all good")
