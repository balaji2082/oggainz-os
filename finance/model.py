# -*- coding: utf-8 -*-
"""OG Gainz money model - the one place that decides what a rupee MEANS.

Three different questions get asked of the same expense row and they have three
different answers. Keeping them apart is the whole point of this file:

  1. CASH OUT      what actually left a pocket this month (bank reality)
  2. OPERATING     what running the kitchen this month really cost (P&L reality)
  3. INVESTED      what was bought once and still owned afterwards (not a cost)

A 11,500 oven is 11,500 of cash out, 0 of operating cost, 11,500 invested.
5,200 of logo tape that lasts ten months is 5,200 cash out but only 520 of
operating cost this month. Mixing these is why the August P&L looked like the
business was burning 82% of revenue when it was really spending 59%.

Nothing here talks to Notion or draws HTML - it takes shaped rows in and gives
numbers out, so it can be checked with a calculator.
"""

from collections import defaultdict

# --------------------------------------------------------------- cost buckets
#
# Cost Type in Notion has four values. We derive a fifth, FINANCING, because an
# EMI is neither an operating cost nor an investment - see note below.

COGS = "COGS"            # scales with meals sold
OPEX = "OPEX"            # flat monthly running cost
FINANCING = "FINANCING"  # loan repayment: real cash, its own line
CAPEX = "CAPEX"          # bought once, still owned - NOT an operating cost
DRAW = "DRAW"            # money taken out of the business, never an expense

BUCKET_ORDER = [COGS, OPEX, FINANCING, CAPEX, DRAW]

# Rows whose Category matches these are pulled out of OPEX into FINANCING.
# An EMI is a loan repayment, not a cost of running the kitchen: most of it is
# principal (buying the asset a slice at a time) and only the interest part is a
# true expense. Booking it as CAPEX every month would claim you bought the
# scooter over and over. So it gets its own line - visible, never hidden, but
# not muddying "what did cooking and delivering actually cost".
FINANCING_CATEGORIES = {"OPEX · Vehicle & EMI"}
FINANCING_WORDS = ("emi", "loan repayment", "instalment", "installment")

# ------------------------------------------------------------- amortisation
#
# Balaji's rule, and it is the right one: consumable stock bought in bulk gets
# spread over the months it lasts. Capital assets do NOT get spread into the
# monthly operating number - a freezer that lasts eight years has no meaningful
# "monthly cost" for a kitchen this size. CapEx sits below the line as
# investment, with a depreciation figure shown only as a reference footnote.

# category -> months of use, applied when no explicit Spread is set on the row.
# Only P&L categories belong here. Spreading a CapEx row would be pointless work:
# CapEx never enters the P&L, so the slices would have nowhere to land and would
# only unbalance the cash-becomes-cost reconciliation. Printed stock that is
# sitting in a CapEx category is flagged by health() to be moved, not silently
# re-bucketed here - guessing at his classification would hide the mistake.
CONSUMABLE_LIFE = {
    "COGS · Packaging": 10,            # printed tape, trays, sticker rolls
    "COGS · Cooking Essentials": 3,    # bulk oil, spice packs
}

# Below this, don't bother spreading - the noise costs more than the accuracy.
# Above it the phone skill is meant to ASK how long the thing will last.
SPREAD_MIN_AMOUNT = 2000

# Reference-only asset lives, used for the depreciation footnote. Never
# subtracted from operating profit.
CAPEX_LIFE = {
    "CAPEX · Kitchen Equipment": 60,   # 5 years
}
CAPEX_LIFE_DEFAULT = 36

# ------------------------------------------------- the unlogged-spend detector
#
# What one meal SHOULD cost, from the zero-based costing in One-Meal-Plan/
# (Signature 92.5 / Elite 116.5 / Athlete 146, blended), plus packaging.
#
# This is the only way to see money that was never logged at all. Logged spend
# can be audited; missing spend is invisible by definition - except that meals
# still went out of the kitchen, and those meals had to be made of something. If
# logged ingredient cost per meal comes in well under what the recipes say a meal
# costs, the difference is either unlogged buying or cheaper sourcing, and he is
# the only one who can say which. So this reports, it never silently adjusts.
MODEL_GATE_PER_MEAL = 112.0
MODEL_PACKAGING_PER_MEAL = 19.0

