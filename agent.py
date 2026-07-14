"""
OG Gainz Command Center — OG Agent.

The in-app intelligence, in two layers:

1. RULES BRAIN (always on, free, deterministic)
   * daily digest + alerts → the notification bell (and Telegram if configured)
   * parses subscription notes into proposed component rules
     ("paneer on tue and friday instead of chicken" → a structured swap rule
      that Balaji confirms with one tap — the prep list then applies it)

2. AI BRAIN (optional — needs an Anthropic API key in Settings)
   * free-form chat that knows the brand (live brochure + terms + learned facts),
     the menu and today's live data — a complete FAQ for anything OG Gainz
   * can PROPOSE actions (rules / reminders / remember-facts) which are only
     applied after Balaji taps confirm — the agent never changes data on its own
   * LEARNS: when Balaji tells it something new, it proposes saving the fact to
     its long-term memory (agent_facts); confirmed facts are injected into every
     future chat, so its knowledge grows over time.
"""

import json
import re
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

import db
import menu

WEEKDAY_LOOKUP = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}


# ---------------------------------------------------------------------------
# Notifications + Telegram
# ---------------------------------------------------------------------------
def notify(conn, kind, title, body="", link=None, telegram=True):
    conn.execute("INSERT INTO notifications(at,kind,title,body,link,is_read) VALUES(?,?,?,?,?,0)",
                 (db.now_iso(), kind, title, body, link))
    conn.commit()
    if telegram:
        send_telegram(conn, f"🥗 {title}" + (f"\n{body}" if body else ""))


def unread_count(conn):
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE is_read=0").fetchone()[0]


def send_telegram(conn, text):
    """Best-effort push to Balaji's Telegram. Never raises — the app must keep
    working even if Telegram is unreachable / not configured."""
    token = db.get_setting(conn, "telegram_bot_token")
    chat = db.get_setting(conn, "telegram_chat_id")
    if not token or not chat:
        return False
    try:
        data = json.dumps({"chat_id": chat, "text": text[:3900]}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data,
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=6).read()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Daily digest (rules brain) — built lazily on the first request of each day
# ---------------------------------------------------------------------------
def run_daily(conn):
    """Generate the day's digest + alerts once per day. Cheap no-op afterwards."""
    today = date.today().strftime("%Y-%m-%d")
    if db.get_setting(conn, "agent_last_digest") == today:
        return False
    db.set_setting(conn, "agent_last_digest", today)   # set first: never double-send

    # Housekeeping: prep-list tick-offs from finished days are no longer needed.
    cutoff = (date.today() - timedelta(days=14)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM prep_ticks WHERE date < ?", (cutoff,))
    conn.commit()

    import core
    lines = []
    if core.is_operating_day(conn, today):
        s = core.today_summary(conn, today)
        lines.append(f"🍱 {s['total']} meals today — {s['lunch_total']} lunch / "
                     f"{s['dinner_total']} dinner.")
    else:
        lines.append("😴 Kitchen is off today.")

    bl = menu.backlog(conn, today)
    if bl["count"]:
        lines.append(f"⏳ {bl['count']} past order(s) never marked delivered/skipped — "
                     "clear them from Today → Unmarked past orders.")

    unpaid = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(total_amount-COALESCE(amount_received,0)),0) amt "
        "FROM subscriptions WHERE payment_status!='Paid'").fetchone()
    if unpaid["n"]:
        lines.append(f"💳 ₹{int(unpaid['amt'])} still to collect from {unpaid['n']} subscription(s).")

    ending = conn.execute("SELECT customer_name, remaining_meals FROM subscriptions "
                          "WHERE status='Ending Soon' ORDER BY remaining_meals").fetchall()
    for e in ending:
        lines.append(f"⚠️ {e['customer_name']} has {e['remaining_meals']} meal(s) left — "
                     "time to talk renewal.")

    due = conn.execute("SELECT COUNT(*) n FROM leads WHERE next_follow_up<=? "
                       "AND stage NOT IN ('Won','Lost')", (today,)).fetchone()["n"]
    if due:
        lines.append(f"📞 {due} CRM follow-up(s) due.")

    trials = conn.execute("SELECT name FROM trial_orders WHERE delivery_date=?", (today,)).fetchall()
    if trials:
        lines.append("🎁 Trial deliveries today: " + ", ".join(t["name"] for t in trials) + ".")

    notify(conn, "digest", f"OG Agent — {datetime.now().strftime('%a %d %b')} morning brief",
           "\n".join(lines), link="/today")
    return True


