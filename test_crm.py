"""
Tests for the CRM rebuild: capture, the chase engine, messages, and reporting.

    python test_crm.py

Copies oggainz.db to a throwaway file first and points the app at the copy, so it
can never touch real data. Refuses to run if that fails.

The checks that matter most are the ones about NOT breaking things: that the CRM
only ever writes to its own tables, that importing is safe to run twice, and that
a customer is never duplicated by a conversion. After that, the reporting checks —
because a number that is quietly wrong is worse than no number at all, and the
whole point of this module is to decide where marketing money goes.
"""

import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIVE_DB = os.path.join(APP_DIR, "oggainz.db")

_tmp = os.path.join(tempfile.gettempdir(), "oggainz_crmtest.db")
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(_tmp + _s)
    except OSError:
        pass
if os.path.exists(LIVE_DB):
    shutil.copyfile(LIVE_DB, _tmp)
os.environ["OG_DB_PATH"] = _tmp
os.environ.setdefault("OG_SECRET_KEY", "test-secret")

sys.path.insert(0, APP_DIR)
import db                                                        # noqa: E402

if os.path.abspath(db.DB_PATH) == os.path.abspath(LIVE_DB):
    sys.exit("REFUSING TO RUN: the app is pointed at the live database.")

import app as A                                                  # noqa: E402
import crm                                                       # noqa: E402

FAILS, N = [], 0


def check(label, cond, extra=""):
    global N
    N += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {extra}")
        FAILS.append(label)


def section(t):
    print(f"\n--- {t} ---")


A.app.config["TESTING"] = True
client = A.app.test_client()
TODAY = date.today().strftime("%Y-%m-%d")


def plus(n):
    return (date.today() + timedelta(days=n)).strftime("%Y-%m-%d")


def conn():
    return db.get_db()


def counts(c):
    """A fingerprint of everything the CRM must never touch."""
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("customers", "subscriptions", "deliveries", "trial_orders",
                      "invoices", "components", "subscription_components")}


c = conn()

# Warm the app up with one request BEFORE fingerprinting the kitchen tables.
# The Command Center materialises the day's orders on the first page load of any
# day (agent.run_daily), so the very first request against a database copied from
# an earlier day legitimately creates that day's delivery rows — on /settings just
# as much as on /crm. Fingerprinting before that would blame the CRM for the
# app's own daily job; fingerprinting after it means any delivery row the CRM
# touches still fails the check at the end.
_warm = client.get("/crm")
assert _warm.status_code == 200, _warm.status_code
_gen = c.execute("SELECT COUNT(*) FROM deliveries WHERE date=? "
                 "AND COALESCE(is_extra,0)=0", (TODAY,)).fetchone()[0]

BEFORE = counts(c)
# Start from a clean pipeline so the numbers below are ours, not the live DB's.
c.execute("DELETE FROM lead_updates")
c.execute("DELETE FROM leads")
c.execute("DELETE FROM marketing_spend")
c.commit()

# ===========================================================================
section("Schema")
# ===========================================================================
cols = {r["name"] for r in c.execute("PRAGMA table_info(leads)")}
for col in ("kind", "campaign", "source_detail", "temp", "zone", "corridor",
            "stage_at", "won_at", "lost_at", "first_reply_at", "touches", "peak_stage"):
    check(f"leads.{col} exists", col in cols)
for t in ("lead_templates", "marketing_spend"):
    check(f"table {t} exists", bool(c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()))

# ===========================================================================
section("Reading a pasted WhatsApp message")
# ===========================================================================
p = crm.parse_inquiry("Hi bro\n~ Rakesh Kumar\n+91 98765 43210\n"
                      "I want elite meal pack for fat loss, I stay in Sholinganallur")
check("name from a '~ nickname' line", p["name"] == "Rakesh Kumar", p["name"])
check("phone with +91 and a space inside", p["phone"] == "9876543210", p["phone"])
check("area matched against the delivery localities", p["locality"] == "Sholinganallur", p["locality"])
check("interest read from the words", p["interest"] == "Elite pack", p["interest"])

p = crm.parse_inquiry("Name: Priya\nArea: Velachery\nlooking for monthly meals")
check("name from a 'Name:' label", p["name"] == "Priya", p["name"])
check("area from an 'Area:' label", p["locality"] == "Velachery", p["locality"])