# Below this gap, say nothing - plan mix and market prices move enough that a
# small difference means nothing.
COST_GAP_TOLERANCE = 0.12

# ---------------------------------------------------------------- delivery
#
# COGS - Delivery was doing three unrelated jobs at once. Splitting them is the
# only way "cost per delivery" means anything.
#
#   OUTBOUND  getting a meal to a customer - Porter runs, own-bike petrol
#   INBOUND   getting ingredients to the kitchen - Hyperpure freight, Instamart
#             handling, Blinkit fees. This is landed ingredient cost, not
#             delivery cost, and must never be divided by orders delivered.

DELIVERY_CATEGORY = "COGS · Delivery"

INBOUND_VENDORS = {
    "hyperpure", "swiggy instamart", "instamart", "blinkit", "zepto",
    "milkbasket", "bigbasket", "dunzo", "jiomart", "amazon", "flipkart",
}
INBOUND_WORDS = (
    "handling", "platform fee", "membership", "delivery charge", "delivery fee",
    "convenience fee", "packing charge", "freight",
)
OUTBOUND_WORDS = ("porter", "petrol", "fuel", "rapido", "borzo", "delivery run")

# ------------------------------------------------------------------- payers

PAYER_ME = "Me (personal)"
PAYER_BUSINESS = "Business bank"
PAYER_APPA = "Appa"
PAYER_ABISHEK = "Abishek"
PAYER_JOINT = "Appa / Abishek"
PAYER_UNKNOWN = "Not set"

PAYER_ORDER = [PAYER_ME, PAYER_BUSINESS, PAYER_APPA, PAYER_ABISHEK,
               PAYER_JOINT, PAYER_UNKNOWN]

_PAYER_MAP = {
    "personal bank": PAYER_ME,
    "personal": PAYER_ME,
    "me": PAYER_ME,
    "balaji": PAYER_ME,
    "cash (mine)": PAYER_ME,
    "business bank": PAYER_BUSINESS,
    "business": PAYER_BUSINESS,
    "appa": PAYER_APPA,
    "dad": PAYER_APPA,
    "abishek": PAYER_ABISHEK,
    "abi": PAYER_ABISHEK,
    "appa / abishek": PAYER_JOINT,
}

# Payers whose money Balaji has to pay back or has fronted himself. Everything
# except the business account is somebody's pocket.
OUT_OF_POCKET = {PAYER_ME}
FRONTED_BY_FAMILY = {PAYER_APPA, PAYER_ABISHEK, PAYER_JOINT}


def norm_payer(v):
    if not v:
        return PAYER_UNKNOWN
    return _PAYER_MAP.get(str(v).strip().lower(), str(v).strip())


# ------------------------------------------------------------------ helpers

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _low(*vals):
    return " ".join(str(v or "") for v in vals).lower()


def month_of(iso):
    """'2026-08-14' -> 'Aug-2026'. Formatted from string parts on purpose -
    building a date object here re-introduces the IST off-by-one-day bug."""
    if not iso or len(iso) < 7:
        return None
    try:
        return "%s-%s" % (MONTH_NAMES[int(iso[5:7]) - 1], iso[:4])
    except (ValueError, IndexError):
        return None


