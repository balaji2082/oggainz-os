# -*- coding: utf-8 -*-
"""Checks on the money model. Run it after touching model.py:

    python test_model.py

Uses made-up rows, so it needs no Notion and no network. The point is the
arithmetic: if cash out stops equalling the sum of its parts, or if CapEx starts
leaking into operating profit, a number on the dashboard has become a lie and
this fails loudly.
"""
import sys

import model as M


FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def eq(name, got, want, tol=0.5):
    check(name, abs(got - want) <= tol, "got %.2f, want %.2f" % (got, want))


def row(**kw):
    r = {"id": kw.get("id"), "db": "business", "date": kw["date"],
         "month": M.month_of(kw["date"]), "what": kw.get("what", "x"),
         "amount": kw["amount"], "category": kw.get("category"),
         "cost_type": None, "subcategory": kw.get("sub"),
         "vendor": kw.get("vendor"), "spread": kw.get("spread"),
         "paid_from": kw.get("paid_from"), "notes": kw.get("notes"),
         "orders_covered": kw.get("orders")}
    return r


ROWS = [
    row(date="2026-08-03", what="Chicken 12kg", amount=4000,
        category="COGS · Protein", paid_from="Personal bank"),
    row(date="2026-08-15", what="Custom logo tape", amount=5000,
        category="COGS · Packaging", paid_from="Personal bank"),      # spreads 10
    row(date="2026-08-28", what="Faber OTG", amount=11500,
        category="CAPEX · Kitchen Equipment", paid_from="Appa"),      # never in P&L
    row(date="2026-08-03", what="Ather scooter EMI", amount=4645,
        category="OPEX · Vehicle & EMI", paid_from="Personal bank"),  # financing
    row(date="2026-08-24", what="Claude subscription", amount=1999,
        category="OPEX · Software & Subscriptions", paid_from="Business bank"),
    row(date="2026-08-05", what="Porter run", amount=100,
        category="COGS · Delivery", vendor="Some Rider", orders=1,
        paid_from="Personal bank"),
    row(date="2026-08-06", what="Petrol", amount=300,
        category="COGS · Delivery", vendor="Petrol bunk", paid_from="Personal bank"),
    row(date="2026-08-07", what="Delivery charge", amount=120,
        category="COGS · Delivery", vendor="Hyperpure", paid_from="Business bank"),
    row(date="2026-08-09", what="Onion", amount=50,
        category="COGS · Produce"),                                   # no payer
]
REV = {"recognised_revenue": 50000.0, "delivery_revenue": 5000.0,
       "trial_revenue": 2000.0, "total_revenue": 80000.0,
       "received": 55000.0, "outstanding": 25000.0, "delivered": 200}

print("\nmodel.py checks")
print("-" * 52)

M.enrich(ROWS)
s = M.month_summary(ROWS, "Aug-2026", REV)

print("\n buckets")
by = {r["what"]: r["bucket"] for r in ROWS}
check("EMI is FINANCING, not OPEX", by["Ather scooter EMI"] == M.FINANCING)
check("oven is CAPEX", by["Faber OTG"] == M.CAPEX)
check("subscription stays OPEX", by["Claude subscription"] == M.OPEX)
check("chicken is COGS", by["Chicken 12kg"] == M.COGS)

print("\n delivery legs")
leg = {r["what"]: r["leg"] for r in ROWS}
check("Porter run is outbound", leg["Porter run"] == "outbound")
check("own petrol is outbound", leg["Petrol"] == "outbound")
check("Hyperpure freight is INBOUND", leg["Delivery charge"] == "inbound")
check("chicken has no leg", leg["Chicken 12kg"] is None)

print("\n spreading")
sp = {r["what"]: r["spread_months"] for r in ROWS}
eq("tape spreads over 10 months", sp["Custom logo tape"], 10)
eq("oven does NOT spread (CapEx)", sp["Faber OTG"], 1)
eq("chicken does not spread", sp["Chicken 12kg"], 1)
eq("tape costs 500/month", [r for r in ROWS if r["what"] == "Custom logo tape"][0]["monthly_cost"], 500)

