"""
OG Gainz Command Center — menu engine.

The single place that knows WHAT food goes in every box:
  * components   — the editable catalog (chicken, egg, carb, veggies…)
  * plan recipes — what a standard pack contains (editable in the Menu editor)
  * subscription compositions — what one subscriber actually gets (recipe + add-ons
    or a fully custom build)
  * component rules — standing tweaks ("paneer instead of chicken on Tue/Fri")
  * the PREP LIST — exact kitchen quantities per slot and for the whole day.

Everything is deterministic: the prep list is pure math over these tables, never AI.
"""

from collections import defaultdict
from datetime import datetime

import db

# ---------------------------------------------------------------------------
# Catalog seed (from the June-2026 brochure) — runs once, then fully editable.
# ---------------------------------------------------------------------------
SEED_COMPONENTS = [
    # name,                unit,      step, default, addon_price, protein,        emoji, sort
    ("Chicken",            "g",        50,   100,     60,   "11g P / 50g",        "🍗", 10),
    ("Paneer",             "g",       100,   100,    100,   "16g P / 100g",       "🧀", 20),
    ("Fish Fillet (Basa)", "g",       150,   150,    250,   "25g P / 150g",       "🐟", 30),
    ("Egg",                "pcs",       1,     1,     20,   "6g P",               "🥚", 40),
    ("Carb of the day",    "serving",   1,     1,   None,   None,                 "🍚", 50),
    ("Rice (of the day)",  "serving",   1,     1,     50,   None,                 "🍛", 55),
    ("Sweet Potato",       "serving",   1,     1,     40,   None,                 "🍠", 60),
    ("Chapathi (2 pc)",    "serving",   1,     1,     50,   None,                 "🫓", 65),
    ("Oats",               "serving",   1,     1,     60,   None,                 "🥣", 70),
    ("Veggies",            "serving",   1,     1,     40,   None,                 "🥦", 80),
    ("Fruits",             "serving",   1,     1,     60,   None,                 "🍎", 90),
    ("Yogurt (50ml)",      "serving",   1,     1,     30,   "4g P",               "🥛", 100),
    ("Legumes / Sprouts",  "serving",   1,     1,     40,   "5–8g P",             "🌱", 110),
    ("Protein Shake",      "serving",   1,     1,    200,   "15g P",              "🥤", 120),
    ("Protein Salad",      "serving",   1,     1,    200,   "35g P",              "🥗", 130),
]

# plan name → [(component name, qty, note)]
SEED_RECIPES = {
    "Signature Meal Pack": [("Chicken", 100, None), ("Egg", 1, "boiled/scrambled"),
                            ("Veggies", 1, None), ("Carb of the day", 1, None)],
    "Elite Meal Pack":     [("Chicken", 100, None), ("Egg", 1, "boiled/scrambled"),
                            ("Veggies", 1, None), ("Carb of the day", 1, None),
                            ("Fruits", 1, None), ("Yogurt (50ml)", 1, None)],
    "Athlete Meal Pack":   [("Chicken", 150, None), ("Egg", 2, "boiled/scrambled"),
                            ("Veggies", 1, None), ("Carb of the day", 1, None),
                            ("Fruits", 1, None), ("Yogurt (50ml)", 1, None)],
    "Protein Salad Box":   [("Protein Salad", 1, None)],
    "Protein Shake":       [("Protein Shake", 1, None)],
    "Custom Meal Pack":    [],
}

# Brochure prices: (monthly, weekly, trial, validity_days)
SEED_PLAN_PRICES = {
    "Signature Meal Pack": (3999, 1299, 249, 40),
    "Elite Meal Pack":     (4999, 1399, 299, 50),
    "Athlete Meal Pack":   (5999, 1699, 349, 50),
}