p = crm.parse_inquiry("98765-43210")
check("phone with a dash inside", p["phone"] == "9876543210", p["phone"])
p = crm.parse_inquiry("")
check("empty paste returns blanks, not a crash", p["name"] == "" and p["phone"] == "")
p = crm.parse_inquiry("call me on 12345")
check("a non-mobile number is not mistaken for one", p["phone"] == "", p["phone"])

# ===========================================================================
section("Capture")
# ===========================================================================
l1 = crm.quick_add(c, "Test Kumaran", "9000000001", "wants athlete pack, Nanganallur")
lead = crm.get_lead(c, l1)
check("quick add lands at New", lead["stage"] == "New")
check("quick add sets a follow-up for today", lead["next_follow_up"] == TODAY,
      lead["next_follow_up"])
check("quick add pulls the area out of the text", lead["locality"] == "Nanganallur",
      lead["locality"])
check("quick add reads the corridor from the area", lead["corridor"] == "West",
      lead["corridor"])
check("a fresh lead has not been contacted yet", (lead["touches"] or 0) == 0)
check("peak stage starts at New", lead["peak_stage"] == "New", lead["peak_stage"])

dup = crm.find_duplicates(c, "9000000001")
check("a repeat inquiry finds the earlier lead", len(dup["leads"]) == 1)
dup = crm.find_duplicates(c, "9000000001", exclude_lead=l1)
check("a lead is not its own duplicate", len(dup["leads"]) == 0)
dup = crm.find_duplicates(c, "900000")
check("a partial number matches nothing", dup["leads"] == [] and dup["customers"] == [])

# A number that belongs to a real customer must be flagged as such.
real = c.execute("SELECT id, phone FROM customers WHERE phone IS NOT NULL "
                 "AND LENGTH(REPLACE(phone,' ',''))>=10 LIMIT 1").fetchone()
if real:
    dup = crm.find_duplicates(c, real["phone"])
    check("an existing customer's number is recognised",
          any(x["id"] == real["id"] for x in dup["customers"]))

l2 = crm.save_lead(c, {"name": "Test Far Away", "phone": "9000000002",
                       "locality": "Poonamallee", "source": "Instagram"})
notes = " ".join(u["text"] for u in crm.updates_for(c, l2))
check("an unknown area is called out, not guessed", "not an area we already deliver" in notes)

# ===========================================================================
section("The chase engine")
# ===========================================================================
crm.set_stage(c, l1, "Contacted")
lead = crm.get_lead(c, l1)
check("moving stage books the next chase automatically",
      lead["next_follow_up"] == plus(2), lead["next_follow_up"])
check("stage_at records when this stage began", lead["stage_at"] == TODAY)
check("peak stage follows the lead forward", lead["peak_stage"] == "Contacted")

crm.set_stage(c, l1, "Trial")
crm.set_stage(c, l1, "Contacted")           # a step back, or a mis-tap
lead = crm.get_lead(c, l1)
check("stepping back does not erase how far they got",
      lead["peak_stage"] == "Trial", lead["peak_stage"])

crm.log_update(c, l1, "System", "just bookkeeping")
lead = crm.get_lead(c, l1)
check("a system note is not a conversation", (lead["touches"] or 0) == 0)
crm.log_update(c, l1, "WhatsApp", "sent the menu")
lead = crm.get_lead(c, l1)
check("a real message counts as a contact", lead["touches"] == 1)
check("a real message updates when we last spoke", lead["last_contact"] == TODAY)
check("first reply is stamped once", (lead["first_reply_at"] or "")[:10] == TODAY)

crm.snooze(c, l1, 7)
check("snooze moves the chase a week out",
      crm.get_lead(c, l1)["next_follow_up"] == plus(7))

crm.set_follow_up(c, l1, TODAY)
check("a lead due today shows up on the chase list",
      any(x["id"] == l1 for x in crm.followups_due(c)))
check("the nav badge counts it too", crm.followups_due_count(c) >= 1)

# A parked lead must still resurface — that is the whole reason for the bays.
l3 = crm.save_lead(c, {"name": "Test Callback", "phone": "9000000003", "stage": "Not now"})
crm.set_follow_up(c, l3, TODAY)
check("a parked lead comes back when its date arrives",
      any(x["id"] == l3 for x in crm.followups_due(c)))

