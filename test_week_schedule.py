# -*- coding: utf-8 -*-
"""Picking a week for a schedule change, and the week strip's counts.

Balaji's case, 21 Oct 2026: a customer moved her days part-way through last week
and he only got round to entering it this week. Before this, a schedule edit
could only take effect from TODAY, so last week stayed wrong for ever — the days
she was actually due were never generated, never ticked, and so never counted
against her pack or into the day's money.

Two things are tested here:

  1. `effective_week` puts the change on the right week — last / this / next —
     and a back-dated one brings the missed days back as Pending so they appear
     in Today -> Unmarked past orders.
  2. It still cannot rewrite what happened. A Delivered or Skipped row is
     history; only Pending rows move.

    python test_week_schedule.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_weeksched.db")
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

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    raise SystemExit("refusing to run against the live database")

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def iso(d):
    return d.strftime("%Y-%m-%d")


# A fixed "today": Wednesday 21 October 2026. Mid-week on purpose — "from today"
# and "from this Monday" have to be different things for the test to mean
# anything.
TODAY = date(2026, 10, 21)
core._today_iso = lambda: iso(TODAY)          # noqa: E305

THIS_MON = date(2026, 10, 19)
LAST_MON = date(2026, 10, 12)
NEXT_MON = date(2026, 10, 26)

db.init_db()
conn = sqlite3.connect(_tmp)
conn.row_factory = sqlite3.Row

print("\nweek-based scheduling")
print("-" * 62)

print("\n the week helpers name the same week everywhere")
check("monday_of a Wednesday is that Monday", core.monday_of(iso(TODAY)) == iso(THIS_MON))
check("monday_of a Monday is itself", core.monday_of(iso(THIS_MON)) == iso(THIS_MON))
check("monday_of a Sunday is the Monday before",
      core.monday_of("2026-10-25") == iso(THIS_MON))
check("week_days gives Mon..Sun", core.week_days(iso(THIS_MON))
      == ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22",
          "2026-10-23", "2026-10-24", "2026-10-25"])
check("shift_week back a week", core.shift_week(iso(TODAY), -1) == "2026-10-14")
check("shift_week on a week", core.shift_week(iso(TODAY), 1) == "2026-10-28")
check("week_of('last')", core.week_of("last") == iso(LAST_MON))
check("week_of('this')", core.week_of("this") == iso(THIS_MON))
check("week_of('next')", core.week_of("next") == iso(NEXT_MON))
check("week_of of nonsense is None", core.week_of("sometime") is None)

# ---------------------------------------------------------------------------
# Her pack: started Mon 5 Oct, 20 meals, Mon/Wed/Fri.
# ---------------------------------------------------------------------------
START = date(2026, 10, 5)
conn.execute(
    "INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,start_date,"
    "month,total_meals_scheduled,remaining_meals,status,schedule_mode,slot) "
    "VALUES('S100','C1','Divya','Elite Meal Pack',?,?,20,20,'Active','weekly','Lunch')",
    (iso(START), "Oct-2026"))
conn.commit()

MON, TUE, WED, THU, FRI = 0, 1, 2, 3, 4


def plan(days):
    return [{"weekday": d, "active": 1, "meals": 1} for d in days]


def sub():
    return conn.execute("SELECT * FROM subscriptions WHERE id='S100'").fetchone()


core.save_day_plan(conn, "S100", plan([MON, WED, FRI]))

# She ate on the first week's Mon/Wed/Fri and they were all ticked.
for d in ("2026-10-05", "2026-10-07", "2026-10-09"):
    conn.execute(
        "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,"
        "status,month) VALUES(?,'S100','C1','Divya','Lunch','Delivered','Oct-2026')", (d,))
conn.commit()
core.recompute_subscription(conn, "S100")

print("\n she told him last Monday she was moving to Mon/Tue/Thu — entered today")
# This is what the form posts when "Last week" is picked.
core.save_day_plan(conn, "S100", plan([MON, TUE, THU]), effective_from=iso(LAST_MON))

cases = [
    ("2026-10-07", "Wed", True,  "first week, before the change — Wed was real"),
    ("2026-10-13", "Tue", True,  "last week, after the change"),
    ("2026-10-15", "Thu", True,  "last week, after the change"),
    ("2026-10-14", "Wed", False, "last week — Wed is gone from the change on"),
    ("2026-10-20", "Tue", True,  "this week"),
    ("2026-10-23", "Fri", False, "this week — Fri is gone"),
]
for iso_d, wd, want, why in cases:
    got = core.plan_for_day(conn, sub(), iso_d)["deliver"]
    check("%s %s -> %s (%s)" % (iso_d, wd, "deliver" if want else "no delivery", why),
          got == want, "got deliver=%s" % got)

print("\n the missed days come back as Pending, ready to tick")
core.resync_scheduled_days(conn, "S100", since=iso(LAST_MON))
created = core.backfill_sub_days(conn, "S100")
pend = [r["date"] for r in conn.execute(
    "SELECT date FROM deliveries WHERE subscription_id='S100' AND status='Pending' "
    "ORDER BY date")]
check("last week's Tue 13 is now owed", "2026-10-13" in pend, str(pend))
check("last week's Thu 15 is now owed", "2026-10-15" in pend, str(pend))
check("last week's Mon 12 is now owed", "2026-10-12" in pend, str(pend))
check("nothing invented on last week's Wed 14", "2026-10-14" not in pend, str(pend))
check("nothing invented on the FIRST week's Tue 6",
      "2026-10-06" not in pend, str(pend))
check("backfill reported what it made", created > 0, "created=%d" % created)

print("\n history is still untouched")
done = [r["date"] for r in conn.execute(
    "SELECT date FROM deliveries WHERE subscription_id='S100' AND status='Delivered' "
    "ORDER BY date")]
check("the 3 delivered meals are all still there",
      done == ["2026-10-05", "2026-10-07", "2026-10-09"], str(done))

print("\n a delivered day the new plan drops is NOT removed")
# Mon 19 Oct (this week) delivered, then the plan is changed to drop Mondays.
conn.execute(
    "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,"
    "status,month) VALUES('2026-10-19','S100','C1','Divya','Lunch','Delivered','Oct-2026')")
conn.commit()
core.save_day_plan(conn, "S100", plan([TUE, THU]), effective_from=iso(THIS_MON))
core.resync_scheduled_days(conn, "S100", since=iso(THIS_MON))
check("Mon 19 Oct is still Delivered", conn.execute(
    "SELECT status FROM deliveries WHERE subscription_id='S100' AND date='2026-10-19'"
).fetchone()["status"] == "Delivered")
check("but Mon 26 Oct is not planned any more",
      not core.plan_for_day(conn, sub(), "2026-10-26")["deliver"])

print("\n a skipped day is history too")
conn.execute(
    "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,"
    "status,month) VALUES('2026-10-20','S100','C1','Divya','Lunch','Skipped','Oct-2026')")
conn.commit()
core.save_day_plan(conn, "S100", plan([THU]), effective_from=iso(THIS_MON))
core.resync_scheduled_days(conn, "S100", since=iso(THIS_MON))
check("Tue 20 Oct is still Skipped", conn.execute(
    "SELECT status FROM deliveries WHERE subscription_id='S100' AND date='2026-10-20'"
).fetchone()["status"] == "Skipped")

print("\n a forward-dated change leaves this week alone")
core.save_day_plan(conn, "S100", plan([MON, WED, FRI]), effective_from=iso(THIS_MON))
conn.commit()
core.save_day_plan(conn, "S100", plan([TUE, THU]), effective_from=iso(NEXT_MON))
check("Wed 21 Oct (this week) still delivers",
      core.plan_for_day(conn, sub(), "2026-10-21")["deliver"])
check("Wed 28 Oct (next week) does not",
      not core.plan_for_day(conn, sub(), "2026-10-28")["deliver"])
check("Tue 27 Oct (next week) does",
      core.plan_for_day(conn, sub(), "2026-10-27")["deliver"])

print("\n re-saving the same week corrects it instead of stacking a generation")


def gens_now():
    return sorted({(r["effective_from"] or "") for r in conn.execute(
        "SELECT effective_from FROM subscription_day_plan WHERE subscription_id='S100'")})


before = gens_now()
core.save_day_plan(conn, "S100", plan([TUE]), effective_from=iso(NEXT_MON))
check("the same count of generations as before the re-save",
      gens_now() == before, "%s -> %s" % (before, gens_now()))
check("one generation per week that was filed, plus the pack's own",
      gens_now() == ["", iso(LAST_MON), iso(THIS_MON), iso(NEXT_MON)], str(gens_now()))
check("and the re-save is what next week now reads",
      sorted(core.day_plan_for(conn, "S100", iso(NEXT_MON))) == [TUE])

print("\n the week strip counts what is actually on each day")
counts = core.week_counts(conn, iso(THIS_MON))
check("Mon 19 counts 1 delivered", counts["2026-10-19"]["done"] == 1,
      str(counts["2026-10-19"]))
check("Tue 20 counts 1 skipped", counts["2026-10-20"]["skipped"] == 1,
      str(counts["2026-10-20"]))
check("Sat 24 is empty", counts["2026-10-24"]["total"] == 0)
check("every day of the week has a cell", len(counts) == 7)

print("\n a two-box stop counts as two meals, not one row")
conn.execute(
    "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,"
    "status,month,qty) VALUES('2026-10-22','S100','C1','Divya','Lunch','Pending',"
    "'Oct-2026',2)")
conn.commit()
counts = core.week_counts(conn, iso(THIS_MON))
check("Thu 22 counts 2 pending meals", counts["2026-10-22"]["pending"] == 2,
      str(counts["2026-10-22"]))

print("\n the one-line summary can be read for a past week")
check("schedule_summary_on reads the plan of that week",
      core.schedule_summary_on(conn, sub(), "2026-10-07") == "Mon, Wed, Fri",
      core.schedule_summary_on(conn, sub(), "2026-10-07"))
check("and of this one",
      core.schedule_summary_on(conn, sub(), iso(THIS_MON)) == "Mon, Wed, Fri",
      core.schedule_summary_on(conn, sub(), iso(THIS_MON)))
check("and of next",
      core.schedule_summary_on(conn, sub(), iso(NEXT_MON)) == "Tue",
      core.schedule_summary_on(conn, sub(), iso(NEXT_MON)))

print("\n a daily pack is unaffected by any of this")
conn.execute(
    "INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,start_date,"
    "month,total_meals_scheduled,remaining_meals,status,schedule_mode,slot) "
    "VALUES('S101','C2','Arun','Signature Meal Pack','2026-10-19','Oct-2026',"
    "20,20,'Active','daily','Lunch')")
conn.commit()
d_sub = conn.execute("SELECT * FROM subscriptions WHERE id='S101'").fetchone()
check("every kitchen day still delivers",
      all(core.plan_for_day(conn, d_sub, d)["deliver"]
          for d in ("2026-10-19", "2026-10-20", "2026-10-21")))
check("its summary is unchanged",
      core.schedule_summary_on(conn, d_sub, iso(LAST_MON)) == "Every kitchen day")

print("-" * 62)
if FAILS:
    print("FAILED: %d check(s) -> %s\n" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all checks passed\n")
