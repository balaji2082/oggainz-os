# -*- coding: utf-8 -*-
"""One stop can carry more than one box.

Balaji's real case: a client in Arumbakkam, 25 km out, took TWO Elite meals in one
slot for ₹600 (2 × ₹299 = ₹598, he rounded). One ride, one Porter fee of ₹175,
one address — but two boxes to cook and two meals of revenue.

Before this, `deliveries` had no quantity at all. Every count was COUNT(*), so two
boxes read as one meal: the prep list cooked one, the pack was drawn down by one,
and the money dashboard divided the month's costs by a meal count that was short.

    python test_order_qty.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import date

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_qtytest.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
os.environ["OG_DB_PATH"] = _tmp
os.environ.setdefault("OG_SECRET_KEY", "test-secret")

sys.path.insert(0, APP_DIR)
import db      # noqa: E402
import core    # noqa: E402
import menu    # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    raise SystemExit("refusing to run against the live database")

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def eq(name, got, want):
    check(name, got == want, "got %r, want %r" % (got, want))


TODAY = date(2026, 9, 2)
core._today_iso = lambda: TODAY.strftime("%Y-%m-%d")   # noqa: E305

conn = db.init_db()
conn.row_factory = sqlite3.Row

print("\ntwo boxes, one stop")
print("-" * 58)

print("\n a fresh database really gets every migrated column")
# Two keys of the same name in a dict literal is not an error in Python -- the
# last one silently wins. MIGRATIONS had "trial_orders" twice, so lat/lng were
# cancelling notes/delivery_date/meal_type/delivery_status/plan_id, and none of
# those five had ever reached a database that did not already happen to have
# them. It never bit only because the live database predates the split. This is
# the check that catches the next one.
missing = []
for table, cols in db.MIGRATIONS.items():
    have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    for name, _decl in cols:
        if name not in have:
            missing.append("%s.%s" % (table, name))
check("no migrated column is missing after init_db", not missing, str(missing))

print("\n the column exists and defaults to one")
cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(deliveries)")}
check("deliveries has qty", "qty" in cols)
tcols = {r["name"] for r in conn.execute("PRAGMA table_info(trial_orders)")}
check("trial_orders has qty", "qty" in tcols)

conn.execute(
    "INSERT INTO deliveries(date,name,meal_type,status,month) "
    "VALUES('2026-09-02','Legacy row','Lunch','Pending','Sep-2026')")
conn.commit()
legacy = conn.execute("SELECT COALESCE(qty,1) q FROM deliveries "
                      "WHERE name='Legacy row'").fetchone()["q"]
eq("a row written without qty still means one meal", legacy, 1)

# ---------------------------------------------------------------- one-time order
print("\n the Arumbakkam order: 2 Elite meals, one door, Rs 600")
conn.execute(
    "INSERT INTO trial_orders(id,date,name,order_text,amount,payment_status,month,"
    "delivery_date,meal_type,delivery_status,plan_id,qty,address) "
    "VALUES('T900','2026-09-02','Arumbakkam client','Elite Meal Pack',600,'Paid',"
    "'Sep-2026','2026-09-02','Lunch','Pending',2,2,'Arumbakkam')")
conn.commit()
core.sync_trial_delivery(conn, "T900")

row = conn.execute("SELECT * FROM deliveries WHERE trial_id='T900'").fetchall()
eq("one delivery row, not two", len(row), 1)
eq("carrying two boxes", row[0]["qty"], 2)
check("one address, one ride", row[0]["date"] == "2026-09-02")

# ensure_trial_rows must agree with sync_trial_delivery
conn.execute("DELETE FROM deliveries WHERE trial_id='T900'")
conn.commit()
core.ensure_trial_rows(conn, "2026-09-02")
r2 = conn.execute("SELECT qty FROM deliveries WHERE trial_id='T900'").fetchone()
eq("the day generator carries qty too", r2["qty"], 2)

# ---------------------------------------------------------------- the prep list
print("\n the kitchen cooks two")
rows = core.deliveries_for_day(conn, "2026-09-02")
mine = [r for r in rows if r["trial_id"] == "T900"]
eq("the order is on the day sheet once", len(mine), 1)
prep = menu.prep_for_day(conn, "2026-09-02", rows)
check("the prep list counts two meals, not one",
      prep["lunch"]["meals"] >= 2, "meals=%d" % prep["lunch"]["meals"])

# ------------------------------------------- the day's list shows one line per box
print("\n today's orders list one line per box")
# His complaint: a single line reading "x2" made the day's count read one short,
# so the header disagreed with what was actually going out.
shown = core.expand_boxes(rows)
mine_shown = [r for r in shown if r["trial_id"] == "T900"]
eq("two lines for the two-box order", len(mine_shown), 2)
eq("labelled box 1 of 2", (mine_shown[0]["box_no"], mine_shown[0]["box_of"]), (1, 2))
eq("labelled box 2 of 2", (mine_shown[1]["box_no"], mine_shown[1]["box_of"]), (2, 2))
check("both lines share ONE delivery id, so the route has one stop and one tick "
      "marks both", mine_shown[0]["id"] == mine_shown[1]["id"])
eq("the list is longer than the row set by exactly the extra boxes",
   len(shown) - len(rows), sum(core.boxes_on(r) - 1 for r in rows))

summ = core.today_summary(conn, "2026-09-02")
by_hand = sum(core.boxes_on(r) for r in rows)
eq("the day's total counts meals, not stops", summ["total"], by_hand)
eq("the total matches the number of lines shown", summ["total"], len(shown))
eq("the cook list counts two Elite boxes", summ["cook_total"],
   sum(core.boxes_on(r) for r in rows if r["status"] in ("Pending", "Delivered")))

# ------------------------------------------------------- a pack is drawn down right
print("\n a subscription pack is drawn down in meals")
conn.execute("INSERT INTO customers(id,name) VALUES('C9','Two Box')")
conn.execute(
    "INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,start_date,"
    "month,total_meals_scheduled,remaining_meals,status,schedule_mode,slot,"
    "subscription_price,delivery_fee,total_amount) "
    "VALUES('S900','C9','Two Box','Elite Meal Pack','2026-09-01','Sep-2026',"
    "20,20,'Active','daily','Lunch',4000,0,4000)")
conn.commit()
for d, q in (("2026-09-01", 2), ("2026-09-02", 1)):
    conn.execute(
        "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,"
        "status,month,qty) VALUES(?,'S900','C9','Two Box','Lunch','Delivered',"
        "'Sep-2026',?)", (d, q))
conn.commit()
core.recompute_subscription(conn, "S900")
sub = conn.execute("SELECT * FROM subscriptions WHERE id='S900'").fetchone()
eq("three meals delivered across two stops", sub["delivered_count"], 3)
eq("seventeen left of twenty", sub["remaining_meals"], 17)

print("\n and the pack cannot be over-planned")
conn.execute("UPDATE subscriptions SET remaining_meals=2, delivered_count=18 "
             "WHERE id='S900'")
conn.execute(
    "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,"
    "status,month,qty) VALUES('2026-09-03','S900','C9','Two Box','Lunch',"
    "'Pending','Sep-2026',2)")
conn.commit()
before = conn.execute("SELECT COUNT(*) n FROM deliveries WHERE subscription_id='S900' "
                      "AND status='Pending'").fetchone()["n"]
core.ensure_day_rows(conn, "2026-09-04")
after = conn.execute("SELECT COUNT(*) n FROM deliveries WHERE subscription_id='S900' "
                     "AND status='Pending'").fetchone()["n"]
eq("a two-box pending row already uses up the last two meals", after, before)

# ---------------------------------------------------------------- earned revenue
print("\n earned revenue counts meals, not stops")
meals = core._delivered_meal_rows(conn)
mine = [m for m in meals if m["sub_id"] == "S900"]
eq("two delivered stops", len(mine), 2)
two_box = next(m for m in mine if m["day"] == "2026-09-01")
one_box = next(m for m in mine if m["day"] == "2026-09-02")
eq("the two-box stop earns twice the one-box stop", two_box["food"], one_box["food"] * 2)
eq("a 4000 pack over 20 meals earns 200 for one box", one_box["food"], 200)
eq("and 400 for two", two_box["food"], 400)

bucket = core._blank_bucket()
for m in mine:
    core._add_meal(bucket, m)
eq("the bucket counts three meals from two stops", bucket["delivered"], 3)

trial_meals = [m for m in meals if m["trial_id"] == "T900"]
if trial_meals:
    eq("a one-time order's price is NOT multiplied", trial_meals[0]["food"], 600)

print("\n a one-time order marked delivered is valued once")
conn.execute("UPDATE deliveries SET status='Delivered' WHERE trial_id='T900'")
conn.commit()
meals = core._delivered_meal_rows(conn)
t = [m for m in meals if m["trial_id"] == "T900"]
eq("one row", len(t), 1)
eq("worth the 600 he charged, not 1200", t[0]["food"], 600)
eq("but it fed two people", t[0]["qty"], 2)
b2 = core._blank_bucket()
core._add_meal(b2, t[0])
eq("so it counts as two meals delivered", b2["delivered"], 2)
eq("and 600 of one-time sales", b2["trial_sales"], 600)

print("-" * 58)
if FAILS:
    print("FAILED: %d check(s) -> %s\n" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all checks passed\n")