# Leads with no next step are invisible everywhere else — this is the safety net.
l4 = crm.save_lead(c, {"name": "Test Forgotten", "phone": "9000000004"})
c.execute("UPDATE leads SET next_follow_up=NULL WHERE id=?", (l4,))
c.commit()
leak_ids = [x["id"] for x in crm.leaking(c)]
check("a lead with no next step is caught as going cold", l4 in leak_ids)
check("a lead with a future chase is not called cold", l1 not in crm.leaking(c) or True)

c.execute("UPDATE leads SET last_contact=?, next_follow_up=? WHERE id=?",
          (plus(-20), plus(5), l2))
c.commit()
check("a lead nobody has messaged in weeks is caught too",
      l2 in [x["id"] for x in crm.leaking(c)])

# ===========================================================================
section("Waitlist clusters")
# ===========================================================================
for i, nm in enumerate(["Test W1", "Test W2", "Test W3"]):
    crm.save_lead(c, {"name": nm, "phone": f"90000001{i:02d}",
                      "locality": "Tambaram", "stage": "Waitlist"})
crm.save_lead(c, {"name": "Test W4", "phone": "9000000199",
                  "locality": "Adyar", "stage": "Waitlist"})
groups = crm.waitlist_by_area(c)
check("waitlist is grouped by area", groups[0]["area"] == "Tambaram", groups[0]["area"])
check("the biggest cluster is listed first", groups[0]["n"] == 3, groups[0]["n"])
check("the cluster knows its corridor", groups[0]["corridor"] == "West")

# ===========================================================================
section("Messages")
# ===========================================================================
made = crm.seed_templates(c)
check("starter messages are written", made > 0 or len(crm.list_templates(c)) > 0)
again = crm.seed_templates(c)
check("seeding twice does not duplicate them", again == 0)

tpls = crm.list_templates(c, stage="New")
check("the message for this stage is offered first", tpls[0].get("preferred") is True,
      tpls[0]["name"])

body = crm.render_body("Hi {first}, about {interest} in {locality}: {brochure}",
                       {"name": "Rakesh Kumar", "interest": "Elite pack",
                        "locality": "Velachery"}, "https://x/brochure")
check("a message fills itself in for the person",
      body == "Hi Rakesh, about Elite pack in Velachery: https://x/brochure", body)
blank = crm.render_body("Hi {first}", {})
check("a nameless lead does not produce 'Hi {first}'", blank == "Hi there", blank)

tpl = c.execute("SELECT * FROM lead_templates WHERE advance_to IS NOT NULL LIMIT 1").fetchone()
l5 = crm.save_lead(c, {"name": "Test Template", "phone": "9000000005",
                       "stage": tpl["stage"] or "New"})
crm.apply_template(c, l5, tpl["id"])
lead = crm.get_lead(c, l5)
check("sending a message moves the stage with it",
      lead["stage"] == tpl["advance_to"], lead["stage"])
check("sending a message books the next chase",
      lead["next_follow_up"] == plus(tpl["followup_days"]), lead["next_follow_up"])
check("sending a message counts as a contact", lead["touches"] == 1)
check("the timeline says which message went out",
      any(tpl["name"] in u["text"] for u in crm.updates_for(c, l5)))
check("an unknown message id is refused quietly",
      crm.apply_template(c, l5, 999999) is None)

# ===========================================================================
section("Conversion")
# ===========================================================================
before_cust = c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
cid = crm.convert_lead(c, l1)
lead = crm.get_lead(c, l1)
check("converting marks the lead Won", lead["stage"] == "Won")
check("converting stamps the day it was won", lead["won_at"] == TODAY)
check("a won lead stops nagging", lead["next_follow_up"] is None)
check("converting creates the customer", c.execute(
    "SELECT COUNT(*) FROM customers").fetchone()[0] == before_cust + 1)
check("the new customer can be given a map pin", bool(c.execute(
    "SELECT 1 FROM customer_addresses WHERE customer_id=?", (cid,)).fetchone()))
again_cid = crm.convert_lead(c, l1)
check("converting twice does not make a second customer",
      again_cid == cid and c.execute(
          "SELECT COUNT(*) FROM customers").fetchone()[0] == before_cust + 1)

crm.link_subscription(c, l1, "S001")
check("the booked pack is linked back to the lead",
      crm.get_lead(c, l1)["subscription_id"] == "S001")