def seed_menu(conn):
    """Idempotent: seeds the component catalog + pack recipes and syncs plan prices
    to the brochure exactly once (guarded by a settings flag). Existing
    subscriptions are never touched — they keep the price they were sold at."""
    if db.get_setting(conn, "menu_seeded_v1"):
        return False
    for name, unit, step, dq, price, protein, emoji, sort in SEED_COMPONENTS:
        if not conn.execute("SELECT 1 FROM components WHERE name=?", (name,)).fetchone():
            conn.execute(
                "INSERT INTO components(name,unit,step,default_qty,addon_price,protein,emoji,sort,active) "
                "VALUES(?,?,?,?,?,?,?,?,1)", (name, unit, step, dq, price, protein, emoji, sort))
    comp_id = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM components")}

    # sync plan prices to the brochure + retire Royal (kept for history)
    for pname, (pm, pw, pt, vd) in SEED_PLAN_PRICES.items():
        conn.execute("UPDATE plans SET price_monthly=?, price_weekly=?, price_trial=?, "
                     "validity_days=? WHERE name=?", (pm, pw, pt, vd, pname))
    conn.execute("UPDATE plans SET active=0 WHERE name='Royal Meal Pack'")

    for pname, recipe in SEED_RECIPES.items():
        plan = conn.execute("SELECT id FROM plans WHERE name=?", (pname,)).fetchone()
        if not plan:
            continue
        if conn.execute("SELECT 1 FROM plan_components WHERE plan_id=?", (plan["id"],)).fetchone():
            continue
        for cname, qty, note in recipe:
            conn.execute("INSERT INTO plan_components(plan_id,component_id,qty,note) VALUES(?,?,?,?)",
                         (plan["id"], comp_id[cname], qty, note))

    # Backfill compositions for existing ACTIVE subs whose plan matches a known pack,
    # so the prep list works from day one. Custom packs stay empty (flagged in the UI).
    for s in conn.execute("SELECT id, plan_name FROM subscriptions "
                          "WHERE status IN ('Active','Ending Soon','Paused')").fetchall():
        if conn.execute("SELECT 1 FROM subscription_components WHERE subscription_id=?",
                        (s["id"],)).fetchone():
            continue
        plan = match_plan(conn, s["plan_name"])
        if plan and plan["name"] != "Custom Meal Pack":
            copy_recipe_to_subscription(conn, plan["id"], s["id"])
    db.set_setting(conn, "menu_seeded_v1", "1")
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Catalog / recipe helpers
# ---------------------------------------------------------------------------
def all_components(conn, active_only=True):
    q = "SELECT * FROM components"
    if active_only:
        q += " WHERE active=1"
    return conn.execute(q + " ORDER BY sort, name").fetchall()


def plan_recipe(conn, plan_id):
    return conn.execute(
        "SELECT pc.*, c.name, c.unit, c.step, c.emoji, c.protein FROM plan_components pc "
        "JOIN components c ON c.id=pc.component_id WHERE pc.plan_id=? ORDER BY c.sort",
        (plan_id,)).fetchall()


def match_plan(conn, plan_name):
    """Find the catalog plan a subscription's free-text plan_name refers to.
    Exact match first, then 'name contains plan name' (handles e.g.
    'Elite Meal Pack + 100g Chicken', 'Protein Salad Box * 2 qty')."""
    if not plan_name:
        return None
    row = conn.execute("SELECT * FROM plans WHERE name=? COLLATE NOCASE", (plan_name,)).fetchone()
    if row:
        return row
    best = None
    for p in conn.execute("SELECT * FROM plans ORDER BY LENGTH(name) DESC").fetchall():
        if p["name"].lower() in plan_name.lower():
            best = p
            break
    return best


def copy_recipe_to_subscription(conn, plan_id, sub_id):
    conn.execute("DELETE FROM subscription_components WHERE subscription_id=? AND kind='base'",
                 (sub_id,))
    for r in plan_recipe(conn, plan_id):
        conn.execute(
            "INSERT INTO subscription_components(subscription_id,component_id,qty,kind,note) "
            "VALUES(?,?,?,?,?)", (sub_id, r["component_id"], r["qty"], "base", r["note"]))


