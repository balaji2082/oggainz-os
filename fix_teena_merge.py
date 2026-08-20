"""
One-off data correction: fold Teena's dinner pack into her lunch pack.

    python fix_teena_merge.py            # show what it would do, change nothing
    python fix_teena_merge.py --apply    # do it

Why
---
Teena took two Signature packs and asked for both boxes on the lunch run, to pay
one delivery instead of two:

    S081  lunch pack   booked 16 Aug, started 18 Aug   Rs 4,999 (3,999 + 1,000 delivery)
    S085  dinner pack  booked 18 Aug, started 19 Aug   Rs 3,999

She is a doctor and has been put on nights, so the second meal no longer gets
eaten on time. She does not want to cancel the food -- she wants one meal a day
instead of two, running twice as long: the 40 meals across both packs, taken one
per day.

What it does
------------
  * re-points S085's delivery rows onto S081, marked as extra meals -- so the two
    days she really did get two boxes (19 Aug, and 20 Aug still to go out) stay
    true in the record, and both still draw from her 40
  * makes S081 a 40-meal pack at Rs 10,000, keeping the Rs 1,000 delivery fee
  * carries her money across: Rs 4,999 + Rs 3,999 = Rs 8,998 received
  * deletes S085 and its component rows -- nothing else in the database points at
    it, and its delivery history has already been moved, so nothing is lost
  * recomputes S081, so every count comes from the delivery rows themselves

After it runs she has 40 meals, 3 of them already delivered (18 Aug, and two on
19 Aug), 2 going out today, and one a day from tomorrow.

Note on the money: the two packs took Rs 8,998, not a round Rs 9,000, so the
balance left to collect is Rs 1,002. The script uses what was actually received
rather than rounding it -- change PRICE below if the deal was different.

Safety
------
  * backs the database up first, to backups/pre-teena-<timestamp>.db
  * runs inside one transaction -- either all of it lands or none of it does
  * idempotent: if S085 is already gone it stops and changes nothing
  * dry run by default; --apply is required to write

Run it where the data lives. On PythonAnywhere:

    workon ogenv
    cd oggainz-os && git pull
    python fix_teena_merge.py
    python fix_teena_merge.py --apply
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

KEEP = "S081"            # the lunch pack she keeps
DROP = "S085"            # the dinner pack folded into it
MEALS = 40               # 20 + 20, now taken one a day
PRICE = 9000.0           # food, before delivery
DELIVERY = 1000.0        # the delivery fee already on S081


def money(v):
    return f"Rs {v:,.0f}"


def main():
    conn = db.get_db()
    keep = conn.execute("SELECT * FROM subscriptions WHERE id=?", (KEEP,)).fetchone()
    drop = conn.execute("SELECT * FROM subscriptions WHERE id=?", (DROP,)).fetchone()

    if not keep:
        sys.exit(f"{KEEP} not found in this database.")
    if not drop:
        print(f"{DROP} is already gone — this has been run. Nothing to do.")
        cur = conn.execute("SELECT * FROM subscriptions WHERE id=?", (KEEP,)).fetchone()
        print(f"  {KEEP}: {cur['total_meals_scheduled']} meals · "
              f"{money(cur['total_amount'])} · delivered {cur['delivered_count']} · "
              f"left {cur['remaining_meals']} · {cur['status']}")
        return

    moving = conn.execute(
        "SELECT id, date, status FROM deliveries WHERE subscription_id=? ORDER BY date",
        (DROP,)).fetchall()
    received = (keep["amount_received"] or 0) + (drop["amount_received"] or 0)
    total = PRICE + DELIVERY
    owed = round(total - received, 2)

    print(f"  customer        {keep['customer_name']} ({keep['customer_id']})")
    print(f"  keeping         {KEEP} · {keep['plan_name']} · {keep['slot']} · "
          f"{keep['total_meals_scheduled']} meals · {money(keep['total_amount'])}")
    print(f"  folding in      {DROP} · {drop['total_meals_scheduled']} meals · "
          f"{money(drop['total_amount'])}  (notes: {(drop['notes'] or '').strip()!r})")
    print()
    print(f"  -> {KEEP} becomes {MEALS} meals · {money(total)} "
          f"({money(PRICE)} food + {money(DELIVERY)} delivery)")
    print(f"     received     {money(received)}  "
          f"({money(keep['amount_received'] or 0)} + {money(drop['amount_received'] or 0)})")
    print(f"     still to collect {money(owed)}")
    print(f"  -> {len(moving)} delivery row(s) move to {KEEP} as extra meals:")
    for r in moving:
        print(f"       {r['date']}  ({r['status']})  — her second box that day")
    print(f"  -> {DROP} and its component rows are deleted")
    print()

    if not APPLY:
        print("Dry run — nothing was written. Re-run with --apply to do it.")
        return

    bdir = os.path.join(APP_DIR, "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(bdir, f"pre-teena-{stamp}.db")
    shutil.copyfile(db.DB_PATH, backup)
    print(f"  backed up to {os.path.relpath(backup, APP_DIR)}")

    note = (keep["notes"] or "").strip()
    merged_note = (note + ("\n" if note else "")
                   + f"{DROP} (2nd Signature pack) folded in on "
                     f"{datetime.now().strftime('%d %b %Y')} — she moved to one meal a day "
                     f"for {MEALS} days instead of two a day for 20. Both boxes on the lunch "
                     f"run until 20 Aug; one a day after that.")

    try:
        conn.execute("BEGIN")
        # Her second box on a day is an extra against the merged pack, and it draws
        # from her 40 -- which is exactly what an extra with counts_against_pack does.
        for r in moving:
            conn.execute(
                "UPDATE deliveries SET subscription_id=?, is_extra=1, "
                "counts_against_pack=1 WHERE id=?", (KEEP, r["id"]))

        conn.execute(
            "UPDATE subscriptions SET total_meals_scheduled=?, subscription_price=?, "
            " delivery_fee=?, total_amount=?, amount_received=?, payment_status=?, "
            " notes=? WHERE id=?",
            (MEALS, PRICE, DELIVERY, total, received,
             "Paid" if owed <= 0 else "Partial", merged_note, KEEP))

        conn.execute("DELETE FROM subscription_components WHERE subscription_id=?", (DROP,))
        conn.execute("DELETE FROM subscription_variants WHERE subscription_id=?", (DROP,))
        conn.execute("DELETE FROM subscription_day_plan WHERE subscription_id=?", (DROP,))
        conn.execute("DELETE FROM subscriptions WHERE id=?", (DROP,))
        conn.commit()
    except Exception:
        conn.rollback()
        print("  FAILED — rolled back, database untouched.")
        raise

    core.recompute_subscription(conn, KEEP)

    r = conn.execute("SELECT * FROM subscriptions WHERE id=?", (KEEP,)).fetchone()
    print("\n  done. after:")
    print(f"    {KEEP}  {r['total_meals_scheduled']} meals · {money(r['total_amount'])} · "
          f"received {money(r['amount_received'])} · {r['payment_status']}")
    print(f"          delivered {r['delivered_count']} · left {r['remaining_meals']} · "
          f"{r['status']}")
    print("    her days:")
    for d in conn.execute(
            "SELECT date, status, COALESCE(is_extra,0) x FROM deliveries "
            "WHERE subscription_id=? ORDER BY date, x", (KEEP,)):
        print(f"      {d['date']}  {d['status']:<10}"
              f"{'  (2nd box — extra)' if d['x'] else ''}")
    conn.close()


if __name__ == "__main__":
    main()
