# -*- coding: utf-8 -*-
"""A one-time order can carry its own box.

Balaji, 30 Sep: "if I choose custom pack and I need three hundred grams of chicken
for that meal I couldn't choose that." A one-time order could only point at a
standard pack, and the prep list cooked that pack's recipe. Custom Meal Pack has
no recipe, so a custom order (Ajith DAP, T055, 15 Sep) reached the kitchen as
"not set" with no quantities at all.

Now the order form has the same box builder as a subscription: a pack arrives
pre-filled and every amount is editable; a custom pack is built item by item.
Whatever is in the box is what the prep list and the rider sheet count, times the
number of meals. Orders saved before this still cook their pack's recipe.

    python test_trial_box.py
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import date

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_trialbox.db")
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

# The packs exist before the app starts, as they do on the server, so the app's
# own start-up seeding builds their recipes exactly as it did there.
_c = db.init_db()
for name, pm, pw, pt in [("Signature Meal Pack", 3999, 1299, 249),
                         ("Elite Meal Pack", 4999, 1399, 299),
                         ("Custom Meal Pack", None, None, None)]:
    _c.execute("INSERT INTO plans(name,price_monthly,price_weekly,price_trial,active) "
               "VALUES(?,?,?,?,1)", (name, pm, pw, pt))
_c.commit()
_c.close()

import menu    # noqa: E402
import app as appmod   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def eq(name, got, want):
    check(name, got == want, "got %r, want %r" % (got, want))


client = appmod.app.test_client()
client.get("/settings")                       # the app's first-request-of-the-day jobs

conn = db.get_db()
PLAN = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM plans")}
COMP = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM components")}
DAY = date.today().strftime("%Y-%m-%d")


def order(**kw):
    f = {"date": DAY, "name": "Ajith DAP", "phone": "", "qty": "1", "amount": "0",
         "order_text": "", "plan_id": "", "payment_status": "Paid", "conversion": "",
         "delivery_date": DAY, "meal_type": "Dinner", "delivery_status": "Pending",
         "address": "", "pin": "", "notes": ""}
    f.update({k: str(v) for k, v in kw.items()})
    return f


def box(*items):
    return json.dumps([{"id": COMP[n], "qty": q, "note": note} for n, q, note in items])


def prep_line(slot, name):
    p = menu.prep_for_day(conn, DAY)
    return next((x for x in p[slot]["components"] if x["name"] == name), None), p


print("\none-time order box")
print("-" * 58)
check("seeding built the Elite recipe", bool(menu.plan_recipe(conn, PLAN["Elite Meal Pack"])))
eq("Custom Meal Pack has no recipe of its own",
   len(menu.plan_recipe(conn, PLAN["Custom Meal Pack"])), 0)

print("\n the form offers the builder")
page = client.get("/trials/new").get_data(as_text=True)
check("new-order page has the box", 'id="box_rows"' in page and 'name="comp_json"' in page)
check("…and the item catalog to build from", '"Chicken"' in page)

print("\n his case: custom pack, 300 g chicken, two meals")
n0 = conn.execute("SELECT COUNT(*) FROM trial_orders").fetchone()[0]
r = client.post("/trials/new", data=order(
    plan_id=PLAN["Custom Meal Pack"], order_text="Custom meal pack", qty=2, amount=700,
    comp_json=box(("Chicken", 300, ""), ("Rice (of the day)", 1, ""), ("Egg", 2, "boiled"))))
eq("saved", r.status_code, 302)
tid = conn.execute("SELECT id FROM trial_orders ORDER BY id DESC LIMIT 1").fetchone()[0]
eq("one order", conn.execute("SELECT COUNT(*) FROM trial_orders").fetchone()[0], n0 + 1)
saved = {x["name"]: x["qty"] for x in menu.trial_composition(conn, tid)}
eq("the box is stored as built", saved, {"Chicken": 300.0, "Rice (of the day)": 1.0, "Egg": 2.0})
chicken, prep = prep_line("dinner", "Chicken")
eq("the prep list cooks 300 g × 2 meals", chicken and chicken["total"], 600.0)
check("…shown as two 300 g portions", chicken and "300g × 2" in chicken["breakdown"],
      chicken and chicken["breakdown"])
egg, _ = prep_line("dinner", "Egg")
eq("eggs multiply too", egg and egg["total"], 4.0)
check("it is not reported as 'not set'",
      not any(u.get("trial_id") == tid for u in prep["unmapped"]))
row = conn.execute("SELECT * FROM deliveries WHERE trial_id=?", (tid,)).fetchone()
items, label = menu.order_items(conn, row, DAY)
check("the rider sheet reads the same box",
      {i["name"]: i["qty"] for i in items}.get("Chicken") == 300)

print("\n editing without touching the box keeps it")
r = client.post(f"/trials/{tid}/edit", data=order(
    plan_id=PLAN["Custom Meal Pack"], order_text="Custom meal pack", qty=2, amount=750))
eq("saved", r.status_code, 302)
eq("box unchanged when the form sends no box",
   {x["name"]: x["qty"] for x in menu.trial_composition(conn, tid)},
   {"Chicken": 300.0, "Rice (of the day)": 1.0, "Egg": 2.0})
page = client.get(f"/trials/{tid}/edit").get_data(as_text=True)
check("the edit page opens with the saved box", '"qty": 300.0' in page or '"qty":300.0' in page)

print("\n a standard pack with one amount changed")
r = client.post("/trials/new", data=order(
    name="Priya", plan_id=PLAN["Elite Meal Pack"], order_text="Elite Meal Pack", amount=359,
    meal_type="Lunch",
    comp_json=json.dumps([{"id": x["component_id"],
                           "qty": 200 if x["name"] == "Chicken" else x["qty"],
                           "note": x["note"] or ""}
                          for x in menu.plan_recipe(conn, PLAN["Elite Meal Pack"])])))
t2 = conn.execute("SELECT id FROM trial_orders ORDER BY id DESC LIMIT 1").fetchone()[0]
chicken, _ = prep_line("lunch", "Chicken")
eq("Elite with 200 g chicken cooks 200 g", chicken and chicken["total"], 200.0)
fruit, _ = prep_line("lunch", "Fruits")
check("…and keeps the rest of the Elite box", fruit is not None)

print("\n orders saved before boxes existed are unchanged")
conn.execute("INSERT INTO trial_orders(id,date,name,order_text,amount,month,delivery_date,"
             "meal_type,delivery_status,plan_id,qty) VALUES('T900',?,'Old order',"
             "'Signature Meal Pack',249,'Sep-2026',?,'Lunch','Pending',?,1)",
             (DAY, DAY, PLAN["Signature Meal Pack"]))
conn.commit()
import core   # noqa: E402
core.sync_trial_delivery(conn, "T900")
row = conn.execute("SELECT * FROM deliveries WHERE trial_id='T900'").fetchone()
items, _ = menu.order_items(conn, row, DAY)
eq("no box → it cooks the Signature recipe, exactly as before",
   sorted((i["name"], i["qty"]) for i in items),
   sorted((x["name"], x["qty"]) for x in menu.plan_recipe(conn, PLAN["Signature Meal Pack"])))
page = client.get("/trials/T900/edit").get_data(as_text=True)
check("its edit page shows that recipe in the box, so what you see is what is cooked",
      '"qty": 100.0' in page or '"qty":100.0' in page)

print("\n a custom order with nothing in it is still flagged, not silently dropped")
client.post("/trials/new", data=order(name="Empty", plan_id=PLAN["Custom Meal Pack"],
                                      order_text="Custom", comp_json="[]"))
t3 = conn.execute("SELECT id FROM trial_orders ORDER BY id DESC LIMIT 1").fetchone()[0]
_, prep = prep_line("dinner", "Chicken")
check("empty custom box shows up under 'not set'",
      any(u.get("trial_id") == t3 for u in prep["unmapped"]))

print("\n deleting the order takes its box with it")
client.post(f"/trials/{tid}/delete")
eq("no box rows left for the deleted order",
   conn.execute("SELECT COUNT(*) FROM trial_components WHERE trial_id=?", (tid,)).fetchone()[0], 0)
eq("the other order's box is untouched",
   {x["name"]: x["qty"] for x in menu.trial_composition(conn, t2)}.get("Chicken"), 200.0)

print("\n an item used ONLY in a one-time box is hidden, not deleted, from the menu editor")
in_recipes = conn.execute(
    "SELECT (SELECT COUNT(*) FROM plan_components WHERE component_id=:c) + "
    "(SELECT COUNT(*) FROM subscription_components WHERE component_id=:c)",
    {"c": COMP["Oats"]}).fetchone()[0]
eq("Oats is in no pack recipe and no subscription", in_recipes, 0)
client.post("/trials/new", data=order(name="Oats fan", plan_id=PLAN["Custom Meal Pack"],
                                      comp_json=box(("Oats", 1, ""))))
t4 = conn.execute("SELECT id FROM trial_orders ORDER BY id DESC LIMIT 1").fetchone()[0]
client.post("/menu/component", data={"do": "delete", "id": COMP["Oats"]})
still = conn.execute("SELECT active FROM components WHERE id=?", (COMP["Oats"],)).fetchone()
check("Oats still exists — a one-time box uses it", still is not None)
eq("…hidden from the menu instead", still and still["active"], 0)
page = client.get(f"/trials/{t4}/edit").get_data(as_text=True)
check("the order that uses it still shows it in its box", '"hidden": true' in page
      or '"hidden":true' in page)
eq("…and still cooks it", [i["name"] for i in menu.order_items(
    conn, conn.execute("SELECT * FROM deliveries WHERE trial_id=?", (t4,)).fetchone(), DAY)[0]],
   ["Oats"])

conn.close()
print("\n" + ("ALL GOOD" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