# ---------------------------------------------------------------------------
# Note parser (rules brain): free text → proposed component rules
# ---------------------------------------------------------------------------
ALIASES = {"curd": "Yogurt (50ml)", "yogurt": "Yogurt (50ml)", "yoghurt": "Yogurt (50ml)",
           "sprout": "Legumes / Sprouts", "legume": "Legumes / Sprouts",
           "fish": "Fish Fillet (Basa)", "shake": "Protein Shake", "salad": "Protein Salad",
           "carb": "Carb of the day", "rice": "Rice (of the day)", "chapati": "Chapathi (2 pc)",
           "chapathi": "Chapathi (2 pc)", "roti": "Chapathi (2 pc)", "fruit": "Fruits",
           "veg": "Veggies", "veggie": "Veggies", "vegetable": "Veggies"}


def _find_component(conn, text):
    """Best-effort match of a food word to a catalog component (whole words only,
    so 'veggies' can never match 'Egg')."""
    t = re.sub(r"[^a-z /]", " ", (text or "").lower()).strip()
    if not t:
        return None
    rows = menu.all_components(conn)
    t_sing = t[:-1] if t.endswith("s") else t
    for r in rows:                                   # whole-name match first
        if r["name"].lower() in (t, t_sing):
            return r
    for k, v in ALIASES.items():                     # alias words
        if re.search(r"\b" + k + r"s?\b", t):
            for r in rows:
                if r["name"] == v:
                    return r
    for r in rows:                                   # component's lead word in the text
        base = r["name"].lower().split(" (")[0].strip()
        base = base[:-1] if base.endswith("s") else base
        if base and re.search(r"\b" + re.escape(base) + r"s?\b", t):
            return r
    return None


def _extract_weekdays(text):
    days = sorted({v for k, v in WEEKDAY_LOOKUP.items()
                   if re.search(r"\b" + k + r"s?\b", text)})
    return ",".join(str(d) for d in days) if days else None


def _extract_qty(text, comp):
    m = re.search(r"(\d+)\s*(g|gram|grams)\b", text)
    if m and comp and comp["unit"] == "g":
        return float(m.group(1))
    m = re.search(r"(\d+)\s*(?:x|pcs?|pieces?|nos?|eggs?)\b", text)
    if m and comp and comp["unit"] == "pcs":
        return float(m.group(1))
    return float(comp["default_qty"] or 1) if comp else 1


def parse_note(conn, text):
    """Turn a free-text note into structured rule proposals.

    Handles the common patterns:
      "paneer on tuesday and friday instead of chicken"  → swap
      "X instead of Y (on <days>)"                       → swap
      "no <X> (on <days>)" / "without X"                 → remove
      "extra egg on monday" / "add sprouts"              → add
    Returns [{action, from_id, from_name, to_id, to_name, qty, weekdays, label}].
    """
    if not text:
        return []
    proposals = []
    for chunk in re.split(r"[,;\n]", text.lower()):
        chunk = chunk.strip()
        if not chunk:
            continue
        wd = _extract_weekdays(chunk)

        m = re.search(r"(.+?)\s+instead\s+of\s+([a-z /()]+)", chunk)
        if m:
            to_part = re.sub(r"\bon\b.*", "", m.group(1))
            to_part = re.sub(r"\d+\s*(g|grams?)?", "", to_part)
            for k in WEEKDAY_LOOKUP:
                to_part = re.sub(r"\b" + k + r"s?\b", "", to_part)
            to_c = _find_component(conn, re.sub(r"\b(and|or|with|needs?|wants?|he|she|they)\b",
                                                "", to_part))
            from_c = _find_component(conn, m.group(2))
            if to_c and from_c and to_c["id"] != from_c["id"]:
                qty = _extract_qty(chunk, to_c)
                proposals.append(_prop("swap", from_c, to_c, qty, wd))
            continue

        m = re.search(r"\b(?:no|without|skip|remove)\s+([a-z /()]+)", chunk)
        if m:
            c = _find_component(conn, m.group(1))
            if c:
                proposals.append(_prop("remove", c, None, None, wd))
            continue

        m = re.search(r"\b(?:extra|add|additional)\s+(\d+\s*g?\s*)?([a-z /()]+)", chunk)
        if m:
            c = _find_component(conn, m.group(2))
            if c:
                qty = _extract_qty(chunk, c)
                proposals.append(_prop("add", None, c, qty, wd))
    return proposals