def sub_composition(conn, sub_id):
    return conn.execute(
        "SELECT sc.*, c.name, c.unit, c.step, c.emoji, c.protein, c.addon_price "
        "FROM subscription_components sc JOIN components c ON c.id=sc.component_id "
        "WHERE sc.subscription_id=? ORDER BY sc.kind, c.sort", (sub_id,)).fetchall()


def save_sub_composition(conn, sub_id, base_items, addon_items):
    """Replace a subscription's composition. Items = [(component_id, qty, note)]."""
    conn.execute("DELETE FROM subscription_components WHERE subscription_id=?", (sub_id,))
    for cid, qty, note in base_items:
        conn.execute("INSERT INTO subscription_components(subscription_id,component_id,qty,kind,note) "
                     "VALUES(?,?,?,?,?)", (sub_id, cid, qty, "base", note or None))
    for cid, qty, note in addon_items:
        conn.execute("INSERT INTO subscription_components(subscription_id,component_id,qty,kind,note) "
                     "VALUES(?,?,?,?,?)", (sub_id, cid, qty, "addon", note or None))
    conn.commit()


# ---------------------------------------------------------------------------
# Component rules ("paneer instead of chicken on Tue/Fri")
# ---------------------------------------------------------------------------
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def rules_for_sub(conn, sub_id, active_only=True):
    q = ("SELECT r.*, cf.name AS from_name, cf.emoji AS from_emoji, "
         "ct.name AS to_name, ct.emoji AS to_emoji, ct.unit AS to_unit "
         "FROM component_rules r "
         "LEFT JOIN components cf ON cf.id=r.from_component_id "
         "LEFT JOIN components ct ON ct.id=r.to_component_id "
         "WHERE r.subscription_id=?")
    if active_only:
        q += " AND r.active=1"
    return conn.execute(q + " ORDER BY r.id", (sub_id,)).fetchall()


def rule_applies(rule, day_iso):
    d = datetime.strptime(day_iso, "%Y-%m-%d").date()
    if rule["start_date"] and day_iso < rule["start_date"]:
        return False
    if rule["end_date"] and day_iso > rule["end_date"]:
        return False
    if rule["weekdays"]:
        days = {int(x) for x in str(rule["weekdays"]).split(",") if x.strip() != ""}
        if d.weekday() not in days:
            return False
    return True


def rule_label(rule):
    """Human sentence for a rule, e.g. 'Tue, Fri: Paneer 100g instead of Chicken'."""
    when = "Every day"
    if rule["weekdays"]:
        names = [WEEKDAY_NAMES[int(x)] for x in str(rule["weekdays"]).split(",") if x.strip() != ""]
        when = ", ".join(names)
    qty = fmt_qty(rule["qty"], rule["to_unit"]) if rule["qty"] and rule["to_name"] else ""
    if rule["action"] == "swap":
        what = f"{rule['to_name']} {qty} instead of {rule['from_name']}"
    elif rule["action"] == "add":
        what = f"add {rule['to_name']} {qty}"
    else:
        what = f"no {rule['from_name']}"
    return f"{when}: {what}".replace("  ", " ").strip()


def apply_rules(items, rules, day_iso):
    """Apply the active rules for one day to a composition list of dicts
    [{component_id, name, unit, step, emoji, qty, note}]. Returns a new list."""
    out = [dict(i) for i in items]
    for r in rules:
        if not rule_applies(r, day_iso):
            continue
        if r["action"] in ("swap", "remove") and r["from_component_id"]:
            out = [i for i in out if i["component_id"] != r["from_component_id"]]
        if r["action"] in ("swap", "add") and r["to_component_id"]:
            out.append({"component_id": r["to_component_id"], "name": r["to_name"],
                        "unit": r["to_unit"] or "serving", "step": 1,
                        "emoji": r["to_emoji"], "qty": r["qty"] or 1, "note": r["note"]})
    return out


