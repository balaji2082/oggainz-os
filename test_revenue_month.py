# -*- coding: utf-8 -*-
"""The big green box on Revenue follows the month being looked at.

Balaji, 30 Sep: September's box read about a lakh, and it was right -- but picking
August in the month filter changed every table on the page EXCEPT that box, which
kept showing September. It was hard-wired to the current month.

Now the box shows the picked month (this month when none is picked) with the same
detail, and has its own ‹ › arrows to step month by month.

    python test_revenue_month.py
"""
import os
import re
import sys
import tempfile
from datetime import date, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_revmonth.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
os.environ["OG_DB_PATH"] = _tmp
os.environ.setdefault("OG_SECRET_KEY", "test-secret")
os.environ.pop("OG_PASSWORD", None)

sys.path.insert(0, APP_DIR)
import db      # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    raise SystemExit("refusing to run against the live database")

import app as appmod   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


# Months relative to the real today, because "this month" is the real today's.
T = date.today()
THIS = T.replace(day=1)
PREV = (THIS - timedelta(days=1)).replace(day=1)
OLDER = (PREV - timedelta(days=1)).replace(day=1)
lbl = lambda d: d.strftime("%b-%Y")            # noqa: E731
full = lambda d: d.strftime("%B %Y")           # noqa: E731

client = appmod.app.test_client()
client.get("/settings")

conn = db.get_db()
conn.execute("INSERT INTO customers(id,name) VALUES('C1','A'),('C2','B')")
subs = [("S001", "C1", OLDER, 4000, 0), ("S002", "C1", PREV, 5000, 500),
        ("S003", "C2", PREV, 6000, 0), ("S004", "C2", THIS, 7000, 1000)]
for sid, cid, d, price, fee in subs:
    conn.execute(
        "INSERT INTO subscriptions(id,customer_id,customer_name,plan_name,booking_date,"
        "start_date,month,subscription_price,delivery_fee,total_amount,amount_received,"
        "payment_status,total_meals_scheduled,status,created_at) "
        "VALUES(?,?,?,'Elite Meal Pack',?,?,?,?,?,?,?,?,20,'Completed',?)",
        (sid, cid, cid, d.isoformat(), d.isoformat(), lbl(d), price, fee, price + fee,
         price + fee if sid != "S003" else 0, "Paid" if sid != "S003" else "Unpaid",
         db.now_iso()))
conn.execute("INSERT INTO trial_orders(id,date,name,amount,payment_status,month) "
             "VALUES('T001',?, 'X', 299, 'Paid', ?)", (PREV.isoformat(), lbl(PREV)))
conn.commit()
conn.close()


def hero(q=""):
    r = client.get("/revenue" + q)
    h = r.get_data(as_text=True)
    box = h[h.find('month-hero'):h.find('rev-boxes')]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", box))
    arrows = re.findall(r'class="mh-arrow[^"]*"(?: href="([^"]+)")?', box)
    return r.status_code, text, [a.replace("&amp;", "&") for a in arrows]


print("\nrevenue — the green box follows the month")
print("-" * 58)

code, text, arrows = hero()
check("page loads", code == 200)
check("no month picked → this month", f"This month · {full(THIS)}" in text, text[:120])
check("…with this month's revenue (7,000 + 1,000)", "₹8,000" in text, text[:160])

code, text, arrows = hero(f"?period=day&month={lbl(PREV)}")
check("last month picked → the box says last month", full(PREV) in text and "This month ·"
      not in text, text[:120])
check("…with last month's revenue (5,500 + 6,000 + 299 one-time)", "₹11,799" in text,
      text[:160])
check("…its packs sold", "₹11,500" in text and "2 packs" in text)
check("…its one-time orders", "₹299" in text and "1 trial" in text)
check("…what is still to collect for that month (S003 unpaid)", "To collect ₹6,000" in text,
      text)
check("…growth measured against the month before it", f"vs {lbl(OLDER)}" in text, text)
check("‹ goes one month further back", any(f"month={lbl(OLDER)}" in a for a in arrows), str(arrows))
check("› comes forward to this month", any(f"month={lbl(THIS)}" in a for a in arrows), str(arrows))
check("the arrows keep the Day/Week/Month view", all("period=day" in a for a in arrows if a))
check("a way straight back to this month", "This month ›" in text)
check("'open day by day' opens THAT month", f"Open {full(PREV)}, day by day" in text)

code, text, arrows = hero(f"?period=week&month={lbl(OLDER)}")
check("two months back works too", full(OLDER) in text and "₹4,000" in text, text[:160])

code, text, arrows = hero(f"?period=month&month={lbl(PREV)}")
check("the Month view honours the month as well", full(PREV) in text and "₹11,799" in text)

code, text, arrows = hero("?month=Nonsense-1999")
check("a bad month falls back to this month, no error",
      code == 200 and f"This month · {full(THIS)}" in text)

print("\n" + ("ALL GOOD" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