def _prop(action, from_c, to_c, qty, weekdays):
    label_days = "every day"
    if weekdays:
        label_days = ", ".join(menu.WEEKDAY_NAMES[int(x)] for x in weekdays.split(","))
    if action == "swap":
        label = f"{to_c['name']} {menu.fmt_qty(qty, to_c['unit'])} instead of {from_c['name']} · {label_days}"
    elif action == "remove":
        label = f"No {from_c['name']} · {label_days}"
    else:
        label = f"Add {to_c['name']} {menu.fmt_qty(qty, to_c['unit'])} · {label_days}"
    return {"action": action,
            "from_id": from_c["id"] if from_c else None,
            "from_name": from_c["name"] if from_c else None,
            "to_id": to_c["id"] if to_c else None,
            "to_name": to_c["name"] if to_c else None,
            "qty": qty, "weekdays": weekdays, "label": label}


def suggestions_for_sub(conn, sub):
    """Proposals from this subscription's notes that aren't covered by an existing
    rule and weren't dismissed. Shown on the subscription page with 1-tap apply."""
    props = parse_note(conn, sub["notes"])
    if not props:
        return []
    existing = {(r["action"], r["from_component_id"], r["to_component_id"], r["weekdays"])
                for r in menu.rules_for_sub(conn, sub["id"], active_only=False)}
    dismissed = set((db.get_setting(conn, f"agent_dismissed_{sub['id']}") or "").split("|"))
    out = []
    for p in props:
        key = (p["action"], p["from_id"], p["to_id"], p["weekdays"])
        if key in existing or p["label"] in dismissed:
            continue
        out.append(p)
    return out


def dismiss_suggestion(conn, sub_id, label):
    cur = db.get_setting(conn, f"agent_dismissed_{sub_id}") or ""
    db.set_setting(conn, f"agent_dismissed_{sub_id}", (cur + "|" if cur else "") + label)


# ---------------------------------------------------------------------------
# Long-term memory (agent_facts) — how OG Agent learns
# ---------------------------------------------------------------------------
def facts(conn):
    return conn.execute("SELECT * FROM agent_facts WHERE active=1 ORDER BY id").fetchall()


def add_fact(conn, fact, topic=None, source="chat"):
    fact = (fact or "").strip()
    if not fact:
        return None
    conn.execute("INSERT INTO agent_facts(at,topic,fact,source,active) VALUES(?,?,?,?,1)",
                 (db.now_iso(), (topic or "").strip() or None, fact, source))
    conn.commit()
    return fact


def delete_fact(conn, fid):
    conn.execute("DELETE FROM agent_facts WHERE id=?", (fid,))
    conn.commit()


# ---------------------------------------------------------------------------
# AI brain (Claude API) — optional
# ---------------------------------------------------------------------------
def ai_ready(conn):
    return bool(db.get_setting(conn, "anthropic_api_key"))