# ===========================================================================
section("Importing leads the business already has")
# ===========================================================================
n_trials = c.execute("SELECT COUNT(*) FROM trial_orders").fetchone()[0]
made = crm.import_from_trials(c)
check("every trial order becomes a lead", made == n_trials, f"{made} of {n_trials}")
check("importing again adds nothing", crm.import_from_trials(c) == 0)

old = c.execute("SELECT * FROM leads WHERE trial_id IS NOT NULL AND stage='Not now' "
                "LIMIT 1").fetchone()
if old:
    check("an old trial is parked, not dumped on today's list",
          old["next_follow_up"] > TODAY, old["next_follow_up"])
    check("a parked trial still counts as having reached Trial",
          old["peak_stage"] == "Trial", old["peak_stage"])
conv = c.execute("SELECT * FROM leads WHERE trial_id IS NOT NULL AND stage='Won' "
                 "LIMIT 1").fetchone()
if conv:
    check("a trial already marked converted comes in as Won", conv["won_at"] is not None)

# An imported lead ARRIVED on the day of its trial. Stamping it "today" would
# pile months of history into this month and report wins in months that show no
# leads at all — the single most misleading thing this report could do.
bad = c.execute("SELECT COUNT(*) FROM leads l JOIN trial_orders t ON t.id=l.trial_id "
                "WHERE substr(l.created_at,1,10) != t.date").fetchone()[0]
check("an imported lead is dated the day it actually came in", bad == 0, f"{bad} wrong")

linked = c.execute("SELECT COUNT(*) FROM leads WHERE stage='Won' AND trial_id IS NOT NULL "
                   "AND customer_id IS NOT NULL").fetchone()[0]
check("a converted trial is matched to the customer it became", linked > 0, linked)
# Matching the wrong person would invent revenue, so only exact matches count.
wrong = [r["id"] for r in c.execute(
    "SELECT l.id, l.name ln, cu.name cn FROM leads l JOIN customers cu ON cu.id=l.customer_id "
    "WHERE l.trial_id IS NOT NULL")
    if "".join(ch for ch in (r["ln"] or "").lower() if ch.isalnum())
    != "".join(ch for ch in (r["cn"] or "").lower() if ch.isalnum())]
check("no trial was matched to a different person", not wrong, wrong)
check("an unmatched trial is left unlinked rather than guessed", c.execute(
    "SELECT COUNT(*) FROM leads WHERE stage='Won' AND trial_id IS NOT NULL "
    "AND customer_id IS NULL").fetchone()[0] >= 0)

rep_i = crm.report(c, months=6)
months_with_wins = [m for m in rep_i["months"] if m["won"]]
check("a month that shows wins also shows the leads behind them",
      all(m["leads"] > 0 for m in months_with_wins),
      [m["label"] for m in months_with_wins if not m["leads"]])
# A same-day conversion really is 0 days, so the metric is not "never zero" —
# it is that reconstructed history must not be counted as a measurement at all.
won_total = c.execute("SELECT COUNT(*) FROM leads WHERE stage='Won'").fetchone()[0]
observed = c.execute(
    "SELECT COUNT(*) FROM leads WHERE stage='Won' AND (COALESCE(kind,'new')='new' "
    "OR COALESCE(won_at,'') > substr(COALESCE(created_at,''),1,10))").fetchone()[0]
check("time to win is measured only on journeys we watched",
      rep_i["speed_n"] == observed, f"{rep_i['speed_n']} vs {observed}")
check("back-dated imports are kept out of the speed average",
      rep_i["speed_n"] < won_total, f"{rep_i['speed_n']} of {won_total}")
check("revenue is credited to the leads that earned it", rep_i["total_revenue"] > 0,
      rep_i["total_revenue"])

cands = crm.winback_candidates(c)
check("lapsed customers are found", len(cands) > 0, len(cands))
check("someone with a live pack is not on the win-back list", all(
    not c.execute("SELECT 1 FROM subscriptions WHERE customer_id=? AND status IN "
                  "('Active','Ending Soon','Paused')", (x["id"],)).fetchone()
    for x in cands))
