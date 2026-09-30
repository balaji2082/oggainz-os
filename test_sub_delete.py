# -*- coding: utf-8 -*-
"""Deleting a subscription has to work, and has to touch nothing else.

Nivitha's renewal went in twice on 29 Sep (S113 + S114, 30 seconds apart) and the
duplicate could not be deleted: every attempt was a server error. The delete
cleared the tables that existed in June and never learned about the two added in
August -- a pack's alternative boxes (subscription_variants) and its weekly day
plan (subscription_day_plan) -- so the database refused to drop a subscription
those rows still pointed at: "FOREIGN KEY constraint failed".

Worse was waiting behind it: after deleting, every later pack was renumbered to
close the gap, and the renumbering did not move those two tables either. Had the
delete ever succeeded, 29 packs would have shifted id and each would have been
handed its NEIGHBOUR's schedule and boxes. Deleting no longer renumbers.

Also here: the same pack submitted twice within minutes lands on the first one
instead of making a duplicate.

    python test_sub_delete.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_subdelete.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
os.environ["OG_DB_PATH"] = _tmp
os.environ.setdefault("OG_SECRET_KEY", "test-secret")
os.environ.pop("OG_PASSWORD", None)          # no login gate in the test client

sys.path.insert(0, APP_DIR)
import db      # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    raise SystemExit("refusing to run against the live database")

import core    # noqa: E402
import app as appmod   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def eq(name, got, want):
    check(name, got == want, "got %r, want %r" % (got, want))


client = appmod.app.test_client()
client.get("/settings")          # first request of the day runs the app's own daily jobs

conn = db.get_db()
NOW = db.now_iso()

# --- fixture: two customers, packs S001 S002 S004 S005 S006 (S003 already gone,
#     like S085 on the live server) --------------------------------------------
conn.execute("INSERT INTO customers(id,name) VALUES('C1','Nivitha'),('C2','Someone Else')")


def add_sub(sid, cid, name, status="Active", start="2026-09-30"):
    conn.execute(
        "INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,start_date,month,"
        "total_meals_scheduled,remaining_meals,status,schedule_mode,slot,created_at,"
        "subscription_price,delivery_fee,total_amount) "
        "VALUES(?,?,?,'Signature Meal Pack',?,'Sep-2026',5,5,?,'weekly','Lunch',?,1000,500,1500)",
        (sid, cid, name, start, status, NOW))


def dress(sid, cid, name):
    """Everything a real renewed pack carries, in every table that points at it."""
    v1 = conn.execute("INSERT INTO subscription_variants(subscription_id,name,plan_id,created_at) "
                      "VALUES(?,'Signature',1,?)", (sid, NOW)).lastrowid
    v2 = conn.execute("INSERT INTO subscription_variants(subscription_id,name,plan_id,created_at) "
                      "VALUES(?,'Veg Signature',1,?)", (sid, NOW)).lastrowid
    for wd in range(5):
        conn.execute("INSERT INTO subscription_day_plan(subscription_id,weekday,active,variant_id)"
                     " VALUES(?,?,1,?)", (sid, wd, v2 if wd == 0 else v1))
    conn.execute("INSERT INTO subscription_components(subscription_id,component_id,qty,kind,"
                 "variant_id) VALUES(?,1,100,'base',NULL)", (sid,))
    conn.execute("INSERT INTO subscription_components(subscription_id,component_id,qty,kind,"
                 "variant_id) VALUES(?,1,100,'base',?)", (sid, v2))
    conn.execute("INSERT INTO subscription_pauses(subscription_id,start_date,created_at) "
                 "VALUES(?,'2026-09-29',?)", (sid, NOW))
    conn.execute("INSERT INTO subscription_slot_moves(subscription_id,start_date,slot,created_at) "
                 "VALUES(?,'2026-09-30','Dinner',?)", (sid, NOW))
    conn.execute("INSERT INTO component_rules(subscription_id,action,created_at) "
                 "VALUES(?,'remove',?)", (sid, NOW))
    conn.execute("INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,status,"
                 "month,variant_id) VALUES('2026-09-30',?,?,?,'Lunch','Pending','Sep-2026',?)",
                 (sid, cid, name, v1))
    conn.execute("INSERT INTO invoices(id,token,customer_id,subscription_id,created_at) "
                 "VALUES(?,?,?,?,?)", ("INV-" + sid, "tok" + sid, cid, sid, NOW))
    conn.execute("INSERT INTO leads(id,name,customer_id,subscription_id,created_at) "
                 "VALUES(?,?,?,?,?)", ("L-" + sid, name, cid, sid, NOW))
    conn.execute("INSERT INTO skip_requests(customer_id,subscription_id,kind,requested_at,"
                 "cutoff_at,outcome) VALUES(?,?,'skip',?,?,'auto_applied')", (cid, sid, NOW, NOW))


for sid, cid, name in [("S001", "C2", "Someone Else"), ("S002", "C1", "Nivitha"),
                       ("S004", "C2", "Someone Else"), ("S005", "C1", "Nivitha"),
                       ("S006", "C1", "Nivitha")]:
    add_sub(sid, cid, name)
    dress(sid, cid, name)
conn.commit()

OWNED, HISTORY = core.SUB_OWNED_TABLES, core.SUB_HISTORY_TABLES


def rows_for(sid):
    """{table: number of rows pointing at sid} across every table that can."""
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE subscription_id=?", (sid,))
            .fetchone()[0] for t in OWNED + HISTORY}


def fingerprint(sid):
    """Everything about a pack that must survive another pack being deleted."""
    out = {"sub": tuple(conn.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone())}
    for t in OWNED + HISTORY:
        out[t] = sorted(tuple(r) for r in conn.execute(
            f"SELECT * FROM {t} WHERE subscription_id=?", (sid,)))
    return out


print("\nsubscription delete")
print("-" * 58)

print("\n every table that points at a subscription is known to the delete")
have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
pointing = sorted(t for t in have
                  if "subscription_id" in {c[1] for c in conn.execute(f"PRAGMA table_info({t})")})
unknown = [t for t in pointing if t not in OWNED + HISTORY]
check("no table with a subscription_id is left out", not unknown, str(unknown))
check("variants and day plan are among the owned tables",
      "subscription_variants" in OWNED and "subscription_day_plan" in OWNED)

print("\n the live failure, reproduced: a pack with boxes and a day plan")
before_other = {s: fingerprint(s) for s in ("S001", "S002", "S004", "S005")}
eq("S006 carries variants", rows_for("S006")["subscription_variants"], 2)
eq("S006 carries a day plan", rows_for("S006")["subscription_day_plan"], 5)
r = client.post("/subscriptions/S006/delete")
eq("delete answers with a redirect, not a server error", r.status_code, 302)
check("…back to the subscriptions list", r.headers["Location"].endswith("/subscriptions"),
      r.headers.get("Location"))
check("S006 is gone",
      conn.execute("SELECT 1 FROM subscriptions WHERE id='S006'").fetchone() is None)
left = rows_for("S006")
check("nothing owned by S006 is left behind",
      all(left[t] == 0 for t in OWNED), str({t: left[t] for t in OWNED if left[t]}))
check("its invoice, lead and skip request are kept",
      conn.execute("SELECT COUNT(*) FROM invoices WHERE id='INV-S006'").fetchone()[0] == 1
      and conn.execute("SELECT COUNT(*) FROM leads WHERE id='L-S006'").fetchone()[0] == 1
      and conn.execute("SELECT COUNT(*) FROM skip_requests WHERE customer_id='C1'")
      .fetchone()[0] == 3)
check("…and only unlinked", all(left[t] == 0 for t in HISTORY))
eq("the database has no broken links afterwards",
   conn.execute("PRAGMA foreign_key_check").fetchall(), [])

print("\n no other pack changes — not its id, not a single row")
eq("ids are exactly what they were",
   [r[0] for r in conn.execute("SELECT id FROM subscriptions ORDER BY id")],
   ["S001", "S002", "S004", "S005"])
for s, fp in before_other.items():
    check(f"{s} untouched", fingerprint(s) == fp)

print("\n deleting from the MIDDLE keeps every later pack's own schedule")
before_s005 = fingerprint("S005")
eq("delete S002", client.post("/subscriptions/S002/delete").status_code, 302)
check("S005 is still S005 with its own rows", fingerprint("S005") == before_s005)
vids = {r[0] for r in conn.execute(
    "SELECT id FROM subscription_variants WHERE subscription_id='S005'")}
plan_vids = {r[0] for r in conn.execute(
    "SELECT variant_id FROM subscription_day_plan WHERE subscription_id='S005'")}
check("S005's day plan still points at S005's own boxes", plan_vids <= vids,
      f"{plan_vids} vs {vids}")

print("\n a failure half-way removes nothing, and says so")
real = core._tables_present
core._tables_present = lambda c, names: list(names) + ["no_such_table"]
try:
    before_s004 = fingerprint("S004")
    try:
        core.delete_subscription(conn, "S004")
        raised = False
    except sqlite3.Error:
        raised = True
    check("the error is raised", raised)
    check("…and S004 is exactly as it was (rolled back)", fingerprint("S004") == before_s004)
    r = client.post("/subscriptions/S004/delete", follow_redirects=False)
    eq("the page stays up (redirect, not a server error)", r.status_code, 302)
    check("…back to the pack itself", "/subscriptions/S004" in r.headers["Location"],
          r.headers.get("Location"))
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    check("…with a message saying nothing was changed", "nothing was changed" in page)
finally:
    core._tables_present = real
check("S004 still exists", conn.execute("SELECT 1 FROM subscriptions WHERE id='S004'")
      .fetchone() is not None)


print("\nthe same pack sent twice")
print("-" * 58)
form = {"customer_id": "C1", "plan_name": "Elite Meal Pack", "duration": "Weekly",
        "total_meals_scheduled": "5", "slot": "Lunch", "start_date": "2026-10-01",
        "booking_date": "2026-10-01", "subscription_price": "1000", "delivery_fee": "500",
        "amount_received": "0", "comp_json": "[]", "schedule_mode": "daily"}


def count_c1():
    return conn.execute("SELECT COUNT(*) FROM subscriptions WHERE customer_id='C1'").fetchone()[0]


n0 = count_c1()
r1 = client.post("/subscriptions/new", data=form)
first = r1.headers["Location"].rstrip("/").split("/")[-1]
eq("the first one is created", count_c1(), n0 + 1)
check("…and gets the next number after the highest", first == "S006", first)
r2 = client.post("/subscriptions/new", data=form)
eq("a second identical submit creates nothing", count_c1(), n0 + 1)
check("…it opens the pack already made", r2.headers["Location"].endswith("/" + first),
      r2.headers.get("Location"))

diff = dict(form, start_date="2026-10-08")
client.post("/subscriptions/new", data=diff)
eq("a different start date is a different pack", count_c1(), n0 + 2)

slot = dict(form, slot="Dinner")
client.post("/subscriptions/new", data=slot)
eq("a different slot is a different pack", count_c1(), n0 + 3)

old = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
conn.execute("UPDATE subscriptions SET created_at=? WHERE id=?", (old, first))
conn.commit()
client.post("/subscriptions/new", data=form)
eq("the same pack 20 minutes later is allowed (a real second pack)", count_c1(), n0 + 4)

print("\n the old one-tap renew route is guarded the same way")
n1 = count_c1()
client.post("/subscriptions/S005/renew", data={"start_date": "2026-11-02"})
client.post("/subscriptions/S005/renew", data={"start_date": "2026-11-02"})
eq("two taps, one renewal", count_c1(), n1 + 1)

conn.close()
print("\n" + ("ALL GOOD" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