def _snapshot(conn):
    """A compact live-business snapshot the AI can reason over."""
    import core
    today = date.today().strftime("%Y-%m-%d")
    stats = core.headline_stats(conn)
    prep = menu.prep_for_day(conn, today)
    subs = []
    for s in conn.execute("SELECT * FROM subscriptions WHERE status IN "
                          "('Active','Ending Soon','Paused') ORDER BY id").fetchall():
        comp = [f"{c['name']} {menu.fmt_qty(c['qty'], c['unit'])}" +
                (" (add-on)" if c["kind"] == "addon" else "")
                for c in menu.sub_composition(conn, s["id"])]
        rules = [menu.rule_label(r) for r in menu.rules_for_sub(conn, s["id"])]
        subs.append({"id": s["id"], "customer": s["customer_name"], "plan": s["plan_name"],
                     "slot": s["slot"], "status": s["status"],
                     "remaining_meals": s["remaining_meals"],
                     "payment": s["payment_status"], "notes": s["notes"],
                     "composition": comp, "rules": rules})
    monthly = core.monthly_revenue(conn)[-3:]
    return {
        "today": today, "weekday": datetime.now().strftime("%A"),
        "headline": {"active_subs": stats["active"], "ending_soon": stats["ending"],
                     "to_collect": stats["pending_all"], "customers": stats["customers"]},
        "todays_prep": {
            "lunch_meals": prep["lunch"]["meals"], "dinner_meals": prep["dinner"]["meals"],
            "totals": [f"{r['emoji']} {r['name']}: {r['display']}"
                       + (f" ({r['breakdown']})" if r["breakdown"] else "")
                       for r in prep["total"]["components"]],
        },
        "backlog_unmarked_past_orders": menu.backlog(conn, today)["count"],
        "subscriptions": subs,
        "last_3_months_revenue": [{"month": m["month"], "revenue": m["revenue"],
                                   "received": m["received"]} for m in monthly],
    }


SYSTEM_PROMPT = """You are OG Agent — the intelligent operations assistant inside the OG Gainz \
Command Center, built for Balaji, the founder. You are his complete FAQ and operations brain: \
you answer anything about the brand, plans, pricing, menus, delivery, terms and the live business.

ABOUT OG GAINZ: a Chennai cloud-kitchen (since 2025, Perumbakkam) selling macro-tracked \
meal subscriptions + fitness coaching, founder-led by Balaji. Tagline "Eat like it matters." \
Kitchen runs Monday–Friday. Payments in advance; skipped meals (informed before 6pm the \
previous day) carry forward within validity.

TERMS SUMMARY: monthly packs = 20 meals (validity 40 days Signature / 50 days Elite & Athlete); \
weekly packs = 5 meals, 15-day validity. Kitchen-closed days extend validity automatically. \
Delivery windows: lunch 11:00–13:45, dinner 17:30–20:00. Agreed delivery slots are honoured on a \
best-effort basis — occasional delays can happen (traffic, weather, logistics). Delivery is free \
in the free zone around Perumbakkam; beyond it a distance-based fee (₹30–₹75) applies, agreed at \
booking and shown on the invoice. No refunds once a subscription starts; meal issues must be \
reported the same day.

BRAND KNOWLEDGE below comes from the live brochure (the same one customers see) plus FACTS \
BALAJI HAS TAUGHT YOU. Treat learned facts as the newest truth — if a fact contradicts the \
brochure, the fact wins. Answer FAQ-style questions from this knowledge; never invent prices \
or policies that aren't in it.

YOU CAN SEE the live business snapshot provided with each conversation. Use it — be specific \
with names, numbers and rupee amounts. Give short, practical, honest answers (Balaji reads on \
his phone). Use ₹. Never invent data that isn't in the snapshot.

WHEN BALAJI ASKS FOR A CHANGE that maps to a standing meal tweak (e.g. "give Ram paneer \
instead of chicken on Tuesdays"), append an action block at the END of your reply, exactly:
```og-action
{"type":"rule","sub_id":"S012","action":"swap","from":"Chicken","to":"Paneer","qty":100,"weekdays":"1","note":"why"}
```
(weekdays = comma-separated Mon=0..Sun=6, omit for every day. action = swap|add|remove.)
For a one-off reminder, use {"type":"reminder","title":"...","body":"..."}.

WHEN BALAJI TELLS YOU SOMETHING NEW worth remembering permanently — a price change, a new \
policy, a customer preference, a supplier detail, a correction to your knowledge — propose \
saving it to your long-term memory with:
```og-action
{"type":"remember","topic":"pricing","fact":"one clear sentence stating the fact"}
```
Write the fact as one self-contained sentence (it will be read months later without context). \
Propose it in the same reply where he tells you; don't wait to be asked.

Only propose actions you're confident about — Balaji confirms each with one tap. \
You never change data or save memory yourself. Final call is always his."""