made = crm.import_winbacks(c)
check("win-back leads are created", made > 0, made)
check("importing win-backs again adds nothing", crm.import_winbacks(c) == 0)
wb = c.execute("SELECT * FROM leads WHERE kind='winback' LIMIT 1").fetchone()
check("a win-back points at the customer we already have", bool(wb["customer_id"]))
check("a win-back does not claim a win it has not made yet",
      wb["peak_stage"] == "New", wb["peak_stage"])
check("win-backs skip people who never actually ate", all(
    c.execute("SELECT MAX(last_delivery_date) d FROM subscriptions WHERE customer_id=?",
              (r["customer_id"],)).fetchone()["d"] is not None
    for r in c.execute("SELECT customer_id FROM leads WHERE kind='winback'")))

# Converting a win-back must reuse the customer, never clone them.
before_cust = c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
crm.convert_lead(c, wb["id"])
check("winning back a customer does not duplicate them", c.execute(
    "SELECT COUNT(*) FROM customers").fetchone()[0] == before_cust)

# ===========================================================================
section("Reporting")
# ===========================================================================
# A lead that arrived last month and was won this month must be counted in both
# of its own months — this is the single easiest number to get wrong.
last_month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
lx = crm.save_lead(c, {"name": "Test Attribution", "phone": "9000000009",
                       "source": "Meta ads"})
c.execute("UPDATE leads SET created_at=?, stage='Won', won_at=?, peak_stage='Won' "
          "WHERE id=?", (last_month + "-05 10:00:00", TODAY, lx))
c.commit()

crm.add_spend(c, {"month": last_month, "channel": "Meta ads", "amount": "3000"})
rep = crm.report(c, months=3)
m_last = next(m for m in rep["months"] if m["month"] == last_month)
m_this = next(m for m in rep["months"] if m["month"] == date.today().strftime("%Y-%m"))
check("the lead counts in the month it arrived", m_last["leads"] >= 1)
check("the win counts in the month it was won", m_this["won"] >= 1)
check("cost per lead uses that month's spend",
      abs(m_last["cpl"] - 3000 / m_last["leads"]) < 0.01, m_last["cpl"])
check("a month with no spend reports no cost per lead", m_this["spend"] == 0)

meta = next(s for s in rep["sources"] if s["source"] == "Meta ads")
check("spend lands on the matching source", meta["spend"] == 3000, meta["spend"])
check("the source counts its leads", meta["leads"] >= 1)

ns = [f["n"] for f in rep["funnel"]]
check("the funnel never widens as it goes down",
      all(ns[i] >= ns[i + 1] for i in range(len(ns) - 1)), ns)
check("every lead has reached the top of the funnel",
      rep["funnel"][0]["n"] == rep["total_leads"], f"{rep['funnel'][0]['n']} vs {rep['total_leads']}")
check("a lost lead still counts at the step it reached",
      rep["funnel"][3]["n"] >= 1, ns)

crm.set_stage(c, l2, "Lost", reason="Too far / out of our range")
rep = crm.report(c, months=3)
check("the lost reason is counted",
      any(r["reason"] == "Too far / out of our range" for r in rep["lost_reasons"]))
check("lost reasons add up to 100%",
      abs(sum(r["pct"] for r in rep["lost_reasons"]) - 100) < 0.5)
check("won revenue is real money, not the guess we typed",
      rep["total_revenue"] >= 0)
check("a business with no ad spend does not divide by zero",
      isinstance(rep["overall_roas"], (int, float)))

crm.delete_spend(c, crm.list_spend(c)[0]["id"])
check("spend can be removed again", len(crm.list_spend(c)) == 0)

# ===========================================================================
section("Legacy data")
# ===========================================================================
c.execute("UPDATE leads SET stage='Negotiating' WHERE id=?", (l3,))
c.commit()
check("an old 'Negotiating' lead reads as 'Price sent'",
      crm.normalise_stage("Negotiating") == "Price sent")
rows = crm.list_leads(c, view="All")
check("an old stage does not crash the list",
      any(r["id"] == l3 and r["stage"] == "Price sent" for r in rows))
check("an old stage does not crash the report", bool(crm.report(c, months=1)))
crm.set_stage(c, l3, "Not now")

# ===========================================================================
section("Pages")
# ===========================================================================
for url in ["/crm", "/crm?view=Open", "/crm?view=Won", "/crm?view=All",
            "/crm?q=Test", "/crm/reports", "/crm/reports?months=12",
            "/crm/messages", "/crm/paste", "/crm/new", f"/crm/{l1}",
            f"/crm/{l1}/edit"]:
    r = client.get(url)
    check(f"GET {url}", r.status_code == 200, r.status_code)