# ---------------------------------------------------------------------------
# The PREP LIST
# ---------------------------------------------------------------------------
def fmt_qty(qty, unit):
    if qty is None:
        return ""
    n = int(qty) if float(qty) == int(qty) else qty
    if unit == "g":
        return f"{n}g"
    if unit == "pcs":
        return f"{n} pc{'s' if n != 1 else ''}"
    return f"{n} serving{'s' if n != 1 else ''}"


def _composition_items(conn, sub_id, cache):
    """[{component_id,name,unit,step,emoji,qty,note}] for a sub (cached per request)."""
    if sub_id in cache:
        return cache[sub_id]
    items = [{"component_id": r["component_id"], "name": r["name"], "unit": r["unit"],
              "step": r["step"], "emoji": r["emoji"], "qty": r["qty"], "note": r["note"]}
             for r in sub_composition(conn, sub_id)]
    cache[sub_id] = items
    return items


def _agg_bucket():
    return {"total": 0.0, "sizes": defaultdict(int), "notes": defaultdict(float)}


def _aggregate(agg, items, meta):
    """Fold one order's items into the per-component aggregate for its slot."""
    for it in items:
        key = (it["name"], it["unit"], it["emoji"] or "")
        b = agg[key]
        qty = float(it["qty"] or 0)
        if it["unit"] == "g":
            b["total"] += qty
            b["sizes"][int(qty)] += 1
        else:
            b["total"] += qty
            if qty != 1:
                b["sizes"][qty] += 1
        note = (it.get("note") or "").strip()
        if note:
            b["notes"][note.lower()] += qty


def _finish(agg):
    """Turn the raw aggregate into sorted display rows."""
    rows = []
    for (name, unit, emoji), b in agg.items():
        total = b["total"]
        if unit == "g":
            breakdown = " · ".join(f"{size}g × {n}" for size, n in sorted(b["sizes"].items()))
            display = f"{int(total)}g"
        else:
            breakdown = ""
            display = fmt_qty(total, unit)
        notes = ", ".join(f"{fmt_qty(q, unit)} {n}" for n, q in sorted(b["notes"].items()))
        rows.append({"name": name, "unit": unit, "emoji": emoji, "total": total,
                     "display": display, "breakdown": breakdown, "notes": notes})
    order = {c: i for i, c in enumerate(
        ["Chicken", "Paneer", "Fish Fillet (Basa)", "Egg"])}
    rows.sort(key=lambda r: (order.get(r["name"], 50), -r["total"]))
    return rows


