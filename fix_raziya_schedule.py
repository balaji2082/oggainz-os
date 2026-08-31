"""
One-off data correction: undo the phantom orders a schedule change invented.

    python fix_raziya_schedule.py            # show what it would do, change nothing
    python fix_raziya_schedule.py --apply    # do it

Why
---
Raziya (S074) delivered Mon/Wed/Fri from 7 Aug. Around 20 Aug she moved to
Mon/Tue/Thu. The weekly plan had no date on it -- one timeless list per pack --
so changing it did not record a change, it restated the past. The backfill then
read today's Mon/Tue/Thu over her whole history and created Pending orders on:

    Tue 11 Aug     Thu 13 Aug     Tue 18 Aug

She was never due on those days; the record shows her real deliveries that week
were Wed 12 and Fri 14. All three rows were created in one batch on 31 Aug, three
weeks after the dates they claim.

They are not only confusing. Planned and delivered rows both count against the
pack, so three invented orders quietly turned her 20-meal pack into 17.

The code fix is in core.save_day_plan / core.day_plan_for: plans are now stored in
generations stamped with the date they take effect, and every date is read against
the plan that was in force on it. That stops new phantoms. This script clears the
three that already exist and records her real history.

What it does
------------
  * files her ORIGINAL Mon/Wed/Fri plan as the generation with no start date,
    meaning "since this pack began"
  * files the Mon/Tue/Thu plan from CHANGE_DATE below
  * deletes the three phantom Pending rows -- Pending only, so nothing delivered
    or skipped is touched
  * recomputes the pack so the meals she is still owed come back
  * dry run by default; --apply is required to write
  * idempotent: run it twice and the second run finds nothing to do

CHANGE_DATE is 20 Aug because that is the first day a Thursday row was generated
for her (the 20 Aug row she then skipped), and the 25/27 Aug Tue/Thu rows were
created on 21 Aug. If Balaji remembers a different day, change this one constant
and re-run -- nothing else depends on it.
"""

import os
import sys
from datetime import date

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import core                                                      # noqa: E402
import db                                                        # noqa: E402

APPLY = "--apply" in sys.argv

SUB = "S074"
CHANGE_DATE = "2026-08-20"
OLD_DAYS = [0, 2, 4]          # Mon, Wed, Fri
NEW_DAYS = [0, 1, 3]          # Mon, Tue, Thu
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def names(days):
    return ", ".join(WD[d] for d in days)


# init_db, not get_db: the generation column and the widened unique key arrive
# through migrate(), and on the server this script may well be the first thing to
# run after the pull. Without it the writes below fail on the old constraint.
conn = db.init_db()

sub = conn.execute("SELECT * FROM subscriptions WHERE id=?", (SUB,)).fetchone()
if not sub:
    raise SystemExit(f"  {SUB} not found in this database.")

print()
print(f"  {SUB}  {sub['customer_name']}  --  {sub['plan_name']}")
print(f"  started {sub['start_date']} · {sub['total_meals_scheduled']} meals · "
      f"{sub['remaining_meals']} remaining · {sub['status']}")
print()

# --- what is there now -------------------------------------------------------
print("  current stored plan:")
for p in conn.execute("SELECT * FROM subscription_day_plan WHERE subscription_id=? "
                      "ORDER BY COALESCE(effective_from,''), weekday", (SUB,)):
    gen = p["effective_from"] or "(since the pack began)"
    print(f"    {WD[p['weekday']]}  active={p['active']}  from {gen}")

# --- the phantoms ------------------------------------------------------------
def _wd(iso):
    return date.fromisoformat(iso).weekday()


# A Pending row dated before the schedule changed, on a weekday that ONLY the new
# plan delivers. Found by query rather than by three hard-coded ids, so a re-run
# after any similar bad edit still picks up the right rows — and so a day that
# both plans share can never be swept up by mistake.
phantoms = [r for r in conn.execute(
    "SELECT id,date,status,created_at FROM deliveries WHERE subscription_id=? "
    "AND status='Pending' AND date < ? AND COALESCE(is_extra,0)=0 ORDER BY date",
    (SUB, CHANGE_DATE)).fetchall()
    if _wd(r["date"]) in NEW_DAYS and _wd(r["date"]) not in OLD_DAYS]

print()
if phantoms:
    print(f"  phantom orders to delete ({len(phantoms)}):")
    for r in phantoms:
        print(f"    {r['date']} {WD[_wd(r['date'])]}  Pending  "
              f"(created {str(r['created_at'])[:16]})")
else:
    print("  no phantom orders found -- nothing to delete.")

print()
print(f"  plan to record:  {names(OLD_DAYS)} since the pack began")
print(f"                   {names(NEW_DAYS)} from {CHANGE_DATE}")

if not APPLY:
    print()
    print("  Dry run -- nothing was written. Re-run with --apply to do it.")
    raise SystemExit(0)

# --- write -------------------------------------------------------------------
core.save_day_plan(conn, SUB, [{"weekday": d, "active": 1, "meals": 1}
                               for d in OLD_DAYS], effective_from=None)
core.save_day_plan(conn, SUB, [{"weekday": d, "active": 1, "meals": 1}
                               for d in NEW_DAYS], effective_from=CHANGE_DATE)

for r in phantoms:
    conn.execute("DELETE FROM deliveries WHERE id=? AND status='Pending'", (r["id"],))
conn.commit()

core.recompute_subscription(conn, SUB)

after = conn.execute("SELECT remaining_meals,status FROM subscriptions WHERE id=?",
                     (SUB,)).fetchone()
print()
print(f"  Done. Deleted {len(phantoms)} phantom order(s).")
print(f"  {SUB} now shows {after['remaining_meals']} meals remaining ({after['status']}).")
print()
print("  Her real history is untouched: every Delivered and Skipped row is still there.")