print("\n cash vs cost")
eq("cash out = every rupee logged", s["cash_out"], 27714)
eq("CapEx counted as invested", s["capex_cash"], 11500)
eq("operating cost excludes CapEx and EMI", s["operating_cost"], 27714 - 11500 - 4645 - 4500)
check("CapEx never reaches the P&L", M.CAPEX not in s["operating"])
eq("EMI kept out of OPEX", s["opex"], 1999)

print("\n reconciliation")
r = s["reconcile"]
check("cash -> cost identity balances", r["balances"])
eq("bucket sum equals cash out", sum(s["cash"].values()), s["cash_out"])
eq("payer sum equals cash out", sum(s["by_payer"].values()), s["cash_out"])
eq("prepaid = tape held back", s["prepaid_out"], 4500)

print("\n payers")
eq("out of pocket", s["out_of_pocket"], 4000 + 5000 + 4645 + 100 + 300)
eq("family fronted", s["fronted_by_family"], 11500)
eq("business bank", s["from_business_bank"], 1999 + 120)
eq("unattributed", s["payer_unknown"], 50)

print("\n delivery economics")
d = s["delivery"]
eq("outbound = porter + own fuel", d["outbound"], 400)
eq("inbound freight kept separate", d["inbound"], 120)
eq("porter cost per run", d["per_porter_run"], 100)
eq("outbound per meal", d["per_meal"], 2.0)

print("\n profit")
eq("income = earned + fees + trials", s["income"], 57000)
eq("gross profit", s["gross_profit"], 57000 - s["cogs"])
eq("operating profit", s["operating_profit"], 57000 - s["cogs"] - s["opex"])
eq("net after EMI", s["net_after_financing"], 57000 - s["cogs"] - s["opex"] - 4645)
check("operating profit beats net after EMI",
      s["operating_profit"] > s["net_after_financing"])

print("\n later months carry the spread")
s2 = M.month_summary(ROWS, "Sep-2026", None)
eq("September carries one tape slice", s2["carried_in"], 500)
eq("no cash out in September", s2["cash_out"], 0)
check("September reconciles too", s2["reconcile"]["balances"])

print("\n appa merge")
dad = [
    {"id": "d1", "date": "2026-08-09", "month": "Aug-2026", "what": "Onion",
     "amount": 50, "category": "Groceries", "to_business": True},      # mirrors a biz row
    {"id": "d2", "date": "2026-08-20", "month": "Aug-2026", "what": "Egg tray",
     "amount": 190, "category": "Groceries", "to_business": True},     # only on his sheet
    {"id": "d3", "date": "2026-08-20", "month": "Aug-2026", "what": "Beer",
     "amount": 500, "category": "Miscellaneous", "to_business": False},  # household
]
extra, mirrored = M.merge_appa(ROWS, dad)
eq("one row folded in", len(extra), 1)
eq("folded amount", sum(x["amount"] for x in extra), 190)
eq("one row matched as mirrored", len(mirrored), 1)
check("mirrored row is the onion", mirrored[0]["id"] == "d1")
check("household row ignored", all(x["id"] != "d3" for x in extra))
check("matched biz row credited to Appa",
      [x for x in ROWS if x["what"] == "Onion"][0]["payer"] == M.PAYER_APPA)

s3 = M.month_summary(ROWS + extra, "Aug-2026", REV)
eq("folded row raises cash out once", s3["cash_out"], 27714 + 190)

print("\n health")
# The onion row had no payer but matched appa's sheet, so the merge attributed it
# to him on evidence. A genuinely unattributable row is a different case.
orphan = row(date="2026-08-11", what="Vegetables", amount=290,
             category="COGS · Produce")
M.enrich([orphan])
h = M.health(ROWS + [orphan], dad, extra, mirrored, "Aug-2026")
keys = {i["key"] for i in h["issues"]}
check("flags a row nothing can attribute", "payer" in keys)
no_payer = next((i for i in h["issues"] if i["key"] == "payer"), None)
check("flags only the orphan, not the row appa's sheet explains",
      no_payer is not None and no_payer["n"] == 1)
check("records the inferred payer", h["inferred_payers"] == 1)
check("flags appa-only spend", "appa-only" in keys)
check("confidence is a real number", 0 <= (h["confidence"] or -1) <= 100)

print("-" * 52)
if FAILS:
    print("FAILED: %d check(s) -> %s\n" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("all checks passed\n")