def audit_subscription(conn, sub):
    """Everything questionable about one subscription's composition.

    Written because the pack structure changed and the old data never caught up:
    extras were recorded by typing them into the *plan name* ("Elite Meal Pack +
    100g Chicken") instead of as add-on components. The prep list can only count
    what is in `subscription_components`, so those extras are silently missing
    from the kitchen quantities every single day.

    Returns [{level, code, msg, fix}] — `level` is 'high' when tonight's cooking
    is actually wrong, 'low' when it is only untidy.
    """
    out = []
    comps = sub_composition(conn, sub["id"])
    base = [x for x in comps if x["kind"] != "addon"]
    addons = [x for x in comps if x["kind"] == "addon"]
    name = (sub["plan_name"] or "").strip()
    plan = match_plan(conn, name)

    if not comps:
        out.append({"level": "high", "code": "no_composition",
                    "msg": "No composition saved — this meal is missing from the prep totals entirely.",
                    "fix": "apply_pack" if plan else None})

    # "+ 100g Chicken" typed into the plan name, with nothing recorded to match.
    if "+" in name and not addons:
        extra = name.split("+", 1)[1].strip()
        out.append({"level": "high", "code": "addon_not_modelled",
                    "msg": f"Plan name says “+ {extra}”, but no add-on is recorded. "
                           f"The kitchen is not being told to add it.",
                    "fix": None})

    # Base differs from the pack it claims to be.
    if plan and base:
        recipe = plan_recipe(conn, plan["id"])
        rec = {(r["component_id"], float(r["qty"])) for r in recipe}
        got = {(b["component_id"], float(b["qty"])) for b in base}
        if rec and rec != got:
            missing = rec - got
            extra_i = got - rec
            bits = []
            if missing:
                bits.append(f"{len(missing)} item(s) in the pack but not here")
            if extra_i:
                bits.append(f"{len(extra_i)} item(s) here but not in the pack")
            out.append({"level": "low", "code": "differs_from_pack",
                        "msg": f"Base differs from the {plan['name']} recipe — " + ", ".join(bits) +
                               ". Fine if deliberate.",
                        "fix": "apply_pack"})

    # Notes that read like a composition change nobody encoded.
    note = (sub["notes"] or "").lower()
    triggers = ["instead of", "without", "no ", "extra", "less", "double", "swap", "add "]
    if note and any(t in note for t in triggers) and not rules_for_sub(conn, sub["id"]):
        out.append({"level": "low", "code": "note_may_hide_change",
                    "msg": "The note describes a change to the meal, but no swap rule or "
                           "add-on is recorded — the kitchen only sees it as free text.",
                    "fix": None})

    # Price sanity: what the pack plus recorded add-ons would come to.
    if plan and plan["price_monthly"]:
        calc = float(plan["price_monthly"])
        for a in addons:
            if a["addon_price"] and a["step"]:
                calc += float(a["addon_price"]) * (float(a["qty"]) / float(a["step"]))
        paid = float(sub["subscription_price"] or 0)
        if paid and abs(paid - calc) >= 1:
            out.append({"level": "info", "code": "price_differs",
                        "msg": f"Price ₹{paid:,.0f} vs ₹{calc:,.0f} for this pack plus its "
                               f"recorded add-ons. Only a problem if unintended.",
                        "fix": None})
    return out


def audit_all(conn, live_only=True):
    """Audit every subscription, worst first."""
    q = "SELECT * FROM subscriptions"
    if live_only:
        q += " WHERE status IN ('Active','Ending Soon')"
    rows = []
    for s in conn.execute(q + " ORDER BY slot, customer_name"):
        issues = audit_subscription(conn, s)
        rows.append({"sub": s, "issues": issues,
                     "comps": sub_composition(conn, s["id"]),
                     "high": sum(1 for i in issues if i["level"] == "high")})
    rows.sort(key=lambda r: (-r["high"], -len(r["issues"]), r["sub"]["customer_name"] or ""))
    return rows


def order_items(conn, r, day_iso, cache=None, plan_cache=None):
    """What one order actually contains: ([items], label).

    Resolves a subscription's saved composition (with any weekday swap/add/remove
    rules applied for this date) or, for a trial, the recipe of the pack it maps to.
    Returns ([], label) when the order can't be decomposed — the caller must still
    show it, just without quantities.

    Shared by the kitchen prep aggregate and the printed rider sheet so the two can
    never disagree about what goes in a box.
    """
    cache = {} if cache is None else cache
    plan_cache = {} if plan_cache is None else plan_cache
    items, label = [], None
    if r["subscription_id"]:
        items = _composition_items(conn, r["subscription_id"], cache)
        rules = rules_for_sub(conn, r["subscription_id"])
        if rules:
            items = apply_rules(items, rules, day_iso)
        label = r["plan_name"] or "Meal"
    elif r["trial_id"]:
        t = conn.execute("SELECT * FROM trial_orders WHERE id=?", (r["trial_id"],)).fetchone()
        label = "TRIAL · " + ((t["order_text"] if t else None) or "Trial meal")
        pid = t["plan_id"] if t else None
        if not pid and t:
            p = match_plan(conn, t["order_text"])
            pid = p["id"] if p else None
        if pid:
            if pid not in plan_cache:
                plan_cache[pid] = [
                    {"component_id": x["component_id"], "name": x["name"], "unit": x["unit"],
                     "step": x["step"], "emoji": x["emoji"], "qty": x["qty"], "note": x["note"]}
                    for x in plan_recipe(conn, pid)]
            items = plan_cache[pid]
    return items, label


