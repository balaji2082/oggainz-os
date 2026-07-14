"""
OG Gainz Command Center — digital brochure.

A customer-facing, always-editable brochure that lives at /brochure (public link,
no login) and is edited at /brochure/edit. Sections are stored in the
brochure_sections table; the body text uses a tiny markup so Balaji can edit
everything from his phone:

    - item            → bullet point
    | col | col |     → table row (first table row = header)
    **text**          → bold
    blank line        → new paragraph

The pack/pricing section is generated from the LIVE plans table at first seed, so
the brochure always starts from the prices actually being sold — after that every
word is editable and the seed never overwrites an edit.
"""

import html
import re

import db
import menu


# ---------------------------------------------------------------------------
# Mini-markup → HTML
# ---------------------------------------------------------------------------
def _inline(text):
    """Escape HTML, then apply **bold**."""
    out = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)


def render_body(body):
    """Render the tiny brochure markup to safe HTML."""
    if not body:
        return ""
    out, para, bullets, table = [], [], [], []

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    def flush_bullets():
        if bullets:
            out.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    def flush_table():
        if table:
            head, *rows = table
            t = ["<div class='bro-tablewrap'><table class='bro-table'>",
                 "<tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr>"]
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
            t.append("</table></div>")
            out.append("".join(t))
            table.clear()

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            flush_para(); flush_bullets(); flush_table()
        elif line.startswith("|") and line.endswith("|") and line.count("|") >= 2:
            flush_para(); flush_bullets()
            table.append([_inline(c.strip()) for c in line.strip("|").split("|")])
        elif line.startswith("- "):
            flush_para(); flush_table()
            bullets.append(_inline(line[2:].strip()))
        else:
            flush_bullets(); flush_table()
            para.append(_inline(line))
    flush_para(); flush_bullets(); flush_table()
    return "".join(out)


def sections(conn, active_only=True):
    q = "SELECT * FROM brochure_sections"
    if active_only:
        q += " WHERE active=1"
    return conn.execute(q + " ORDER BY sort, id").fetchall()


def plain_text(conn):
    """The whole brochure as plain text — fed to OG Agent as brand knowledge,
    so the agent always answers from whatever the brochure currently says."""
    parts = []
    for s in sections(conn):
        block = s["title"]
        if s["subtitle"]:
            block += f" ({s['subtitle']})"
        body = re.sub(r"\*\*(.+?)\*\*", r"\1", s["body"] or "")
        parts.append(block + ":\n" + body.strip())
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Seed (runs once; guarded by a settings flag; never overwrites edits)
# ---------------------------------------------------------------------------
_PROTEIN_HINT = {"Signature Meal Pack": "30–35g protein",
                 "Elite Meal Pack": "35–40g protein",
                 "Athlete Meal Pack": "50–55g protein"}
_TAGLINE_HINT = {"Signature Meal Pack": "The everyday staple — honest macros, balanced plate.",
                 "Elite Meal Pack": "Fully loaded — fresh fruits & yogurt over the core plate.",
                 "Athlete Meal Pack": "Performance fuel — extra protein for serious training appetites."}


def _inr0(v):
    return f"₹{int(v):,}" if v else "—"


def _packs_body(conn):
    """Build the Standard Meal Packs section from the LIVE plans + recipes."""
    lines = ["One perfect plate a day — freshly cooked, macro-tracked, delivered Mon–Fri.", ""]
    rows = ["| Pack | Trial (1 day) | Weekly (5 meals) | Monthly (20 meals) | Validity |"]
    details = []
    for name in ("Signature Meal Pack", "Elite Meal Pack", "Athlete Meal Pack"):
        p = conn.execute("SELECT * FROM plans WHERE name=? AND active=1", (name,)).fetchone()
        if not p:
            continue
        short = name.replace(" Meal Pack", "")
        rows.append(f"| **{short}** | {_inr0(p['price_trial'])} | {_inr0(p['price_weekly'])} "
                    f"| {_inr0(p['price_monthly'])} | {p['validity_days'] or '—'} days |")
        recipe = [f"{r['name']} {menu.fmt_qty(r['qty'], r['unit'])}"
                  for r in menu.plan_recipe(conn, p["id"])]
        if recipe:
            details.append(f"- **{short}** ({_PROTEIN_HINT.get(name, '')}) — "
                           + " · ".join(recipe)
                           + f". {_TAGLINE_HINT.get(name, '')}")
    lines += rows + ["", "**In every box:**"] + details
    lines += ["", "Government holidays carry forward — your validity extends automatically, never wasted."]
    return "\n".join(lines)


def _addons_body(conn):
    lines = ["Boost any plan — priced per meal, added to any pack.", ""]
    rows = ["| Add-on | Price / meal |"]
    for c in menu.all_components(conn):
        if c["addon_price"]:
            qty = menu.fmt_qty(c["step"], c["unit"]) if c["unit"] == "g" else ""
            label = f"{c['emoji'] or ''} {c['name']}" + (f" (+{qty})" if qty else "")
            rows.append(f"| {label.strip()} | ₹{int(c['addon_price'])} |")
    return "\n".join(lines + rows)


