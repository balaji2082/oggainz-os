"""
One-off data correction: split Gowtham's S066 across the two months it spans.

    python fix_gowtham_split.py            # show what it would do, change nothing
    python fix_gowtham_split.py --apply    # do it

Why
---
S066 is a Signature Meal Pack, 24 meals for Rs 4,800 (Rs 200 a meal), booked and
started Wed 29 Jul 2026. Three of those meals were eaten in July (29, 30, 31) and
the remaining twenty-one in August. The whole Rs 4,800 is stamped Jul-2026,
because revenue is dated by when a pack was booked rather than when its meals are
delivered -- so July reads about Rs 600 too high and August about Rs 4,200 too low,
and the month-on-month line shows a spike and a dip that never happened.

What it does
------------
  * creates a new 3-meal pack dated July, at Rs 600, marked Paid and Completed
  * re-points the three July delivery rows (29-31 Jul) onto it
  * leaves S066 with its 21 August meals at Rs 4,200, re-dated to August
  * recomputes both packs so every count comes from the delivery rows themselves

Safety
------
  * backs the database up first, to backups/pre-gowtham-<timestamp>.db
  * runs inside one transaction -- either all of it lands or none of it does
  * idempotent: if the July pack already exists it stops and changes nothing
  * dry run by default; --apply is required to write

Run it where the data actually lives. On PythonAnywhere:

    workon ogenv
    cd oggainz-os && git pull
    python fix_gowtham_split.py            # read the plan
    python fix_gowtham_split.py --apply
"""

import os
import shutil
import sys
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import core                                                      # noqa: E402
import db                                                        # noqa: E402

APPLY = "--apply" in sys.argv

SRC_SUB = "S066"
JULY_DAYS = ("2026-07-29", "2026-07-30", "2026-07-31")
UNIT_PRICE = 200.0                       # Rs 4,800 / 24 meals
AUG_START = "2026-08-03"                 # the first August meal on the pack


def money(v):
    return f"Rs {v:,.0f}"


def next_sub_id(conn):
    mx = 0
    for r in conn.execute("SELECT id FROM subscriptions WHERE id LIKE 'S%'"):
        tail = "".join(ch for ch in r["id"][1:] if ch.isdigit())
        if tail:
            mx = max(mx, int(tail))
    return f"S{mx + 1:03d}"


def main():
    conn = db.get_db()
    sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (SRC_SUB,)).fetchone()
    if not sub:
        sys.exit(f"{SRC_SUB} not found in this database.")

    july = conn.execute(
        "SELECT id, date, status FROM deliveries WHERE subscription_id=? AND date<'2026-08-01' "
        "ORDER BY date", (SRC_SUB,)).fetchall()
    if not july:
        print(f"{SRC_SUB} has no July delivery rows left — already split. Nothing to do.")
        return

    already = conn.execute(
        "SELECT id FROM subscriptions WHERE customer_id=? AND month='Jul-2026' "
        "AND total_meals_scheduled=? AND id!=?",
        (sub["customer_id"], len(july), SRC_SUB)).fetchone()
    if already:
        print(f"A {len(july)}-meal July pack already exists ({already['id']}). Nothing to do.")
        return

    n_july = len(july)
    n_rest = (sub["total_meals_scheduled"] or 0) - n_july
    july_amt = round(n_july * UNIT_PRICE, 2)
    rest_amt = round(n_rest * UNIT_PRICE, 2)
    new_id = next_sub_id(conn)

    print(f"  customer          {sub['customer_name']} ({sub['customer_id']})")
    print(f"  pack              {SRC_SUB} · {sub['plan_name']} · {sub['slot']}")
    print(f"  as it stands      {sub['total_meals_scheduled']} meals · "
          f"{money(sub['total_amount'])} · stamped {sub['month']}")
    print(f"  unit price        {money(UNIT_PRICE)}  ({money(sub['total_amount'])}"
          f" / {sub['total_meals_scheduled']} meals)")
    print()
    print(f"  -> new {new_id}      {n_july} meals · {money(july_amt)} · Jul-2026 · Completed")
    for r in july:
        print(f"       moves {r['date']}  ({r['status']})")
    print(f"  -> {SRC_SUB} becomes  {n_rest} meals · {money(rest_amt)} · Aug-2026 · "
          f"starts {AUG_START}")
    print()

    if not APPLY:
        print("Dry run — nothing was written. Re-run with --apply to do it.")
        return

    bdir = os.path.join(APP_DIR, "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(bdir, f"pre-gowtham-{stamp}.db")
    shutil.copyfile(db.DB_PATH, backup)
    print(f"  backed up to {os.path.relpath(backup, APP_DIR)}")

    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO subscriptions(id, customer_id, customer_name, plan_name, duration, "
            " notes, slot, preferred_timing, booking_date, start_date, month, "
            " subscription_price, delivery_fee, total_amount, amount_received, "
            " payment_status, total_meals_scheduled, status, created_at, schedule_mode) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id, sub["customer_id"], sub["customer_name"], sub["plan_name"],
             sub["duration"],
             f"July portion of {SRC_SUB} — {n_july} meals delivered 29–31 Jul, "
             f"split out so each month carries the meals it actually served.",
             sub["slot"], sub["preferred_timing"],
             sub["booking_date"], sub["start_date"], "Jul-2026",
             july_amt, 0.0, july_amt, july_amt, "Paid", n_july, "Completed",
             db.now_iso(), sub["schedule_mode"] or "daily"))

        for r in july:
            conn.execute("UPDATE deliveries SET subscription_id=?, month='Jul-2026' "
                         "WHERE id=?", (new_id, r["id"]))

        conn.execute(
            "UPDATE subscriptions SET total_meals_scheduled=?, subscription_price=?, "
            " total_amount=?, amount_received=?, month='Aug-2026', booking_date=?, "
            " start_date=? WHERE id=?",
            (n_rest, rest_amt, rest_amt,
             min(sub["amount_received"] or 0, rest_amt) if (sub["amount_received"] or 0) < rest_amt
             else rest_amt,
             AUG_START, AUG_START, SRC_SUB))
        conn.commit()
    except Exception:
        conn.rollback()
        print("  FAILED — rolled back, database untouched.")
        raise

    core.recompute_subscription(conn, new_id)
    core.recompute_subscription(conn, SRC_SUB)

    print("\n  done. after:")
    for sid in (new_id, SRC_SUB):
        r = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
        print(f"    {sid}  {r['month']}  {r['total_meals_scheduled']} meals · "
              f"{money(r['total_amount'])} · delivered {r['delivered_count']} · "
              f"left {r['remaining_meals']} · {r['status']}")
    conn.close()


if __name__ == "__main__":
    main()