def prep_for_day(conn, day_iso, rows=None):
    """The full kitchen prep for one day.

    Returns {
      lunch:  {components: [...], packs: [...], meals: N},
      dinner: {components: [...], packs: [...], meals: N},
      total:  {components: [...], meals: N},
      unmapped: [{name, label}],       # orders we can't decompose yet
      day: day_iso,
    }
    Counts every order that must be COOKED (Pending or Delivered; Skipped excluded).
    """
    import core
    if rows is None:
        rows = core.deliveries_for_day(conn, day_iso)
    cache, plan_cache = {}, {}
    agg = {"Lunch": defaultdict(_agg_bucket), "Dinner": defaultdict(_agg_bucket),
           "All": defaultdict(_agg_bucket)}
    packs = {"Lunch": defaultdict(list), "Dinner": defaultdict(list)}
    meals = {"Lunch": 0, "Dinner": 0}
    unmapped = []

    for r in rows:
        if r["status"] not in ("Pending", "Delivered"):
            continue
        slot = "Dinner" if (r["meal_type"] or "Lunch") == "Dinner" else "Lunch"
        items, label = order_items(conn, r, day_iso, cache, plan_cache)
        if items:
            _aggregate(agg[slot], items, r)
            _aggregate(agg["All"], items, r)
        else:
            unmapped.append({"name": r["name"], "label": label or r["notes"] or "Meal",
                             "slot": slot,
                             "sub_id": r["subscription_id"], "trial_id": r["trial_id"]})
        meals[slot] += 1
        detail = " + ".join(f"{i['name']} {fmt_qty(i['qty'], i['unit'])}" for i in items)
        packs[slot][label or "Meal"].append({"name": r["name"], "detail": detail,
                                             "note": r["day_note"]})

    def pack_rows(slot):
        out = []
        for lbl, orders in packs[slot].items():
            details = {o["detail"] for o in orders}
            out.append({"label": lbl, "n": len(orders), "orders": orders,
                        "detail": details.pop() if len(details) == 1 else None})
        out.sort(key=lambda p: -p["n"])
        return out

    return {
        "day": day_iso,
        "lunch": {"components": _finish(agg["Lunch"]), "packs": pack_rows("Lunch"),
                  "meals": meals["Lunch"]},
        "dinner": {"components": _finish(agg["Dinner"]), "packs": pack_rows("Dinner"),
                   "meals": meals["Dinner"]},
        "total": {"components": _finish(agg["All"]), "meals": meals["Lunch"] + meals["Dinner"]},
        "unmapped": unmapped,
    }


# ---------------------------------------------------------------------------
# Backlog: past orders never marked Delivered/Skipped
# ---------------------------------------------------------------------------
def backlog(conn, before_iso):
    """Every order from BEFORE `before_iso` still sitting as Pending — i.e. days
    you forgot to tick. Grouped newest date first so they're quick to clear."""
    rows = conn.execute(
        "SELECT d.*, COALESCE(s.plan_name, d.notes) AS plan_label "
        "FROM deliveries d LEFT JOIN subscriptions s ON s.id=d.subscription_id "
        "WHERE d.status='Pending' AND d.date < ? "
        "ORDER BY d.date DESC, (d.meal_type='Dinner'), d.name", (before_iso,)).fetchall()
    groups = []
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)
    for day in sorted(by_date.keys(), reverse=True):
        groups.append({"date": day, "rows": by_date[day]})
    return {"count": len(rows), "groups": groups}