_WHY_BODY = """Not another meal service. A standard. No refined oils like palm oil, no soft \
portions, no inflated "high-protein" labels — just disciplined cooking, real ingredients and \
macros you can actually trust.

- **Protein first. Always.** Every meal is built around lean, high-quality protein — primarily chicken breast. Properly portioned, properly stated.
- **Clean cooking. No shortcuts.** Cooked in extra virgin olive oil, ghee and coconut oil in controlled quantities — never refined oils. Every protein marinated up to 24 hours.
- **Macros you can trust.** Protein, carbs and fats calculated on actual ingredient quantities — not generic estimates. When we say 30–35g of protein, we mean it.
- **Built around your goal.** Fat loss, lean muscle or clean daily eating — every plan fits a real goal.
- **Premium protein shakes.** High-quality whey, sweetened with honey, monk fruit or stevia. Zero white sugar in any shake. Ever.
- **Service that stays consistent.** Direct WhatsApp support (no chatbot), regular follow-ups, and feedback that actually shapes the menu."""

_MENU_BODY = """A fresh plate every weekday — same rotation across all packs, portions scale by pack.

| Day | Plate of the day |
| **Mon** | Hariyali Chicken · Sweet Potato · Carrot & Corn · Egg |
| **Tue** | Tandoori Chicken · Ghee Podi Rice · Veg Salad · Egg |
| **Wed** | OG Smoky Chicken · Coriander Rice · Broccoli & Peas · Egg |
| **Thu** | Lemon Pepper Chicken · Chapathi + Dal · Beetroot & Carrot · Egg |
| **Fri** | Honey BBQ Chicken · Italian Herb Rice · Capsicum & Peas · Egg |

Elite & Athlete packs add seasonal fruits (120–150g) and yogurt (50ml) every day.

Tentative menu — proteins, carbs & sides may vary by season and availability, but every meal delivers within the promised macro range. That's the promise that never changes."""

_PROGRAMS_BODY = """Built to recompose, not just feed. Goal-driven nutrition — 3 meals a day \
(lunch · snack · dinner), 5 days a week.

| Program | Daily energy | Protein / day | Trial | Weekly | Monthly |
| **Fat Loss — The Cut** | 1,200–1,300 kcal | 80–90g | ₹599 | ₹2,499 | ₹8,999 |
| **Muscle Gain — The Build** (shake snack) | 1,600–1,800 kcal | 85–95g | ₹699 | ₹3,199 | ₹11,499 |
| **Muscle Gain — The Build** (fruit box + egg whites) | 1,600–1,800 kcal | 85–95g | ₹699 | ₹2,799 | ₹9,999 |

- **The Cut** — high-protein, controlled-calorie. Designed to strip fat while preserving muscle.
- **The Build** — calorie-dense, protein-loaded. Engineered for hypertrophy and strength progression. Snack option A: premium protein shake (red banana / coffee / avocado / chocolate). Option B: seasonal fruit box + 3 boiled egg whites.

Weekly validity 14 days · monthly validity 60 days. Ask us for the full weekly menus."""

_BYOM_BODY = """Your plate, your rules. Pick your protein, carb, portion, veggies and add-ons — \
total flexibility, macros stay tracked and honest, cooked the same clean way as every OG Gainz plate.

Message us on WhatsApp and we'll build it with you."""

_DELIVERY_BODY = """Freshly cooked in our Perumbakkam kitchen and delivered across Chennai, Monday to Friday.

- **Lunch:** 11:00 AM – 1:45 PM · **Dinner:** 5:30 PM – 8:00 PM.
- **Free delivery** within our free-delivery zone around Perumbakkam; beyond it a small distance-based delivery fee (typically ₹30–₹75) applies, confirmed with you at booking.
- Skipped meals (informed before 6:00 PM the previous day) carry forward — nothing is wasted.
- Government holidays extend your validity automatically.

**How to start:**
- **1 · Pick your pack** — Signature, Elite or Athlete, a transformation program, or build your own.
- **2 · We cook fresh** — macro-tracked plates, prepped daily in our kitchen.
- **3 · Delivered to you** — hot and on time. Just eat.

Start with a trial — no commitment. WhatsApp **+91 93617 98644** · Instagram **@_oggainz**"""


def seed_brochure(conn):
    """Idempotent: creates the starter brochure once. Never touches edited content."""
    if db.get_setting(conn, "brochure_seeded_v1"):
        return False
    if conn.execute("SELECT 1 FROM brochure_sections LIMIT 1").fetchone():
        db.set_setting(conn, "brochure_seeded_v1", "1")
        return False
    rows = [
        (10, "Why OG Gainz", "Real food · Honest macros · For people who train, track & care", _WHY_BODY),
        (20, "Standard Meal Packs", "One plate · Five days · Mon–Fri", _packs_body(conn)),
        (30, "Weekly Tentative Menu", "The weekday plates — Mon to Fri", _MENU_BODY),
        (40, "Transformation Programs", "Fat Loss · Muscle Gain · 3 meals a day", _PROGRAMS_BODY),
        (50, "Add-Ons & Extras", "Boost any plan", _addons_body(conn)),
        (60, "Build Your Own Meal", "Your plate, your rules", _BYOM_BODY),
        (70, "Delivery & Getting Started", "Perumbakkam kitchen · Chennai", _DELIVERY_BODY),
    ]
    for sort, title, subtitle, body in rows:
        conn.execute("INSERT INTO brochure_sections(sort,title,subtitle,body,active,updated_at) "
                     "VALUES(?,?,?,?,1,?)", (sort, title, subtitle, body, db.now_iso()))
    db.set_setting(conn, "brochure_seeded_v1", "1")
    conn.commit()
    return True