def add_months(m, n):
    mon, yr = m.split("-")
    i = MONTH_NAMES.index(mon) + n
    return "%s-%d" % (MONTH_NAMES[i % 12], int(yr) + i // 12)


# ------------------------------------------------------- per-row enrichment

def bucket_for(row):
    """Which of the five buckets this row belongs in."""
    cat = row.get("category") or ""
    ctype = row.get("cost_type") or (cat.split(" · ")[0] if " · " in cat else OPEX)
    if ctype in (OPEX, COGS):
        if cat in FINANCING_CATEGORIES or any(
                w in _low(row.get("what"), row.get("notes")) for w in FINANCING_WORDS):
            return FINANCING
    return ctype if ctype in (COGS, OPEX, CAPEX, DRAW) else OPEX


def delivery_leg(row):
    """'outbound' | 'inbound' | None - only meaningful for delivery rows."""
    if (row.get("category") or "") != DELIVERY_CATEGORY:
        return None
    vendor = (row.get("vendor") or "").strip().lower()
    text = _low(row.get("what"), row.get("notes"))
    if vendor in INBOUND_VENDORS or any(w in text for w in INBOUND_WORDS):
        return "inbound"
    if any(w in text for w in OUTBOUND_WORDS):
        return "outbound"
    # A named human as the vendor is a Porter/rider payout.
    return "outbound" if vendor else "inbound"


def spread_for(row, bucket):
    """How many months this row's cost should be spread across.

    Explicit beats inferred, always. Inference only fires for consumable stock
    over the threshold - capital assets deliberately return 1 here because they
    leave the P&L entirely rather than being smeared across it.
    """
    explicit = row.get("spread")
    try:
        explicit = float(explicit) if explicit else 0
    except (TypeError, ValueError):
        explicit = 0
    if explicit and explicit > 1:
        return explicit, "set on the row"

    cat = row.get("category")
    if bucket == CAPEX and cat not in CONSUMABLE_LIFE:
        return 1.0, None

    life = CONSUMABLE_LIFE.get(cat)
    if life and (row.get("amount") or 0) >= SPREAD_MIN_AMOUNT:
        return float(life), "default for %s" % cat
    return 1.0, None


def enrich(rows):
    """Annotate every business row in place with the model's verdict."""
    for r in rows:
        b = bucket_for(r)
        sp, why = spread_for(r, b)
        r["bucket"] = b
        r["payer"] = norm_payer(r.get("paid_from"))
        r["leg"] = delivery_leg(r)
        r["spread_months"] = sp
        r["spread_why"] = why
        r["monthly_cost"] = round((r.get("amount") or 0) / sp, 2)
        # Does this rupee belong in the operating P&L at all?
        r["in_pnl"] = b in (COGS, OPEX)
        r["needs_life"] = bool(
            b in (COGS, OPEX, CAPEX)
            and (r.get("amount") or 0) >= SPREAD_MIN_AMOUNT
            and sp == 1.0
            and r.get("category") in CONSUMABLE_LIFE
        )
    return rows


# --------------------------------------------------- appa sheet reconciliation

def merge_appa(business, dad):
    """Fold appa's business purchases into the business rows without double-counting.

    Appa buys for the kitchen and the house from one pocket. Some of his kitchen
    buys get mirrored into the business database, some never do. Adding the two
    sheets double-counts the mirrored ones; ignoring his sheet loses the rest.

    Matching on (date, amount) is crude but these are the same transaction typed
    twice, so the pair always agrees on both. Returns (extra_rows, mirrored).
    """
    idx = defaultdict(list)
    for r in business:
        if norm_payer(r.get("paid_from")) in FRONTED_BY_FAMILY or not r.get("paid_from"):
            idx[(r.get("date"), round(float(r.get("amount") or 0), 2))].append(r)

    extra, mirrored = [], []
    for d in dad or []:
        if not d.get("to_business"):
            continue
        amt = float(d.get("amount") or 0)
        if not amt or not d.get("date"):
            continue
        key = (d.get("date"), round(amt, 2))
        hit = next((b for b in idx.get(key, []) if not b.get("_matched_appa")), None)
        if hit:
            hit["_matched_appa"] = True
            # His sheet is proof of who actually paid: it's appa's own log.
            if hit.get("payer") in (PAYER_JOINT, PAYER_UNKNOWN):
                hit["payer"] = PAYER_APPA
                hit["payer_inferred"] = True
            mirrored.append(d)
            continue
        cat = d.get("category") or ""
        extra.append({
            "id": d.get("id"), "db": "dad", "source": "appa-sheet",
            "month": d.get("month") or month_of(d.get("date")),
            "date": d.get("date"), "what": d.get("what"),
            "amount": amt,
            "category": "COGS · Produce" if cat in ("Fruits & Produce", "Groceries")
                        else "COGS · Other",
            "cost_type": COGS, "bucket": COGS,
            "subcategory": cat or None, "vendor": None,
            "payer": PAYER_APPA, "paid_from": "Appa",
            "spread": 1, "spread_months": 1.0, "spread_why": None,
            "monthly_cost": amt, "in_pnl": True,
            "leg": None, "needs_life": False, "payment": None,
            "orders_covered": None,
            # Kept so the edit drawer round-trips this row back to appa's sheet
            # with its flag intact instead of blanking it on save.
            "to_business": True,
            "notes": "Only on Appa's sheet - folded in so the business total is complete.",
        })
    return extra, mirrored


# ------------------------------------------------------------------- rollups

def month_summary(rows, month, revenue=None):
    """Everything the dashboard needs for one month, from one pass over rows.

    `rows` are enriched business rows (including appa extras). `revenue` is the
    matching row from build_monthly(), or None.
    """
    rev = revenue or {}
    cash = defaultdict(float)
    operating = defaultdict(float)
    by_payer = defaultdict(float)
    by_payer_pnl = defaultdict(float)
    by_category = defaultdict(float)
    by_subcategory = defaultdict(float)
    s = {
        "month": month,
        "carried_in": 0.0,     # earlier months' stock used up now
        "prepaid_out": 0.0,    # this month's cash pushed to later months
        "delivery": {"outbound": 0.0, "inbound": 0.0, "porter_runs": 0,
                     "porter_spend": 0.0, "own_spend": 0.0},
        "rows_this_month": 0,
    }

    for r in rows:
        amt = float(r.get("amount") or 0)
        if not amt or not r.get("date"):
            continue
        rm = r.get("month") or month_of(r["date"])
        if not rm:
            continue
        b = r.get("bucket") or OPEX
        sp = r.get("spread_months") or 1.0
        slice_amt = amt / sp

        in_pnl = bool(r.get("in_pnl"))
        if rm == month:
            s["rows_this_month"] += 1
            cash[b] += amt
            by_payer[r.get("payer") or PAYER_UNKNOWN] += amt
            # Only P&L cash can be deferred - CapEx leaves the statement whole.
            if sp > 1 and in_pnl:
                s["prepaid_out"] += amt - slice_amt
            leg = r.get("leg")
            if leg == "outbound":
                s["delivery"]["outbound"] += amt
                if "porter" in _low(r.get("what")):
                    s["delivery"]["porter_runs"] += 1
                    s["delivery"]["porter_spend"] += amt
                else:
                    s["delivery"]["own_spend"] += amt
            elif leg == "inbound":
                s["delivery"]["inbound"] += amt

        # Operating charge - which months does this cost actually belong to?
        if in_pnl or b == FINANCING:
            whole = int(sp)
            rem = sp - whole
            hits = [(add_months(rm, i), slice_amt) for i in range(whole)]
            if rem > 0.01:
                hits.append((add_months(rm, whole), slice_amt * rem))
            for hm, val in hits:
                if hm != month:
                    continue
                operating[b] += val
                by_category[r.get("category") or "Uncategorised"] += val
                by_subcategory[r.get("subcategory") or "—"] += val
                by_payer_pnl[r.get("payer") or PAYER_UNKNOWN] += val
                if rm != month and in_pnl:
                    s["carried_in"] += val

    # Depreciation footnote - reference only, never in operating profit.
    dep = 0.0
    for r in rows:
        rm = r.get("month") or month_of(r.get("date") or "")
        if r.get("bucket") == CAPEX and rm:
            life = CAPEX_LIFE.get(r.get("category"), CAPEX_LIFE_DEFAULT)
            dep += float(r.get("amount") or 0) / life
    s["depreciation"] = dep

    cogs = operating[COGS]
    opex = operating[OPEX]
    fin = operating[FINANCING]
    earned = float(rev.get("recognised_revenue") or 0)
    delivery_rev = float(rev.get("delivery_revenue") or 0)
    trial_rev = float(rev.get("trial_revenue") or 0)
    income = earned + delivery_rev + trial_rev
    meals = int(rev.get("delivered") or 0)
    has_cost = bool(cogs or opex)

    s["cash"] = dict(cash)
    s["operating"] = dict(operating)
    s["by_payer"] = dict(by_payer)
    s["by_payer_pnl"] = dict(by_payer_pnl)
    s["by_category"] = dict(by_category)
    s["by_subcategory"] = dict(by_subcategory)

    s["income"] = income
    s["earned_revenue"] = earned
    s["delivery_revenue"] = delivery_rev
    s["trial_revenue"] = trial_rev
    s["billed_revenue"] = float(rev.get("total_revenue") or 0)
    s["received"] = float(rev.get("received") or 0)
    s["outstanding"] = float(rev.get("outstanding") or 0)
    s["meals"] = meals
    s["cogs"] = cogs
    s["opex"] = opex
    s["financing"] = fin
    s["gross_profit"] = (income - cogs) if (income and cogs) else None
    s["gross_margin"] = ((income - cogs) / income * 100) if (income and cogs) else None
    s["operating_profit"] = (income - cogs - opex) if (income and has_cost) else None
    s["net_after_financing"] = (income - cogs - opex - fin) if (income and has_cost) else None
    s["operating_cost"] = cogs + opex
    s["capex_cash"] = cash.get(CAPEX, 0.0)
    s["draw_cash"] = cash.get(DRAW, 0.0)
    s["cash_out"] = sum(cash.values())
    s["cost_per_meal"] = (cogs / meals) if (meals and cogs) else None
    s["opex_per_meal"] = (opex / meals) if (meals and opex) else None
    s["revenue_per_meal"] = (earned / meals) if (meals and earned) else None
    s["profit_per_meal"] = ((income - cogs - opex) / meals) if (meals and has_cost and income) else None

    d = s["delivery"]
    d["total"] = d["outbound"] + d["inbound"]
    d["per_meal"] = (d["outbound"] / meals) if meals else None
    d["per_porter_run"] = (d["porter_spend"] / d["porter_runs"]) if d["porter_runs"] else None
    # Porter drops carry one order each; everything else went on the scooter.
    d["self_delivered"] = max(0, meals - d["porter_runs"]) if meals else 0
    d["own_per_meal"] = (d["own_spend"] / d["self_delivered"]) if d["self_delivered"] else None
    d["recovered"] = delivery_rev
    d["net"] = delivery_rev - d["outbound"]

    # How cash becomes cost. This must balance to the rupee or a number on the
    # dashboard is lying, so the test asserts it rather than trusting it.
    cash_pnl = cash.get(COGS, 0.0) + cash.get(OPEX, 0.0)
    s["reconcile"] = {
        "cash_out": s["cash_out"],
        "capex": s["capex_cash"],
        "financing": cash.get(FINANCING, 0.0),
        "draw": s["draw_cash"],
        "cash_pnl": cash_pnl,
        "prepaid_out": s["prepaid_out"],
        "carried_in": s["carried_in"],
        "operating_cost": s["operating_cost"],
        "balances": abs((cash_pnl - s["prepaid_out"] + s["carried_in"])
                        - s["operating_cost"]) < 0.5,
    }

    # Does the logged food cost square with the meals that actually went out?
    food = cogs - d["outbound"]
    expected_each = MODEL_GATE_PER_MEAL + MODEL_PACKAGING_PER_MEAL
    s["cost_check"] = None
    if meals and food:
        expected = meals * expected_each
        gap = expected - food
        s["cost_check"] = {
            "meals": meals,
            "logged_per_meal": food / meals,
            "expected_per_meal": expected_each,
            "expected_total": expected,
            "logged_total": food,
            "gap": gap,
            # Positive gap = the kitchen produced more food than the books paid
            # for, which almost always means buying that never got entered.
            "direction": "under" if gap > 0 else "over",
            "material": abs(gap) > expected * COST_GAP_TOLERANCE,
        }

    s["out_of_pocket"] = sum(v for k, v in by_payer.items() if k in OUT_OF_POCKET)
    s["fronted_by_family"] = sum(v for k, v in by_payer.items() if k in FRONTED_BY_FAMILY)
    s["from_business_bank"] = by_payer.get(PAYER_BUSINESS, 0.0)
    s["payer_unknown"] = by_payer.get(PAYER_UNKNOWN, 0.0)
    return s


def all_months(rows):
    out = set()
    for r in rows:
        m = r.get("month") or month_of(r.get("date") or "")
        if m:
            out.add(m)
    return sorted(out, key=lambda m: (int(m.split("-")[1]), MONTH_NAMES.index(m.split("-")[0])))


# --------------------------------------------------------------- data health

def health(business, dad, extras, mirrored, month):
    """Everything wrong with the data, priced.

    Accuracy you can see beats accuracy you assume - August went in at maybe 80%
    and the useful question is which 20% is missing, not a guess at the total.
    """
    issues = []

    def this_month(r):
        return (r.get("month") or month_of(r.get("date") or "")) == month

    biz_m = [r for r in business if this_month(r)]

    def add(key, level, title, rows_, why, fix, with_ids=True):
        if not rows_:
            return
        issues.append({
            "key": key, "level": level, "title": title,
            "n": len(rows_),
            "amount": sum(float(r.get("amount") or 0) for r in rows_),
            "why": why, "fix": fix,
            "ids": [r.get("id") for r in rows_ if r.get("id")] if with_ids else [],
        })

    add("payer", "warn", "Entries with nobody marked as the payer",
        [r for r in biz_m if r.get("payer") == PAYER_UNKNOWN],
        "These can't be counted into what came out of your pocket, so "
        "'out of my pocket' is understated by this much.",
        "Open each and set Paid From.")

    add("unsplit", "info", "Still on the joint “Appa / Abishek” pocket",
        [r for r in biz_m if r.get("payer") == PAYER_JOINT],
        "Appa and Abishek are separate payers now - these rows can't be "
        "attributed to either one.",
        "Tick them in the table and bulk-set Paid From.")

    add("appa-only", "warn", "On Appa's sheet, never mirrored into the business book",
        [r for r in extras if this_month(r)],
        "Real kitchen spend the business total was missing. They are folded "
        "into every number above, but they carry no proper category.",
        "Nothing urgent - or log them into the business sheet next month.",
        with_ids=False)

    add("life", "info", "Bulk buys with no lifespan set",
        [r for r in biz_m if r.get("needs_life")],
        "Anything over %s that lasts months should be spread, or this month "
        "carries a cost it didn't really incur." % SPREAD_MIN_AMOUNT,
        "Set 'Spread over months' on each.")

    add("misfiled", "info", "Printed stock filed as capital investment",
        [r for r in biz_m
         if r.get("category") == "CAPEX · Branding & Setup"
         and any(w in _low(r.get("what")) for w in
                 ("sticker", "tape", "label", "print", "pamphlet", "flyer"))],
        "Stickers and printed tape get used up on boxes - that's packaging "
        "stock, not equipment you still own.",
        "Move to COGS · Packaging and give it a lifespan.")

    add("category", "warn", "Entries with no category",
        [r for r in biz_m if not r.get("category")],
        "Uncategorised spend can't be told apart from ingredients, so every "
        "per-meal number is slightly off.",
        "Set a category on each.")

    add("dadflag", "info", "Appa's entries not marked business or household",
        [r for r in (dad or [])
         if (r.get("month") or month_of(r.get("date") or "")) == month
         and r.get("to_business") is None],
        "Any of these that were for the kitchen are missing from the business "
        "total.",
        "Set 'Goes To Business' on Appa's tab.",
        with_ids=False)

    # Rows whose payer was worked out from appa's own sheet rather than typed in
    # Notion. Not a problem - it is resolved evidence - but worth being able to say.
    inferred = [r for r in biz_m if r.get("payer_inferred")]
    if inferred:
        issues.append({
            "key": "inferred", "level": "info",
            "title": "Payer worked out from Appa's sheet",
            "n": len(inferred),
            "amount": sum(float(r.get("amount") or 0) for r in inferred),
            "why": "These had no payer in the business book, but the same purchase "
                   "is on Appa's own sheet, so he is who paid.",
            "fix": "Nothing to do — set Paid From in Notion only if you want it "
                   "recorded there too.",
            "ids": [r.get("id") for r in inferred if r.get("id")],
        })

    tracked = sum(float(r.get("amount") or 0) for r in biz_m)
    at_risk = sum(i["amount"] for i in issues if i["level"] == "warn")
    return {
        "issues": issues,
        "inferred_payers": len(inferred),
        "mirrored_count": len(mirrored),
        "mirrored_amount": sum(float(r.get("amount") or 0) for r in mirrored),
        "tracked": tracked,
        "at_risk": at_risk,
        "confidence": max(0.0, 100 - (at_risk / tracked * 100)) if tracked else None,
    }
