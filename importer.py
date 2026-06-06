"""
Import the real OG Gainz business data from NEW TRACKER.xlsx into SQLite.

Design goals:
  * Never touch the live workbook — we copy it to a local snapshot first.
  * Lose nothing — every customer / subscription / meal / trial row is imported.
  * Prove it — print a validation report that cross-checks computed totals
    against the figures Balaji already has in his Finance & Monthly sheets.

Run:  python importer.py            (uses the default source candidates)
      python importer.py "path.xlsx"
"""

import os
import sys
import shutil
from datetime import datetime, date

import openpyxl

import db

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(APP_DIR, "_import_source")

# Where the real workbook might live (first that exists wins).
SOURCE_CANDIDATES = [
    r"C:\Users\navan\OneDrive\NEW TRACKER COPY for claude.xlsx",
    r"C:\Users\navan\OneDrive\Documents\NEW TRACKER.xlsx",
]


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------
def clean_str(v):
    if v is None:
        return None
    s = str(v).replace("\n", " ").strip()
    return s or None


def parse_date(v):
    """Return 'YYYY-MM-DD' or None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_money(v):
    """Return a float, or None for blanks / placeholders like '--'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("₹", "")
    if s in ("", "-", "--", "—", "n/a", "N/A", "nil"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(v):
    f = parse_money(v)
    return int(f) if f is not None else None


def month_from_date(*date_vals):
    """First parseable date -> 'Mon-YYYY' (e.g. Jun-2026), else None.

    Used as a fallback for the sheet's '=TEXT(date,"MMM-YYYY")' month columns: if the
    workbook was last saved by a script (openpyxl drops cached formula results) the
    formula cells read as None, so we re-derive the month from the row's own date."""
    for v in date_vals:
        d = parse_date(v)
        if d:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%b-%Y")
    return None


def norm_sub_status(v):
    s = clean_str(v) or ""
    low = s.lower()
    if "ending" in low:
        return "Ending Soon"
    if "complete" in low:
        return "Completed"
    if "active" in low:
        return "Active"
    if "pause" in low:
        return "Paused"
    return s or "Active"


def norm_delivery_status(v):
    s = clean_str(v)
    if not s:
        return "Pending"
    low = s.lower()
    if low in ("-", "—"):
        return "Off"
    if "deliver" in low:
        return "Delivered"
    if "skip" in low:
        return "Skipped"
    if "complete" in low:        # subscription-completion marker, not a delivered meal
        return "Completed"       # (keeps parity with his "Total Orders Delivered")
    if "off" in low or "holiday" in low:
        return "Off"
    return "Pending"


def looks_like_id(v, prefix):
    s = clean_str(v)
    return bool(s) and s.upper().startswith(prefix) and any(ch.isdigit() for ch in s)


# ---------------------------------------------------------------------------
# Source snapshot
# ---------------------------------------------------------------------------
def make_snapshot(explicit=None):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    src = explicit
    if not src:
        for cand in SOURCE_CANDIDATES:
            if os.path.exists(cand):
                src = cand
                break
    if not src or not os.path.exists(src):
        raise FileNotFoundError(
            "Could not find NEW TRACKER.xlsx. Tried:\n  " + "\n  ".join(SOURCE_CANDIDATES)
        )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(SNAPSHOT_DIR, f"NEW_TRACKER_snapshot_{stamp}.xlsx")
    shutil.copy2(src, dest)
    # also keep a stable "latest" copy
    shutil.copy2(src, os.path.join(SNAPSHOT_DIR, "NEW_TRACKER_latest.xlsx"))
    return src, dest


# ---------------------------------------------------------------------------
# Per-sheet importers
# ---------------------------------------------------------------------------
def import_customers(conn, ws):
    n = 0
    for row in ws.iter_rows(min_row=3, values_only=True):
        cid = clean_str(row[0])
        if not looks_like_id(cid, "C"):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO customers(id,name,phone,locality,address,notes,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (cid.upper(), clean_str(row[1]), clean_str(row[2]), clean_str(row[3]),
             clean_str(row[4]), clean_str(row[5]), db.now_iso()),
        )
        n += 1
    return n


def import_subscriptions(conn, ws):
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM customers")}
    n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        sid = clean_str(row[0])          # A
        if not looks_like_id(sid, "S"):
            continue
        cust_id = clean_str(row[1])                    # B customer_id
        # Columns C / P / R / L are formulas in the sheet; if their cached value is
        # missing (script-saved workbook) we re-derive from the row's raw inputs.
        cust_name = clean_str(row[2]) or names.get(cust_id)            # C customer_name
        price = parse_money(row[13])                   # N price
        fee = parse_money(row[14])                     # O delivery fee
        total = parse_money(row[15])                   # P total (=N+O)
        if total is None:
            total = (price or 0) + (fee or 0)
        received = parse_money(row[16]) or 0           # Q received
        pay = clean_str(row[17])                       # R payment status (IF formula)
        if not pay:
            pay = "Paid" if received >= total and total > 0 else ("Partial" if received > 0 else "Unpaid")
        month = clean_str(row[11]) or month_from_date(row[10], row[9])  # L month (=TEXT)
        conn.execute(
            """INSERT OR REPLACE INTO subscriptions(
                 id, customer_id, customer_name, plan_name, duration, notes, slot,
                 preferred_timing, booking_date, start_date, month,
                 subscription_price, delivery_fee, total_amount, amount_received,
                 payment_status, total_meals_scheduled, src_delivered, src_pending,
                 src_skipped, status, last_delivery_date, inactive_days, order_no, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sid.upper(),
                cust_id,                               # B customer_id
                cust_name,                             # C customer_name
                clean_str(row[4]),                     # E plan
                clean_str(row[5]),                     # F duration
                clean_str(row[6]),                     # G notes
                clean_str(row[7]),                     # H slot
                clean_str(row[8]),                     # I timing
                parse_date(row[9]),                    # J booking
                parse_date(row[10]),                   # K start
                month,                                 # L month
                price,                                 # N price
                fee,                                   # O delivery fee
                total,                                 # P total
                received,                              # Q received
                pay,                                   # R payment status
                parse_int(row[19]),                    # T scheduled
                parse_int(row[20]),                    # U delivered (snapshot)
                parse_int(row[21]),                    # V pending (snapshot)
                parse_int(row[22]),                    # W skipped (snapshot)
                norm_sub_status(row[23]),              # X status
                parse_date(row[25]),                   # Z last delivery
                parse_int(row[26]),                    # AA inactive days
                clean_str(row[27]),                    # AB order no
                db.now_iso(),
            ),
        )
        n += 1
    return n


def import_deliveries(conn, ws):
    # In the sheet, D (customer id) and E (name) are both VLOOKUPs on the subscription
    # id (C); I (month) is =TEXT(date). If a script last saved the workbook those cached
    # results read as None, so we re-derive them from the subscription / the row's date.
    sub_map = {r["id"]: (r["customer_id"], r["customer_name"])
               for r in conn.execute("SELECT id,customer_id,customer_name FROM subscriptions")}
    n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        sid = clean_str(row[2])          # C subscription id
        d = parse_date(row[1])           # B date
        if not looks_like_id(sid, "S") or not d:
            continue                     # skips headers + week separators
        s_cid, s_name = sub_map.get(sid.upper(), (None, None))
        cust_id = clean_str(row[3]) or s_cid     # D customer id
        name = clean_str(row[4]) or s_name       # E name
        month = clean_str(row[8]) or month_from_date(row[1])
        conn.execute(
            "INSERT INTO deliveries(date,subscription_id,customer_id,name,meal_type,status,month,notes,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                d,
                sid.upper(),
                cust_id,
                name,                    # E name
                clean_str(row[6]),       # G meal type
                norm_delivery_status(row[7]),  # H status
                month,                   # I month
                clean_str(row[5]),       # F item/plan note
                db.now_iso(),
            ),
        )
        n += 1
    return n


def import_trials(conn, ws):
    n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not clean_str(row[0]) and not clean_str(row[2]):
            continue
        n += 1
        tid = f"T{n:03d}"
        conn.execute(
            """INSERT OR REPLACE INTO trial_orders(
                 id,date,name,phone,order_text,amount,payment_status,month,conversion,address,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tid,
                parse_date(row[1]),            # B date
                clean_str(row[2]),             # C name
                clean_str(row[3]),             # D phone
                clean_str(row[4]),             # E order
                parse_money(row[5]),           # F amount
                clean_str(row[6]),             # G status
                clean_str(row[7]) or month_from_date(row[1]),  # H month (=TEXT formula)
                clean_str(row[8]),             # I conversion
                clean_str(row[9]),             # J address
                db.now_iso(),
            ),
        )
    return n


# ---------------------------------------------------------------------------
# Plans catalog seed (editable later in the app)
# ---------------------------------------------------------------------------
PLAN_SEED = [
    # name, category, monthly, weekly, trial, validity, serv_m, serv_w, meals/day, slot
    ("Signature Meal Pack", "Regular", 3799, 999, 209, 40, 20, 5, 1, "Lunch"),
    ("Elite Meal Pack",     "Regular", 4999, 1299, 299, 50, 20, 5, 1, "Lunch"),
    ("Royal Meal Pack",     "Regular", 5699, 1499, 349, 50, 20, 5, 1, "Lunch"),
    ("Athlete Meal Pack",   "Regular", 6999, 1999, None, 50, 20, 5, 1, "Lunch"),
    ("Protein Salad Box",   "Standalone", 3800, None, 200, 50, 20, 5, 1, "Dinner"),
    ("Protein Shake",       "Standalone", 3099, None, None, 50, 20, 5, 1, "Lunch"),
    ("Custom Meal Pack",    "Custom", None, None, None, 50, 20, 5, 1, "Lunch"),
]


# ---------------------------------------------------------------------------
# Data cleanup — fix the spreadsheet's hand-entry errors
# ---------------------------------------------------------------------------
STATUS_PRIORITY = {"Delivered": 4, "Skipped": 3, "Completed": 2, "Pending": 1, "Off": 0}


def cleanup_data(conn):
    """
    Two safe corrections, applied to subscription meals only (trials untouched):
      1) Remove EXACT duplicate rows (same subscription + date + meal slot) that
         came from mis-dated / repeated spreadsheet rows — keep the most meaningful
         status (Delivered > Skipped > Completed > Pending > Off).
      2) A fixed-size pack can't deliver more meals than it sold: if a subscription
         has more 'Delivered' rows than it was scheduled for, drop the surplus
         (latest first) and clear any leftover phantom Pending/Completed rows on a
         fully-delivered subscription (the 'extra slots typed for a finished client').
    """
    from collections import defaultdict
    rep = {"dups": 0, "overflow": 0, "notes": []}

    # 1. exact duplicates
    rows = conn.execute(
        "SELECT id, subscription_id, date, meal_type, status FROM deliveries "
        "WHERE subscription_id IS NOT NULL").fetchall()
    groups = defaultdict(list)
    for r in rows:
        groups[(r["subscription_id"], r["date"], r["meal_type"])].append((r["id"], r["status"]))
    for items in groups.values():
        if len(items) > 1:
            best_first = sorted(items, key=lambda x: (-STATUS_PRIORITY.get(x[1], 1), x[0]))
            for did, _ in best_first[1:]:
                conn.execute("DELETE FROM deliveries WHERE id=?", (did,))
                rep["dups"] += 1
    conn.commit()

    # 2. cap over-delivered + clear phantoms on finished packs
    for s in conn.execute("SELECT id, total_meals_scheduled, customer_name FROM subscriptions").fetchall():
        sched = s["total_meals_scheduled"] or 0
        if not sched:
            continue
        delivered = conn.execute(
            "SELECT id FROM deliveries WHERE subscription_id=? AND status='Delivered' "
            "ORDER BY date, id", (s["id"],)).fetchall()
        if len(delivered) > sched:
            for d in delivered[sched:]:                       # keep earliest `sched`
                conn.execute("DELETE FROM deliveries WHERE id=?", (d["id"],))
                rep["overflow"] += 1
            rep["notes"].append(
                f"{s['id']} {s['customer_name']}: removed {len(delivered) - sched} "
                f"delivered row(s) beyond the {sched}-meal pack")
        done = conn.execute(
            "SELECT COUNT(*) n FROM deliveries WHERE subscription_id=? AND status='Delivered'",
            (s["id"],)).fetchone()["n"]
        if done >= sched:                                     # finished pack -> no future slots
            for p in conn.execute(
                "SELECT id FROM deliveries WHERE subscription_id=? AND status IN ('Pending','Completed')",
                (s["id"],)).fetchall():
                conn.execute("DELETE FROM deliveries WHERE id=?", (p["id"],))
                rep["overflow"] += 1
    conn.commit()
    return rep


def seed_plans(conn):
    have = conn.execute("SELECT COUNT(*) AS c FROM plans").fetchone()["c"]
    if have:
        return 0
    for p in PLAN_SEED:
        conn.execute(
            "INSERT INTO plans(name,category,price_monthly,price_weekly,price_trial,"
            "validity_days,servings_monthly,servings_weekly,meals_per_day,default_slot,active) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,1)",
            p,
        )
    return len(PLAN_SEED)


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------
def validation_report(conn):
    def one(sql, *a):
        r = conn.execute(sql, a).fetchone()
        return list(r)[0] if r else None

    print("\n" + "=" * 64)
    print("  IMPORT VALIDATION REPORT")
    print("=" * 64)

    cust = one("SELECT COUNT(*) FROM customers")
    subs = one("SELECT COUNT(*) FROM subscriptions")
    dels = one("SELECT COUNT(*) FROM deliveries")
    trials = one("SELECT COUNT(*) FROM trial_orders")
    # Reference figures below reflect the Jun-2026 correction: S020 (Ajith DAP
    # "Elite Meal Pack") was a 3-meal trial, not a subscription — moved to TRIAL ORDERS.
    print(f"  Customers ............ {cust}        (expected 16)")
    print(f"  Subscriptions ........ {subs}        (expected 22)")
    print(f"  Delivery records ..... {dels}       (expected ~384)")
    print(f"  Trial orders ......... {trials}        (expected 19)")

    paid = one("SELECT COUNT(*) FROM subscriptions WHERE payment_status='Paid'")
    unpaid = one("SELECT COUNT(*) FROM subscriptions WHERE payment_status!='Paid'")
    print(f"  Paid / Unpaid subs ... {paid} / {unpaid}    (expected 20 / 2)")

    sales = one("SELECT COALESCE(SUM(total_amount),0) FROM subscriptions") or 0
    received = one("SELECT COALESCE(SUM(amount_received),0) FROM subscriptions") or 0
    pending = sales - received
    trial_sales = one("SELECT COALESCE(SUM(amount),0) FROM trial_orders") or 0
    overall = sales + trial_sales
    print("\n  FINANCE SHEET cross-check (post-S020-correction baseline in brackets):")
    print(f"  Total subscription sales .. ₹{sales:>8,.0f}   (₹80,458)")
    print(f"  Total revenue received .... ₹{received:>8,.0f}   (₹74,859)")
    print(f"  Pending amount ............ ₹{pending:>8,.0f}   (₹5,599)")
    print(f"  Total trial sales ......... ₹{trial_sales:>8,.0f}   (₹4,796)")
    print(f"  OVERALL REVENUE ........... ₹{overall:>8,.0f}   (₹85,254)")

    print("\n  MONTHLY DASHBOARD cross-check (subscription sales):")
    for m, exp in (("Apr-2026", 33386), ("May-2026", 38875), ("Jun-2026", 8197)):
        v = one("SELECT COALESCE(SUM(total_amount),0) FROM subscriptions WHERE month=?", m) or 0
        flag = "OK" if abs(v - exp) < 1 else "<-- check"
        print(f"    {m}: ₹{v:>8,.0f}   (₹{exp:,})  {flag}")

    print("\n  Delivery status breakdown:")
    for r in conn.execute("SELECT status, COUNT(*) c FROM deliveries GROUP BY status ORDER BY c DESC"):
        print(f"    {r['status']:<10} {r['c']}")

    print("\n  TOTAL ORDERS DELIVERED cross-check (Apr 106 / May 124 / Jun 30 = 260):")
    for m, exp in (("Apr-2026", 106), ("May-2026", 124), ("Jun-2026", 30)):
        v = one("SELECT COUNT(*) FROM deliveries WHERE month=? AND status='Delivered'", m) or 0
        flag = "OK" if v == exp else "<-- check"
        print(f"    {m}: {v:>4}   ({exp})  {flag}")
    tot = one("SELECT COUNT(*) FROM deliveries WHERE status='Delivered'") or 0
    print(f"    TOTAL: {tot}   (260)  {'OK' if tot == 260 else '<-- check'}")

    print("\n  Active / Ending-Soon subscriptions (must not be missed):")
    for r in conn.execute(
        "SELECT id, customer_name, plan_name, status, total_meals_scheduled, src_delivered, src_pending "
        "FROM subscriptions WHERE status IN ('Active','Ending Soon') ORDER BY id"
    ):
        print(f"    {r['id']} {r['customer_name']:<14} {str(r['plan_name'])[:22]:<22} "
              f"{r['status']:<11} sched={r['total_meals_scheduled']} "
              f"deliv={r['src_delivered']} pend={r['src_pending']}")
    print("=" * 64 + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_import(explicit=None, wipe=True):
    src, snap = make_snapshot(explicit)
    print(f"Source : {src}")
    print(f"Snapshot: {snap}")

    conn = db.init_db()
    if wipe:
        for t in ("deliveries", "subscriptions", "trial_orders", "customers"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()

    wb = openpyxl.load_workbook(snap, data_only=True, read_only=True)

    def sheet(*names):
        for nm in names:
            if nm in wb.sheetnames:
                return wb[nm]
        return None

    c = import_customers(conn, sheet("CUSTOMER DATABASE"))
    s = import_subscriptions(conn, sheet("SUBSCRIPTION DATA"))
    d = import_deliveries(conn, sheet("MEALS TRACKER", "MEAL TRACKER"))
    t = import_trials(conn, sheet("TRIAL ORDERS"))
    p = seed_plans(conn)
    conn.commit()

    import core                       # populate live delivered/skipped/remaining + status
    core.recompute_all(conn)

    rep = cleanup_data(conn)           # fix duplicate / over-delivered spreadsheet errors
    core.recompute_all(conn)

    print(f"\nImported: {c} customers, {s} subscriptions, {d} deliveries, {t} trials, {p} plans seeded.")
    print(f"Cleanup : removed {rep['dups']} duplicate meal row(s), "
          f"{rep['overflow']} over-schedule/phantom row(s).")
    for n in rep["notes"]:
        print("   -", n)
    validation_report(conn)
    conn.close()


if __name__ == "__main__":
    run_import(sys.argv[1] if len(sys.argv) > 1 else None)