def _knowledge(conn):
    """The dynamic brand-knowledge block appended to the system prompt: the live
    brochure text (always current, since Balaji edits it in /brochure/edit) plus
    every fact he has taught the agent."""
    import brochure
    parts = []
    try:
        bro = brochure.plain_text(conn)
        if bro:
            parts.append("=== BROCHURE (live, customer-facing) ===\n" + bro)
    except Exception:
        pass
    learned = facts(conn)
    if learned:
        lines = [f"- [{f['topic'] or 'general'}] {f['fact']} (saved {str(f['at'])[:10]})"
                 for f in learned]
        parts.append("=== FACTS BALAJI TAUGHT YOU (newest truth — these win) ===\n"
                     + "\n".join(lines))
    return ("\n\n".join(parts))[:24000]


def ai_chat(conn, user_text):
    """One chat turn with the Claude API. Returns (reply_text, actions, error)."""
    key = db.get_setting(conn, "anthropic_api_key")
    if not key:
        return None, [], "No API key set. Add your Anthropic API key in Settings → OG Agent."
    model = db.get_setting(conn, "agent_model", "claude-sonnet-5")

    history = conn.execute("SELECT role, content FROM agent_messages "
                           "ORDER BY id DESC LIMIT 12").fetchall()
    msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(history)]
    msgs.append({"role": "user", "content": user_text})

    body = json.dumps({
        "model": model, "max_tokens": 1200,
        "system": SYSTEM_PROMPT + "\n\n" + _knowledge(conn)
                  + "\n\nLIVE SNAPSHOT (just fetched):\n"
                  + json.dumps(_snapshot(conn), ensure_ascii=False),
        "messages": msgs,
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"Content-Type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        reply = "".join(b.get("text", "") for b in resp.get("content", []))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        return None, [], f"Claude API error {e.code}: {detail or e.reason}"
    except Exception as e:
        return None, [], f"Could not reach the Claude API ({e})."

    actions = []
    def _collect(m):
        try:
            actions.append(json.loads(m.group(1)))
        except Exception:
            pass
        return ""
    clean = re.sub(r"```og-action\s*(\{.*?\})\s*```", _collect, reply, flags=re.S).strip()

    conn.execute("INSERT INTO agent_messages(at,role,content) VALUES(?,?,?)",
                 (db.now_iso(), "user", user_text))
    conn.execute("INSERT INTO agent_messages(at,role,content) VALUES(?,?,?)",
                 (db.now_iso(), "assistant", clean))
    conn.commit()
    return clean, actions, None


def apply_action(conn, a):
    """Apply one confirmed agent action. Returns a human summary or None."""
    if a.get("type") == "reminder":
        notify(conn, "reminder", a.get("title") or "Reminder", a.get("body") or "")
        return "Reminder saved."
    if a.get("type") == "remember":
        saved = add_fact(conn, a.get("fact"), a.get("topic"))
        return "Learned ✓ — I'll remember that from now on." if saved else None
    if a.get("type") == "rule":
        sub = conn.execute("SELECT id FROM subscriptions WHERE id=?", (a.get("sub_id"),)).fetchone()
        if not sub:
            return None
        from_c = _find_component(conn, a.get("from") or "") if a.get("from") else None
        to_c = _find_component(conn, a.get("to") or "") if a.get("to") else None
        action = a.get("action") or "swap"
        if action == "swap" and not (from_c and to_c):
            return None
        if action == "remove" and not from_c:
            return None
        if action == "add" and not to_c:
            return None
        conn.execute(
            "INSERT INTO component_rules(subscription_id,weekdays,action,from_component_id,"
            "to_component_id,qty,note,source,active,created_at) VALUES(?,?,?,?,?,?,?,?,1,?)",
            (sub["id"], a.get("weekdays") or None, action,
             from_c["id"] if from_c else None, to_c["id"] if to_c else None,
             a.get("qty"), a.get("note"), "agent", db.now_iso()))
        conn.commit()
        return f"Rule saved for {sub['id']} — the prep list applies it automatically."
    return None