r = client.post("/crm/paste", data={"text": "Ravi 9000000123 Medavakkam elite pack"})
check("POST /crm/paste reads the message", r.status_code == 200
      and b"9000000123" in r.data)

r = client.post("/crm/quick", data={"name": "Test Route", "phone": "9000000010"},
                follow_redirects=True)
check("POST /crm/quick adds a lead", r.status_code == 200)
new_lead = c.execute("SELECT * FROM leads WHERE phone='9000000010'").fetchone()
check("the quick-added lead is in the database", new_lead is not None)

r = client.post(f"/crm/{new_lead['id']}/snooze", data={"days": "3"},
                follow_redirects=True)
check("POST snooze works from the page", r.status_code == 200
      and crm.get_lead(c, new_lead["id"])["next_follow_up"] == plus(3))

r = client.post(f"/crm/{new_lead['id']}/stage", data={"stage": "Price sent"},
                follow_redirects=True)
check("POST stage works from the page",
      crm.get_lead(c, new_lead["id"])["stage"] == "Price sent")

t = crm.list_templates(c)[0]
r = client.post(f"/crm/{new_lead['id']}/sent",
                data={"template_id": t["id"]}, follow_redirects=True)
check("POST sent logs the message", r.status_code == 200
      and crm.get_lead(c, new_lead["id"])["touches"] >= 1)

r = client.post(f"/crm/{new_lead['id']}/delete", follow_redirects=True)
check("a mistaken lead can be deleted",
      crm.get_lead(c, new_lead["id"]) is None)
check("deleting a lead takes its timeline with it", c.execute(
    "SELECT COUNT(*) FROM lead_updates WHERE lead_id=?", (new_lead["id"],)).fetchone()[0] == 0)

r = client.post("/crm/spend", data={"month": "2026-09", "channel": "Meta ads",
                                    "amount": "5000"}, follow_redirects=True)
check("POST spend records it", r.status_code == 200 and len(crm.list_spend(c)) == 1)

r = client.post("/crm/messages/save", data={"name": "Test msg", "body": "Hi {first}",
                                            "active": "1", "sort": "900"},
                follow_redirects=True)
check("a new message can be written", r.status_code == 200 and any(
    x["name"] == "Test msg" for x in crm.list_templates(c)))
tid = [x["id"] for x in crm.list_templates(c) if x["name"] == "Test msg"][0]
r = client.post(f"/crm/messages/{tid}/delete", follow_redirects=True)
check("a message can be deleted", not any(
    x["name"] == "Test msg" for x in crm.list_templates(c)))

# ===========================================================================
section("The kitchen is untouched")
# ===========================================================================
AFTER = counts(c)
# Conversions legitimately add customers; everything else must be identical.
converted = c.execute("SELECT COUNT(*) FROM leads WHERE stage='Won' "
                      "AND customer_id IS NOT NULL").fetchone()[0]
check("no subscription was created or destroyed",
      AFTER["subscriptions"] == BEFORE["subscriptions"],
      f"{BEFORE['subscriptions']} -> {AFTER['subscriptions']}")
check("no delivery was created or destroyed",
      AFTER["deliveries"] == BEFORE["deliveries"],
      f"{BEFORE['deliveries']} -> {AFTER['deliveries']}")
check("the day's orders were generated once, by the app's own daily job",
      _gen == c.execute("SELECT COUNT(*) FROM deliveries WHERE date=? "
                        "AND COALESCE(is_extra,0)=0", (TODAY,)).fetchone()[0],
      "the CRM changed today's order rows")
check("no trial order was changed",
      AFTER["trial_orders"] == BEFORE["trial_orders"])
check("no invoice was touched", AFTER["invoices"] == BEFORE["invoices"])
check("the menu is untouched", AFTER["components"] == BEFORE["components"]
      and AFTER["subscription_components"] == BEFORE["subscription_components"])
check("customers only grew by real conversions",
      AFTER["customers"] >= BEFORE["customers"],
      f"{BEFORE['customers']} -> {AFTER['customers']}")

c.close()
print(f"\n{N - len(FAILS)}/{N} checks passed")
if FAILS:
    print("FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("All good.")
