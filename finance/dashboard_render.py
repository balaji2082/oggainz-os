"""
Interactive dashboard renderer for the OG Gainz finance system.

Replaces the old static render_html(). Emits ONE self-contained HTML file:
no CDN, no external fonts, no network calls — it opens straight off disk and
works offline. All filtering and chart drawing happens client side against data
embedded as JSON, so switching views is instant.

Views: Business · Personal · Appa (the third one appears only when that Notion
database is shared with the integration).

Colour: the categorical palette below was validated with the dataviz skill's
validate_palette.js in BOTH modes — lightness band, chroma floor, CVD adjacent
separation, normal-vision floor and surface contrast all PASS. Do not hand-edit
these hexes; re-run the validator if they ever need to change.
    light  #1A8B40 #2C6FC4 #C9651F #A6376B   on surface #FBF9F3
    dark   #2FA652 #4A8BD8 #D0762C #C4548A   on surface #111613
Slots are assigned by cost type in fixed order (COGS, OPEX, CAPEX, DRAW) and
never by rank, so filtering never repaints a survivor.
"""

import json
import os
from datetime import datetime


import model


def load_pairs():
    """Category -> subcategory map, from data/pairs.json when it exists.

    serve_dashboard.py writes that file whenever a subcategory is added or moved, so
    lists edited from the dashboard survive the next rebuild. PAIRS below is the seed.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pairs.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict) and saved:
                return saved
        except (ValueError, OSError):
            pass
    return PAIRS

# Which subcategories belong under which category. Notion stores Subcategory as one flat
# select list, so nothing there stops "Chicken" being filed under "Packaging" — this map
# is what keeps the edit dropdown honest. Categories not listed take no subcategory.
PAIRS = {
    "business": {
        "COGS · Protein": ["Chicken", "Fish", "Paneer", "Eggs", "Legumes",
                                "Protein powder"],
        "COGS · Dairy": ["Milk", "Curd/Yogurt", "Cheese"],
        "COGS · Produce": ["Vegetables", "Fruits", "Herbs"],
        "COGS · Grains & Staples": ["Rice", "Wheat/Atta", "Oats", "Sweet potato"],
        "COGS · Cooking Essentials": ["Oils", "Spices", "Sauces", "Condiments"],
        "COGS · Packaging": ["Trays", "Lids", "Tape", "Labels/Stickers",
                                  "Carry bags", "Cutlery"],
        "COGS · Delivery": ["Porter", "Fuel self-delivery", "Vehicle maintenance"],
        "OPEX · Utilities": ["Cooking gas", "Electricity", "Water"],
        "OPEX · Kitchen Consumables": ["Cleaning", "Foil/wrap", "Gloves", "Tissue"],
        "OPEX · Software & Subscriptions": ["Claude", "Hosting", "Domain", "Canva",
                                                 "Notion"],
        "OPEX · Marketing": ["Ads", "Collabs", "Sampling"],
        "OPEX · Content & Creative": ["Photography", "Props", "Print collateral"],
        "OPEX · Professional Fees": ["Accounting", "Legal"],
        "OPEX · Staff & Help": ["Wages", "Freelance"],
        "CAPEX · Kitchen Equipment": ["Appliances", "Cookware", "Weighing scale",
                                           "Storage"],
        "CAPEX · Tech": ["Laptop", "Phone", "Accessories"],
        "CAPEX · Branding & Setup": ["Signage", "Merch stock"],
    },
    "personal": {
        "Housing": ["Rent", "Maintenance", "Utilities"],
        "Food & Groceries": ["Groceries", "Eating out", "Delivery apps"],
        "Gym & Fitness": ["Membership", "Supplements", "Gear"],
        "Transport": ["Fuel", "Auto/Cab", "Public transport", "Vehicle service"],
        "Health": ["Doctor", "Medicines", "Insurance"],
        "Shopping": ["Clothing", "Personal care", "Household"],
        "Entertainment": ["Streaming", "Outings", "Hobbies"],
        "Learning": ["Courses", "Books", "Tools & subscriptions"],
        "Savings & Investments": ["SIP", "Emergency fund"],
    },
    "dad": {},
}

# Which fields the editor offers, per database, in the order they appear.
EDIT_FIELDS = {
    "business": ["what", "date", "amount", "category", "subcategory", "vendor",
                 "payment", "paid_from", "spread", "orders_covered", "notes"],
    "personal": ["what", "date", "amount", "category", "subcategory", "payment", "notes"],
    "dad": ["what", "date", "amount", "category", "to_business", "notes"],
}

CSS = """
*, *::before, *::after { box-sizing: border-box; }
:root {
  color-scheme: light;
  --plane:#F2EFE5; --surface:#FBF9F3; --raise:#FFFFFF;
  --ink:#0A0A0A; --ink2:#4A4F4B; --muted:#7C827D;
  --grid:#E4E0D2; --axis:#C9C4B2; --border:rgba(10,10,10,.10);
  --brand:#004030; --brandink:#004030;
  --s1:#1A8B40; --s2:#2C6FC4; --s3:#C9651F; --s4:#A6376B;
  --good:#0F7A34; --bad:#C0392B;
  --shadow:0 1px 2px rgba(10,10,10,.04), 0 8px 24px rgba(10,10,10,.05);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --plane:#0B0E0C; --surface:#111613; --raise:#161C18;
    --ink:#FFFFFF; --ink2:#C6CCC7; --muted:#8B918C;
    --grid:#212A24; --axis:#2E3A32; --border:rgba(255,255,255,.10);
    --brand:#7FD4A8; --brandink:#7FD4A8;
    --s1:#2FA652; --s2:#4A8BD8; --s3:#D0762C; --s4:#C4548A;
    --good:#3FBF63; --bad:#E06B5E;
    --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --plane:#0B0E0C; --surface:#111613; --raise:#161C18;
  --ink:#FFFFFF; --ink2:#C6CCC7; --muted:#8B918C;
  --grid:#212A24; --axis:#2E3A32; --border:rgba(255,255,255,.10);
  --brand:#7FD4A8; --brandink:#7FD4A8;
  --s1:#2FA652; --s2:#4A8BD8; --s3:#D0762C; --s4:#C4548A;
  --good:#3FBF63; --bad:#E06B5E;
  --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35);
}
html, body { margin:0; padding:0; }
body {
  background:var(--plane); color:var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size:15px; line-height:1.45; -webkit-font-smoothing:antialiased;
}
.wrap { max-width:1180px; margin:0 auto; padding:20px 18px 72px; }

/* ---------- header ---------- */
.top { display:flex; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-bottom:18px; }
.brandmark { font-size:13px; font-weight:700; letter-spacing:.16em; text-transform:uppercase;
  color:var(--brandink); }
.brandmark span { color:var(--muted); font-weight:600; }
.stamp { margin-left:auto; font-size:12px; color:var(--muted); display:flex; gap:10px; align-items:center; }
.iconbtn { background:var(--surface); border:1px solid var(--border); color:var(--ink2);
  border-radius:8px; padding:6px 10px; font:inherit; font-size:12px; cursor:pointer; }
.iconbtn:hover { background:var(--raise); }

/* ---------- view toggle ---------- */
.views { display:flex; gap:4px; background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:4px; margin-bottom:14px; width:fit-content; max-width:100%;
  overflow-x:auto; }
.views button { flex:0 0 auto; border:0; background:transparent; color:var(--ink2);
  font:inherit; font-weight:600; font-size:14px; padding:9px 18px; border-radius:9px;
  cursor:pointer; white-space:nowrap; }
.views button[aria-selected="true"] { background:var(--brand); color:var(--surface); }
:root[data-theme="dark"] .views button[aria-selected="true"],
@media (prefers-color-scheme: dark) { }
.views button:disabled { opacity:.4; cursor:not-allowed; }

/* ---------- filter row ---------- */
.filters { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:18px; }
.filters select, .filters input {
  font:inherit; font-size:13px; color:var(--ink); background:var(--surface);
  border:1px solid var(--border); border-radius:9px; padding:8px 11px; min-width:0; }
.filters input[type=search] { min-width:200px; flex:1 1 200px; max-width:340px; }
.filters select:focus-visible, .filters input:focus-visible,
.views button:focus-visible, .iconbtn:focus-visible, th[role=button]:focus-visible {
  outline:2px solid var(--s2); outline-offset:2px; }
.chipclear { border:0; background:transparent; color:var(--muted); font:inherit;
  font-size:13px; text-decoration:underline; cursor:pointer; padding:8px 4px; }

/* ---------- cards & kpis ---------- */
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr)); gap:12px;
  margin-bottom:16px; }
.kpi { background:var(--surface); border:1px solid var(--border); border-radius:14px;
  padding:14px 16px; box-shadow:var(--shadow); }
.kpi .lab { font-size:12px; color:var(--muted); margin-bottom:6px; }
.kpi .val { font-size:26px; font-weight:650; letter-spacing:-.02em; }
.kpi.hero .val { font-size:44px; line-height:1.05; }
.kpi .sub { font-size:12px; color:var(--ink2); margin-top:4px; }
.kpi .sub b { font-weight:650; color:var(--ink); }

.grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:14px; }
.card { background:var(--surface); border:1px solid var(--border); border-radius:14px;
  padding:16px 18px 18px; box-shadow:var(--shadow); margin-bottom:14px; min-width:0; }
.card h2 { margin:0 0 2px; font-size:15px; font-weight:650; letter-spacing:-.01em; }
.card .hint { margin:0 0 14px; font-size:12px; color:var(--muted); }
.card.span { grid-column:1 / -1; }
.scroll { overflow-x:auto; }

/* ---------- legend ---------- */
.legend { display:flex; gap:14px; flex-wrap:wrap; margin:0 0 12px; padding:0; list-style:none; }
.legend li { display:flex; align-items:center; gap:6px; font-size:12px; color:var(--ink2); }
.legend i { width:11px; height:11px; border-radius:3px; display:block; flex:0 0 auto; }

/* ---------- charts ---------- */
/* Charts are drawn 1:1 against their container and carry explicit width/height, so
   they must never be stretched — scaling a viewBox shrinks label text below legible
   size on a phone and pushes bars past the 24px mark spec on a wide screen. */
svg { display:block; max-width:100%; height:auto; overflow:visible; }
.gridline { stroke:var(--grid); stroke-width:1; }
.axisline { stroke:var(--axis); stroke-width:1; }
.tick { fill:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
.barlabel { fill:var(--ink); font-size:11.5px; font-weight:600; }
.catlabel { fill:var(--ink2); font-size:12px; }
.hit { fill:transparent; cursor:default; }
.mark { transition:opacity .12s ease; }
.hovered .mark:not(.on) { opacity:.45; }

/* ---------- tooltip ---------- */
.tip { position:fixed; z-index:60; pointer-events:none; opacity:0; transform:translate(-50%,-115%);
  background:var(--raise); color:var(--ink); border:1px solid var(--border); border-radius:10px;
  padding:8px 11px; box-shadow:var(--shadow); font-size:12px; max-width:260px;
  transition:opacity .1s ease; }
.tip.on { opacity:1; }
.tip .tv { font-size:16px; font-weight:650; letter-spacing:-.01em; display:block; }
.tip .tl { color:var(--ink2); display:flex; align-items:center; gap:6px; margin-top:3px; }
.tip .tl i { width:14px; height:2px; border-radius:2px; display:block; flex:0 0 auto; }
.tip .tm { color:var(--muted); margin-top:2px; }

/* ---------- table ---------- */
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:left; padding:9px 10px; border-bottom:1px solid var(--border);
  white-space:nowrap; }
th { font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted);
  font-weight:650; position:sticky; top:0; background:var(--surface); }
th[role=button] { cursor:pointer; user-select:none; }
th[role=button]:hover { color:var(--ink); }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
td.wrapcell { white-space:normal; min-width:180px; }
tbody tr:hover { background:var(--plane); }
.tblwrap { max-height:520px; overflow:auto; border-radius:10px; }
.pill { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; color:var(--ink2); }
.pill i { width:8px; height:8px; border-radius:2px; flex:0 0 auto; }

/* ---------- editing ---------- */
td.pickcell, th.pickcell { width:34px; padding-left:12px; padding-right:0; }
input[type=checkbox] { width:16px; height:16px; accent-color:var(--s1); cursor:pointer;
  margin:0; vertical-align:middle; }
tr.editable td:not(.pickcell) { cursor:pointer; }
tr.picked { background:var(--plane); }
.editcue { color:var(--muted); font-size:11px; opacity:0; }
tr:hover .editcue { opacity:1; }

.bulkbar { position:fixed; left:50%; bottom:18px; transform:translateX(-50%) translateY(140%);
  z-index:70; background:var(--raise); border:1px solid var(--border); border-radius:14px;
  box-shadow:var(--shadow); padding:10px 12px; display:flex; gap:8px; align-items:center;
  flex-wrap:wrap; max-width:min(94vw,860px); transition:transform .16s ease; }
.bulkbar.on { transform:translateX(-50%) translateY(0); }
.bulkbar .n { font-size:13px; font-weight:650; white-space:nowrap; padding:0 4px; }
.bulkbar select { font:inherit; font-size:13px; color:var(--ink); background:var(--surface);
  border:1px solid var(--border); border-radius:9px; padding:7px 10px; max-width:200px; }
.btn { font:inherit; font-size:13px; font-weight:600; border:1px solid var(--border);
  background:var(--surface); color:var(--ink); border-radius:9px; padding:7px 13px;
  cursor:pointer; white-space:nowrap; }
.btn:hover:not(:disabled) { background:var(--plane); }
.btn:disabled { opacity:.45; cursor:not-allowed; }
.btn.primary { background:var(--brand); color:var(--surface); border-color:transparent; }
:root[data-theme="dark"] .btn.primary { color:#07110C; }
.btn.danger { color:var(--bad); border-color:color-mix(in srgb, var(--bad) 40%, transparent); }

.scrim { position:fixed; inset:0; background:rgba(0,0,0,.35); z-index:80; opacity:0;
  pointer-events:none; transition:opacity .16s ease; }
.scrim.on { opacity:1; pointer-events:auto; }
.drawer { position:fixed; top:0; right:0; height:100%; width:min(420px,100%);
  background:var(--surface); border-left:1px solid var(--border); z-index:90;
  transform:translateX(100%); transition:transform .18s ease; overflow-y:auto;
  padding:18px 20px 28px; }
.drawer.on { transform:translateX(0); }
.drawer h3 { margin:0 0 2px; font-size:16px; font-weight:650; }
.drawer .who { font-size:12px; color:var(--muted); margin-bottom:16px; }
.fld { margin-bottom:12px; }
.fld label { display:block; font-size:12px; color:var(--muted); margin-bottom:5px; }
.fld input, .fld select, .fld textarea {
  width:100%; font:inherit; font-size:14px; color:var(--ink); background:var(--raise);
  border:1px solid var(--border); border-radius:9px; padding:9px 11px; }
.fld textarea { min-height:64px; resize:vertical; }
.fld input:focus-visible, .fld select:focus-visible, .fld textarea:focus-visible,
.btn:focus-visible, .bulkbar select:focus-visible {
  outline:2px solid var(--s2); outline-offset:2px; }
.drawer .acts { display:flex; gap:8px; margin-top:18px; flex-wrap:wrap; }
.drawer .acts .spacer { flex:1; }
.err { color:var(--bad); font-size:12.5px; margin-top:10px; min-height:1em; }

.drawer.wide { width:min(520px,100%); }
.addbox { border:1px solid var(--border); border-radius:12px; padding:12px 13px 2px;
  background:var(--plane); margin-bottom:14px; }
.addbox .row { display:flex; gap:8px; }
.addbox .row input { flex:1; }
.optrow { display:flex; align-items:center; gap:8px; padding:8px 2px;
  border-bottom:1px solid var(--border); }
.optrow .nm { flex:1; font-size:13.5px; min-width:0; overflow-wrap:anywhere; }
.optrow .used { font-size:11.5px; color:var(--muted); white-space:nowrap; }
.optrow .btn { padding:5px 9px; font-size:12px; }
.optrow .parent { font-size:11.5px; color:var(--muted); }
.grouphd { font-size:11px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--muted); font-weight:650; margin:16px 0 2px; }

.toast { position:fixed; left:50%; bottom:18px; transform:translateX(-50%) translateY(140%);
  z-index:100; background:var(--ink); color:var(--plane); border-radius:10px;
  padding:10px 16px; font-size:13px; font-weight:600; box-shadow:var(--shadow);
  transition:transform .18s ease; max-width:90vw; }
.toast.on { transform:translateX(-50%) translateY(0); }
.toast.bad { background:var(--bad); color:#fff; }
.ro { font-size:12px; color:var(--muted); }

.empty { padding:34px 8px; text-align:center; color:var(--muted); font-size:13px; }
.note { background:var(--surface); border:1px dashed var(--axis); border-radius:12px;
  padding:14px 16px; color:var(--ink2); font-size:13px; margin-bottom:14px; }
.note b { color:var(--ink); }
/* ---------- the simple front page ---------- */
.simple { background:var(--surface); border:1px solid var(--border); border-radius:16px;
  padding:22px 22px 20px; box-shadow:var(--shadow); margin-bottom:14px; }
.simple .head { font-size:13px; color:var(--muted); margin-bottom:2px; }
.simple .hero { font-size:clamp(38px,9vw,60px); font-weight:700; letter-spacing:-.03em;
  line-height:1.02; margin:2px 0 4px; }
.simple .herosub { font-size:14.5px; color:var(--ink2); margin-bottom:20px; }
.simple .rows { display:flex; flex-direction:column; gap:0; margin-bottom:6px; }
.srow { display:flex; align-items:baseline; gap:12px; padding:13px 0;
  border-bottom:1px solid var(--grid); }
.srow:last-child { border-bottom:0; }
.srow .k { font-size:14.5px; color:var(--ink2); }
.srow .v { margin-left:auto; font-size:20px; font-weight:650; white-space:nowrap;
  font-variant-numeric:tabular-nums; }
.srow .k small { display:block; font-size:12px; color:var(--muted); font-weight:400;
  margin-top:2px; }
.srow.total { border-top:1.5px solid var(--axis); border-bottom:0; margin-top:2px; }
.srow.total .k { font-weight:650; color:var(--ink); font-size:15.5px; }
.srow.total .v { font-size:26px; }
.aside { margin-top:16px; padding:14px 16px; border-radius:12px; background:var(--plane);
  font-size:13.5px; color:var(--ink2); line-height:1.5; }
.aside + .aside { margin-top:9px; }
.aside b { color:var(--ink); }
.aside.warn { border-left:3px solid var(--s3); }
.more { margin-top:18px; width:100%; padding:13px 16px; border-radius:11px;
  border:1px solid var(--border); background:var(--plane); color:var(--ink);
  font:inherit; font-size:14.5px; font-weight:600; cursor:pointer; }
.more:hover { background:var(--raise); }
.backsimple { margin-bottom:14px; }

/* ---------- money control: month bar ---------- */
.monthbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }
.monthbar select { font:inherit; font-size:14px; font-weight:650; color:var(--ink);
  background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:9px 12px; }
.seg { display:inline-flex; background:var(--surface); border:1px solid var(--border);
  border-radius:10px; padding:3px; gap:2px; }
.seg button { border:0; background:transparent; color:var(--ink2); font:inherit;
  font-size:12.5px; font-weight:600; padding:6px 12px; border-radius:8px; cursor:pointer; }
.seg button[aria-pressed="true"] { background:var(--brand); color:var(--surface); }

/* ---------- the three-number strip ---------- */
.three { display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); gap:12px;
  margin-bottom:14px; }
.three .box { background:var(--surface); border:1px solid var(--border); border-radius:14px;
  padding:15px 16px; box-shadow:var(--shadow); border-top:3px solid var(--axis); }
.three .box.a { border-top-color:var(--s3); }
.three .box.b { border-top-color:var(--s1); }
.three .box.c { border-top-color:var(--s2); }
.three .q { font-size:12px; color:var(--muted); margin-bottom:6px; }
.three .n { font-size:30px; font-weight:650; letter-spacing:-.02em; line-height:1.1; }
.three .d { font-size:12px; color:var(--ink2); margin-top:6px; }

/* ---------- P&L ladder ---------- */
.pnl { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
.pnl td { padding:9px 4px; border-bottom:1px solid var(--grid); vertical-align:baseline; }
.pnl td.v { text-align:right; font-weight:600; white-space:nowrap; }
.pnl td.n { font-size:12px; color:var(--muted); text-align:right; white-space:nowrap;
  width:1%; padding-left:14px; }
.pnl tr.sum td { border-top:1.5px solid var(--axis); border-bottom:0; font-weight:700; }
.pnl tr.sum td.v { font-size:19px; }
.pnl tr.big td { border-bottom:0; padding-top:14px; }
.pnl tr.big td.v { font-size:26px; letter-spacing:-.02em; }
.pnl tr.minor td { color:var(--ink2); }
.pnl .lbl b { font-weight:650; }
.pnl .sub { display:block; font-size:11.5px; color:var(--muted); font-weight:400; }
.pos { color:var(--good); } .neg { color:var(--bad); }

/* ---------- payer split ---------- */
.payers { display:flex; flex-direction:column; gap:9px; }
.payer { display:grid; grid-template-columns:minmax(96px,132px) 1fr auto; gap:10px;
  align-items:center; font-size:13px; }
.payer .nm { color:var(--ink2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.payer .track { background:var(--grid); border-radius:5px; height:15px; overflow:hidden; }
.payer .fillbar { height:100%; border-radius:5px; }
.payer .amt { font-variant-numeric:tabular-nums; font-weight:650; white-space:nowrap; }
.payer.click { cursor:pointer; }
.payer.click:hover .nm { color:var(--ink); }
.payer.on .nm { color:var(--ink); font-weight:650; }
.owed { margin-top:14px; padding-top:13px; border-top:1px solid var(--grid);
  font-size:13px; color:var(--ink2); }
.owed b { color:var(--ink); font-size:17px; font-weight:700; }

/* ---------- fact rows ---------- */
.facts { display:grid; grid-template-columns:repeat(auto-fit,minmax(148px,1fr)); gap:2px 18px; }
.fact { padding:9px 0; border-bottom:1px solid var(--grid); }
.fact .k { font-size:11.5px; color:var(--muted); }
.fact .v { font-size:19px; font-weight:650; letter-spacing:-.01em; }
.fact .h { font-size:11.5px; color:var(--ink2); margin-top:1px; }

/* ---------- data health ---------- */
.hbar { height:9px; border-radius:5px; background:var(--grid); overflow:hidden;
  display:flex; margin:4px 0 14px; }
.hbar i { display:block; height:100%; }
.issue { display:grid; grid-template-columns:auto 1fr auto; gap:11px; align-items:start;
  padding:11px 0; border-bottom:1px solid var(--grid); font-size:13px; }
.issue:last-child { border-bottom:0; }
.issue .dot { width:9px; height:9px; border-radius:50%; margin-top:5px; flex:0 0 auto; }
.issue .dot.warn { background:var(--s3); }
.issue .dot.info { background:var(--s2); }
.issue .t { font-weight:650; }
.issue .w { color:var(--muted); font-size:12px; margin-top:2px; }
.issue .f { color:var(--ink2); font-size:12px; margin-top:3px; }
.issue .amt { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.issue .amt b { display:block; font-weight:650; }
.issue .amt span { font-size:11.5px; color:var(--muted); }

@media (max-width:640px) {
  .wrap { padding:14px 12px 56px; }
  .kpi.hero .val { font-size:34px; }
  .card { padding:14px 13px 16px; }
  .three .n { font-size:25px; }
  .pnl tr.big td.v { font-size:22px; }
  .payer { grid-template-columns:minmax(78px,102px) 1fr auto; font-size:12.5px; }
}
@media print { .filters, .views, .iconbtn, .seg { display:none; } .card { break-inside:avoid; } }
"""


JS = r"""
'use strict';
const D = window.__FIN__;
const $ = (s, r) => (r || document).querySelector(s);
const money = n => '₹' + Math.round(n).toLocaleString('en-IN');
const moneyK = n => Math.abs(n) >= 100000 ? '₹' + (n/100000).toFixed(2) + 'L'
                  : Math.abs(n) >= 1000 ? '₹' + (n/1000).toFixed(1) + 'k' : money(n);
const SLOT = { COGS:'--s1', OPEX:'--s2', CAPEX:'--s3', DRAW:'--s4' };
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const slotColor = ct => cssv(SLOT[ct] || '--s2');

const state = { view:'business', range:'month', cat:'', sub:'', biz:'', q:'',
                payer:'', bucket:'', sort:'date', dir:-1,
                month: D.currentMonth, basis:'earned',
                // Opens plain. The full breakdown is one tap away and the choice
                // is remembered, so the page never greets him with a wall of
                // numbers he did not ask for.
                detail:(function(){ try { return localStorage.getItem('ogfin-detail') === '1'; }
                                    catch (e) { return false; } })() };

/* Filters that belong to the row list, not to the month P&L. Cleared together
   whenever the tab changes so a filter can never survive into a tab where its
   control is invisible - that was the "switch tabs, see nothing, hit reset" bug. */
const ROW_FILTERS = ['cat','sub','biz','q','payer','bucket'];
/* Rows pulled up by a "show these entries" button in the health panel. Kept
   apart from the dropdown filters but cleared with them, so it can never
   silently narrow the table either. */
let HFOCUS = null;
function clearRowFilters(){
  for (const k of ROW_FILTERS) state[k] = '';
  HFOCUS = null;
  const q = $('#fq'); if (q) q.value = '';
  const b = $('#fbiz'); if (b) b.value = '';
}

/* ---------------- data helpers ---------------- */
function rowsFor(v){ return (D.data[v] || []).filter(r => r.date); }

function inRange(iso){
  if (state.range === 'all') return true;
  const d = new Date(iso + 'T00:00:00'), now = new Date(D.today + 'T00:00:00');
  if (state.range === 'month') return iso.slice(0,7) === D.today.slice(0,7);
  const days = state.range === '7' ? 7 : 30;
  const from = new Date(now); from.setDate(from.getDate() - (days - 1));
  return d >= from && d <= now;
}

function filtered(){
  const q = state.q.trim().toLowerCase();
  return rowsFor(state.view).filter(r => {
    if (!inRange(r.date)) return false;
    if (state.cat && r.category !== state.cat) return false;
    if (state.sub && (r.subcategory || '') !== state.sub) return false;
    if (state.biz === 'yes' && r.to_business !== true) return false;
    if (state.biz === 'no' && r.to_business === true) return false;
    if (state.payer && (r.payer || '') !== state.payer) return false;
    if (state.bucket && (r.bucket || '') !== state.bucket) return false;
    if (q) {
      const hay = [r.what, r.category, r.subcategory, r.vendor, r.notes, r.payment]
        .filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function sumBy(rows, key){
  const m = new Map();
  for (const r of rows) {
    const k = r[key] || '—';
    m.set(k, (m.get(k) || 0) + r.amount);
  }
  return [...m.entries()].sort((a,b) => b[1] - a[1]);
}

/* ---------------- tooltip ---------------- */
const tip = $('#tip');
function showTip(e, value, label, meta, color){
  tip.textContent = '';
  const v = document.createElement('span'); v.className = 'tv'; v.textContent = value;
  tip.appendChild(v);
  if (label){
    const l = document.createElement('span'); l.className = 'tl';
    if (color){ const i = document.createElement('i'); i.style.background = color; l.appendChild(i); }
    l.appendChild(document.createTextNode(label));      // untrusted text -> textContent
    tip.appendChild(l);
  }
  if (meta){
    const m = document.createElement('span'); m.className = 'tm'; m.textContent = meta;
    tip.appendChild(m);
  }
  tip.classList.add('on');
  const pad = 12, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX, y = e.clientY;
  x = Math.max(w/2 + pad, Math.min(window.innerWidth - w/2 - pad, x));
  if (y - h - pad < 0) y = y + h + pad * 2;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
function hideTip(){ tip.classList.remove('on'); }

/* ---------------- svg helpers ---------------- */
const NS = 'http://www.w3.org/2000/svg';
function el(n, a){ const e = document.createElementNS(NS, n);
  for (const k in (a||{})) e.setAttribute(k, a[k]); return e; }
function txt(n, a, s){ const e = el(n, a); e.textContent = s; return e; }
function niceMax(v){
  if (v <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1,1.25,1.5,2,2.5,3,4,5,7.5,10]) if (p*m >= v) return p*m;
  return p*10;
}

/* ---------------- chart: ranked horizontal bars (one hue) ---------------- */
/* Nominal categories -> every bar takes slot 1. Never a value-ramp: bar length
   already encodes magnitude, so spending the identity channel on it would be
   double-encoding. */
function rankedBars(mount, pairs, opts){
  mount.textContent = '';
  opts = opts || {};
  if (!pairs.length){ mount.appendChild(emptyMsg('Nothing in this slice.')); return; }
  const top = pairs.slice(0, opts.limit || 14);
  // Render 1:1 against the container instead of scaling a fixed viewBox: a 720-wide
  // viewBox squeezed into a 380px phone card renders 11px text at ~6px.
  const w = Math.max(300, Math.min(760, mount.clientWidth || 720));
  const narrow = w < 470;
  const BAR = 22, GAP = narrow ? 12 : 14;
  const LAB = narrow ? 104 : (opts.labelWidth || 150);
  const PADR = narrow ? 56 : 66;
  const maxChars = Math.max(8, Math.floor((LAB - 12) / 6.4));
  const h = top.length * (BAR + GAP) + 6;
  const max = top[0][1] || 1;
  const plot = w - LAB - PADR;
  const svg = el('svg', { viewBox:`0 0 ${w} ${h}`, width:w, height:h, role:'img',
    'aria-label': opts.aria || 'Ranked spending' });
  const color = opts.color || cssv('--s1');

  top.forEach((p, i) => {
    const y = i * (BAR + GAP);
    const bw = Math.max(2, (p[1] / max) * plot);
    const g = el('g', { class:'mark' });

    const name = txt('text', { x:LAB - 10, y:y + BAR/2 + 4, class:'catlabel',
      'text-anchor':'end' },
      p[0].length > maxChars ? p[0].slice(0, maxChars - 1) + '…' : p[0]);
    name.appendChild(txt('title', {}, p[0]));   // full name on hover, never clipped away
    g.appendChild(name);

    // 4px rounded data-end, square at the baseline
    const r = Math.min(4, bw / 2);
    const path = el('path', { d:
      `M${LAB},${y} H${LAB+bw-r} a${r},${r} 0 0 1 ${r},${r} V${y+BAR-r} a${r},${r} 0 0 1 ${-r},${r} H${LAB} Z`,
      fill: color });
    g.appendChild(path);

    g.appendChild(txt('text', { x:LAB + bw + 8, y:y + BAR/2 + 4, class:'barlabel' },
      moneyK(p[1])));

    const hit = el('rect', { x:0, y:y - GAP/2, width:w, height:BAR + GAP, class:'hit',
      tabindex:'0', role:'button', 'aria-label': p[0] + ': ' + money(p[1]) });
    if (opts.onPick){
      hit.style.cursor = 'pointer';
      const pick = () => { hideTip(); opts.onPick(p[0]); };
      hit.addEventListener('click', pick);
      hit.addEventListener('keydown', ev => {
        if (ev.key === 'Enter' || ev.key === ' '){ ev.preventDefault(); pick(); }
      });
    }
    const show = e => { svg.classList.add('hovered'); g.classList.add('on');
      showTip(e, money(p[1]), p[0],
        opts.meta ? opts.meta(p) : (opts.onPick ? 'Click to filter' : ''), color); };
    const hide = () => { svg.classList.remove('hovered'); g.classList.remove('on'); hideTip(); };
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('focus', e => {
      const b = hit.getBoundingClientRect();
      show({ clientX:b.left + b.width/3, clientY:b.top + b.height/2 });
    });
    hit.addEventListener('blur', hide);
    g.appendChild(hit);
    svg.appendChild(g);
  });
  mount.appendChild(svg);
  if (pairs.length > top.length){
    const more = document.createElement('p');
    more.className = 'hint'; more.style.margin = '10px 0 0';
    more.textContent = (pairs.length - top.length) + ' more in the table below.';
    mount.appendChild(more);
  }
}

/* ---------------- chart: daily spend (single-series area + line) ---------------- */
function dailyChart(mount, rows){
  mount.textContent = '';
  if (!rows.length){ mount.appendChild(emptyMsg('Nothing in this slice.')); return; }
  const byDay = new Map();
  for (const r of rows) byDay.set(r.date, (byDay.get(r.date) || 0) + r.amount);
  const days = [...byDay.keys()].sort();
  // Fill gaps so a quiet day reads as zero, not as a missing point.
  // Format from LOCAL date parts — toISOString() converts to UTC, which in IST
  // (+05:30) rolls every date back a day and silently shifts the whole series.
  const fmt = d => d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2,'0') + '-' +
    String(d.getDate()).padStart(2,'0');
  const out = [];
  let cur = new Date(days[0] + 'T00:00:00');
  const end = new Date(days[days.length-1] + 'T00:00:00');
  while (cur <= end){
    const iso = fmt(cur);
    out.push([iso, byDay.get(iso) || 0]);
    cur.setDate(cur.getDate() + 1);
  }
  if (out.length === 1) out.push([out[0][0], out[0][1]]);

  const w = Math.max(300, Math.min(940, mount.clientWidth || 720));
  const h = w < 470 ? 180 : 210, L = w < 470 ? 44 : 52, R = 12, T = 14, B = 28;
  const pw = w - L - R, ph = h - T - B;
  const max = niceMax(Math.max(...out.map(d => d[1])));
  const x = i => L + (out.length === 1 ? pw/2 : (i / (out.length - 1)) * pw);
  const y = v => T + ph - (v / max) * ph;
  const color = cssv('--s1');
  const svg = el('svg', { viewBox:`0 0 ${w} ${h}`, width:w, height:h, role:'img',
    'aria-label':'Daily spend' });

  for (let i = 0; i <= 4; i++){
    const gy = T + (ph / 4) * i, v = max - (max / 4) * i;
    svg.appendChild(el('line', { x1:L, x2:w - R, y1:gy, y2:gy, class:'gridline' }));
    svg.appendChild(txt('text', { x:L - 9, y:gy + 4, class:'tick', 'text-anchor':'end' },
      moneyK(v)));
  }
  const line = out.map((d,i) => `${i?'L':'M'}${x(i)},${y(d[1])}`).join(' ');
  svg.appendChild(el('path', { d:`${line} L${x(out.length-1)},${T+ph} L${x(0)},${T+ph} Z`,
    fill:color, opacity:'.10' }));
  svg.appendChild(el('path', { d:line, fill:'none', stroke:color, 'stroke-width':'2',
    'stroke-linejoin':'round', 'stroke-linecap':'round' }));

  const peak = out.reduce((a,b) => b[1] > a[1] ? b : a, out[0]);
  const pi = out.indexOf(peak);
  if (peak[1] > 0){
    svg.appendChild(el('circle', { cx:x(pi), cy:y(peak[1]), r:'4.5', fill:color,
      stroke:cssv('--surface'), 'stroke-width':'2' }));
    // keep the peak label off both the y-axis ticks and the right edge
    svg.appendChild(txt('text',
      { x:Math.max(L + 24, Math.min(x(pi), w - R - 30)),
        y:Math.max(y(peak[1]) - 11, 13),
        class:'barlabel', 'text-anchor':'middle' }, moneyK(peak[1])));
  }

  const step = Math.ceil(out.length / 7);
  out.forEach((d,i) => { if (i % step === 0 || i === out.length-1)
    svg.appendChild(txt('text', { x:x(i), y:h - 8, class:'tick', 'text-anchor':'middle' },
      d[0].slice(8) + '/' + d[0].slice(5,7))); });

  svg.appendChild(el('line', { x1:L, x2:w - R, y1:T + ph, y2:T + ph, class:'axisline' }));

  // crosshair: readers aim at a date, never at a 2px line
  const cross = el('line', { x1:0, x2:0, y1:T, y2:T + ph, class:'axisline', opacity:'0' });
  svg.appendChild(cross);
  const dot = el('circle', { r:'5', fill:color, stroke:cssv('--surface'),
    'stroke-width':'2', opacity:'0' });
  svg.appendChild(dot);
  const grab = el('rect', { x:L, y:T, width:pw, height:ph, class:'hit' });
  grab.addEventListener('pointermove', e => {
    const b = svg.getBoundingClientRect();
    const rel = ((e.clientX - b.left) / b.width) * w;
    let i = Math.round(((rel - L) / pw) * (out.length - 1));
    i = Math.max(0, Math.min(out.length - 1, i));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
    cross.setAttribute('opacity', '1');
    dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(out[i][1]));
    dot.setAttribute('opacity', '1');
    const dd = new Date(out[i][0] + 'T00:00:00');
    showTip(e, money(out[i][1]),
      dd.toLocaleDateString('en-IN', { weekday:'short', day:'numeric', month:'short' }),
      '', color);
  });
  grab.addEventListener('pointerleave', () => {
    cross.setAttribute('opacity','0'); dot.setAttribute('opacity','0'); hideTip();
  });
  svg.appendChild(grab);
  mount.appendChild(svg);
}

/* ---------------- chart: cost-type split (stacked, 2px surface gaps) ---------------- */
function costTypeBar(mount, rows){
  mount.textContent = '';
  const order = ['COGS','OPEX','CAPEX','DRAW'];
  const m = new Map();
  for (const r of rows) m.set(r.cost_type, (m.get(r.cost_type) || 0) + r.amount);
  const parts = order.filter(k => m.get(k) > 0).map(k => [k, m.get(k)]);
  if (!parts.length){ mount.appendChild(emptyMsg('Nothing in this slice.')); return; }
  const total = parts.reduce((a,b) => a + b[1], 0);

  const leg = document.createElement('ul');
  leg.className = 'legend';
  for (const [k,v] of parts){
    const li = document.createElement('li');
    const i = document.createElement('i'); i.style.background = slotColor(k);
    li.appendChild(i);
    li.appendChild(document.createTextNode(
      k + ' · ' + money(v) + ' (' + Math.round(v/total*100) + '%)'));
    leg.appendChild(li);
  }
  mount.appendChild(leg);

  const w = Math.max(300, Math.min(760, mount.clientWidth || 720)), h = 46, GAP = 2;
  const svg = el('svg', { viewBox:`0 0 ${w} ${h}`, width:w, height:h, role:'img',
    'aria-label':'Cost type split' });
  let cx = 0;
  parts.forEach(([k,v], idx) => {
    const raw = (v / total) * w;
    const bw = Math.max(3, raw - (idx < parts.length - 1 ? GAP : 0));
    const first = idx === 0, last = idx === parts.length - 1;
    const r = 4;
    let d;
    if (first && last) d = `M${cx+r},0 H${cx+bw-r} a${r},${r} 0 0 1 ${r},${r} V${h-r} a${r},${r} 0 0 1 ${-r},${r} H${cx+r} a${r},${r} 0 0 1 ${-r},${-r} V${r} a${r},${r} 0 0 1 ${r},${-r} Z`;
    else if (first) d = `M${cx+r},0 H${cx+bw} V${h} H${cx+r} a${r},${r} 0 0 1 ${-r},${-r} V${r} a${r},${r} 0 0 1 ${r},${-r} Z`;
    else if (last) d = `M${cx},0 H${cx+bw-r} a${r},${r} 0 0 1 ${r},${r} V${h-r} a${r},${r} 0 0 1 ${-r},${r} H${cx} Z`;
    else d = `M${cx},0 H${cx+bw} V${h} H${cx} Z`;
    const g = el('g', { class:'mark' });
    g.appendChild(el('path', { d, fill:slotColor(k) }));

    // only label inside the segment when the text genuinely fits
    const label = Math.round(v/total*100) + '%';
    if (bw > 46){
      g.appendChild(txt('text', { x:cx + bw/2, y:h/2 + 4, 'text-anchor':'middle',
        fill:'#fff', 'font-size':'12', 'font-weight':'650' }, label));
    }
    const hit = el('rect', { x:cx, y:0, width:bw, height:h, class:'hit',
      tabindex:'0', role:'button', 'aria-label': k + ': ' + money(v) });
    const show = e => { svg.classList.add('hovered'); g.classList.add('on');
      showTip(e, money(v), k, Math.round(v/total*100) + '% of spend', slotColor(k)); };
    const hide = () => { svg.classList.remove('hovered'); g.classList.remove('on'); hideTip(); };
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('focus', () => { const b = hit.getBoundingClientRect();
      show({ clientX:b.left + b.width/2, clientY:b.top + b.height/2 }); });
    hit.addEventListener('blur', hide);
    g.appendChild(hit);
    svg.appendChild(g);
    cx += raw;
  });
  mount.appendChild(svg);
}

function emptyMsg(s){ const p = document.createElement('p'); p.className = 'empty';
  p.textContent = s; return p; }

/* ---------------- editing ---------------- */
/* Only live when served by serve_dashboard.py. Opened as a plain file the page stays
   read-only, because a file:// page cannot reach Notion (no CORS) and must not hold a
   token. EDIT.on gates every affordance so the read-only file never shows a dead button. */
const EDIT = { on:false, schema:null, row:null };
const sel = new Set();

const FIELD_LABEL = {
  what:'Item', date:'Date', amount:'Amount ₹', category:'Category',
  subcategory:'Subcategory', vendor:'Vendor', payment:'Payment method',
  paid_from:'Paid from', spread:'Spread over months', orders_covered:'Orders covered',
  to_business:'Goes to OG Gainz', notes:'Notes'
};
const FIELD_PROP = {
  category:'Category', subcategory:'Subcategory', payment:'Payment Method',
  paid_from:'Paid From', to_business:'Goes To Business'
};

async function initEdit(){
  if (!location.protocol.startsWith('http')) return;
  try {
    const r = await fetch('api/schema');
    if (!r.ok) return;
    const j = await r.json();
    EDIT.schema = j.schema || {};
    EDIT.on = true;
    LISTS.meta = j;
    $('#manage').style.display = '';
    $('#tblhint').textContent =
      'Click any row to fix it. Tick several to change them together. ' +
      'Saves straight to Notion.';
  } catch (e) { /* served without the API — stay read-only */ }
}

function toast(msg, bad){
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('bad', !!bad);
  t.classList.add('on');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('on'), bad ? 5200 : 2600);
}

function optionsFor(field, db, category){
  // Stays null on a read-only page (no API, or the fetch failed), so never index it raw.
  const schema = EDIT.schema || {};
  if (field === 'subcategory'){
    const map = (D.pairs || {})[db] || {};
    if (category && map[category]) return map[category];
    if (category) return [];                       // category has no subcategories
    return (schema[db] || {})['Subcategory'] || [];
  }
  if (field === 'to_business') return ['Yes','No'];
  const prop = FIELD_PROP[field];
  return prop ? ((schema[db] || {})[prop] || []) : [];
}

/* ---- single-row drawer ---- */
function openEditor(row){
  EDIT.row = row;
  $('#drawerwho').textContent =
    (row.db === 'business' ? 'OG Gainz' : row.db === 'personal' ? 'Personal' : 'Appa') +
    ' · ' + row.date;
  $('#drawererr').textContent = '';
  const box = $('#drawerfields'); box.textContent = '';

  const fields = (D.editFields || {})[row.db] || [];
  for (const f of fields){
    const wrap = document.createElement('div'); wrap.className = 'fld';
    const lab = document.createElement('label');
    lab.textContent = FIELD_LABEL[f] || f;
    lab.htmlFor = 'f_' + f;
    wrap.appendChild(lab);

    let input;
    if (FIELD_PROP[f]){
      input = document.createElement('select');
      const cat = f === 'subcategory' ? (row.category || '') : null;
      const opts = optionsFor(f, row.db, cat);
      const blank = document.createElement('option');
      blank.value = ''; blank.textContent = '— none —';
      input.appendChild(blank);
      for (const o of opts){
        const el2 = document.createElement('option');
        el2.value = o; el2.textContent = o;
        input.appendChild(el2);
      }
      let cur = row[f];
      if (f === 'to_business') cur = row.to_business === true ? 'Yes'
                                   : row.to_business === false ? 'No' : '';
      if (cur && !opts.includes(cur)){
        // keep an out-of-list current value visible rather than silently blanking it
        const el2 = document.createElement('option');
        el2.value = cur; el2.textContent = cur + '  (not a standard option)';
        input.appendChild(el2);
      }
      input.value = cur || '';
    } else if (f === 'notes'){
      input = document.createElement('textarea');
      input.value = row.notes || '';
    } else {
      input = document.createElement('input');
      input.type = f === 'date' ? 'date'
                 : (f === 'amount' || f === 'spread' || f === 'orders_covered')
                   ? 'number' : 'text';
      if (f === 'amount'){ input.step = '0.01'; input.min = '0'; }
      if (f === 'spread' || f === 'orders_covered'){ input.step = '1'; input.min = '0'; }
      const v = row[f];
      input.value = (v === null || v === undefined) ? '' : v;
    }
    input.id = 'f_' + f;
    input.dataset.field = f;
    wrap.appendChild(input);
    box.appendChild(wrap);
  }

  // Changing category re-stocks the subcategory list, so an invalid pair can't be saved.
  const catSel = box.querySelector('[data-field="category"]');
  const subSel = box.querySelector('[data-field="subcategory"]');
  if (catSel && subSel){
    catSel.addEventListener('change', () => {
      const opts = optionsFor('subcategory', row.db, catSel.value);
      subSel.textContent = '';
      const blank = document.createElement('option');
      blank.value = ''; blank.textContent = opts.length ? '— none —'
                                                        : '— none for this category —';
      subSel.appendChild(blank);
      for (const o of opts){
        const e2 = document.createElement('option'); e2.value = o; e2.textContent = o;
        subSel.appendChild(e2);
      }
      subSel.value = '';
    });
  }

  $('#scrim').classList.add('on');
  $('#drawer').classList.add('on');
  const first = box.querySelector('input,select,textarea');
  if (first) first.focus();
}

function closeEditor(){
  $('#scrim').classList.remove('on');
  $('#drawer').classList.remove('on');
  EDIT.row = null;
}

function collectChanges(row){
  const changes = {};
  $('#drawerfields').querySelectorAll('[data-field]').forEach(inp => {
    const f = inp.dataset.field;
    let v = inp.value;
    if (f === 'to_business'){
      const now = v === 'Yes' ? true : v === 'No' ? false : null;
      if (now !== (row.to_business === undefined ? null : row.to_business))
        changes[f] = now;
      return;
    }
    if (inp.type === 'number'){
      const now = v === '' ? null : Number(v);
      const was = (row[f] === undefined || row[f] === null) ? null : Number(row[f]);
      if (now !== was) changes[f] = now;
      return;
    }
    const was = (row[f] === null || row[f] === undefined) ? '' : String(row[f]);
    if (v !== was) changes[f] = v;
  });
  return changes;
}

async function saveEditor(){
  const row = EDIT.row; if (!row) return;
  const changes = collectChanges(row);
  if (!Object.keys(changes).length){ closeEditor(); return; }
  const btn = $('#dsave'); btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch('api/update', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ id: row.id, db: row.db, changes })
    });
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
    applyLocal(row, changes);
    closeEditor();
    render();
    toast('Saved to Notion');
  } catch (e){
    $('#drawererr').textContent = e.message;
    toast('Not saved — ' + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = 'Save to Notion';
  }
}

/* The model's derived fields, recomputed in the browser.

   model.py owns the real logic; this mirrors just enough of it that an edit shows
   up immediately instead of waiting for the next rebuild. The maps come from the
   server in the payload so the two can't drift apart. */
function deriveRow(row){
  if (row.db !== 'business') return row;
  const cat = row.category || '';
  let bucket = row.cost_type || cat.split(' · ')[0] || 'OPEX';
  const hay = ((row.what || '') + ' ' + (row.notes || '')).toLowerCase();
  if ((bucket === 'OPEX' || bucket === 'COGS')
      && ((D.financingCategories || []).includes(cat)
          || (D.financingWords || []).some(w => hay.includes(w)))) bucket = 'FINANCING';
  if (!['COGS','OPEX','CAPEX','DRAW','FINANCING'].includes(bucket)) bucket = 'OPEX';
  row.bucket = bucket;
  const p = (row.paid_from || '').trim();
  row.payer = p ? ((D.payerMap || {})[p.toLowerCase()] || p) : 'Not set';
  const sp = Number(row.spread) > 1 ? Number(row.spread) : 1;
  row.spread_months = sp;
  row.monthly_cost = Math.round(((row.amount || 0) / sp) * 100) / 100;
  return row;
}

/* Mirror the write locally so the page reflects reality without a full rebuild. */
function applyLocal(row, changes){
  Object.assign(row, changes);
  if ('category' in changes && row.db === 'business'){
    row.cost_type = (row.category || '').split(' · ')[0] || row.cost_type;
  }
  if ('amount' in changes) row.amount = Number(row.amount) || 0;
  deriveRow(row);
}

async function archiveRows(ids){
  const r = await fetch('api/archive', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ ids })
  });
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  const gone = new Set(j.done || ids);
  for (const k of ['business','personal','dad']){
    D.data[k] = (D.data[k] || []).filter(x => !gone.has(x.id));
  }
  return j;
}

/* ---- bulk ---- */
/* Only offer fields the current sheet actually has — appa's has no payment method or
   subcategory, and only appa's has the business flag. */
const BULK_FIELDS = {
  business: ['category','subcategory','payment','paid_from'],
  personal: ['category','subcategory','payment'],
  dad: ['category','to_business'],
};
function syncBulkFields(){
  const box = $('#bulkfield');
  const want = BULK_FIELDS[state.view] || ['category'];
  const keep = box.value;
  box.textContent = '';
  for (const f of want){
    const o = document.createElement('option');
    o.value = f; o.textContent = 'Set ' + (FIELD_LABEL[f] || f).toLowerCase();
    box.appendChild(o);
  }
  box.value = want.includes(keep) ? keep : want[0];
}
function syncBulk(){
  const bar = $('#bulkbar');
  bar.classList.toggle('on', EDIT.on && sel.size > 0);
  $('#bulkn').textContent = sel.size + ' selected';
  // Read-only: the bar is hidden and there is no schema to build its dropdowns from.
  // Switching tabs calls this, so it must never be able to throw and skip the render.
  if (!EDIT.on) return;
  syncBulkFields();
  syncBulkValues();
}
function syncBulkValues(){
  const field = $('#bulkfield').value;
  const box = $('#bulkvalue');
  box.textContent = '';
  // Bulk-setting a subcategory across mixed categories can't be validated safely,
  // so offer the union only when every picked row shares one category.
  let cat = null;
  if (field === 'subcategory'){
    const cats = new Set([...sel].map(id => findRow(id)).filter(Boolean)
      .map(r => r.category));
    cat = cats.size === 1 ? [...cats][0] : null;
  }
  const opts = optionsFor(field, state.view, cat);
  if (field === 'subcategory' && !cat && opts.length === 0){
    const o = document.createElement('option');
    o.value = ''; o.textContent = 'pick rows of one category first';
    box.appendChild(o); box.disabled = true; return;
  }
  box.disabled = false;
  const blank = document.createElement('option');
  blank.value = ''; blank.textContent = '— clear —';
  box.appendChild(blank);
  for (const o of opts){
    const e2 = document.createElement('option'); e2.value = o; e2.textContent = o;
    box.appendChild(e2);
  }
}
function findRow(id){
  for (const k of ['business','personal','dad']){
    const r = (D.data[k] || []).find(x => x.id === id);
    if (r) return r;
  }
  return null;
}

async function applyBulk(){
  const field = $('#bulkfield').value;
  let value = $('#bulkvalue').value || null;
  if (field === 'to_business') value = value === 'Yes' ? true : value === 'No' ? false : null;
  const ids = [...sel];
  const btn = $('#bulkapply'); btn.disabled = true; btn.textContent = 'Saving…';
  try {
    const r = await fetch('api/bulk', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ ids, db: state.view, changes: { [field]: value } })
    });
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    (j.done || []).forEach(id => { const row = findRow(id);
      if (row) applyLocal(row, { [field]: value }); });
    sel.clear(); syncBulk(); render();
    if (j.failed && j.failed.length)
      toast(j.done.length + ' updated, ' + j.failed.length + ' failed', true);
    else toast(j.done.length + ' rows updated in Notion');
  } catch (e){
    toast('Not saved — ' + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = 'Apply';
  }
}

/* ---------------- manage lists ---------------- */
const LISTS = { db:'business', prop:'Category', data:null, meta:null };

async function api(path, body){
  const r = await fetch(path, body ? {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)
  } : undefined);
  const j = await r.json().catch(() => ({ error:'bad response' }));
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

function fillSel(sel, values, current, labels){
  sel.textContent = '';
  values.forEach((v, i) => {
    const o = document.createElement('option');
    o.value = v; o.textContent = labels ? labels[i] : v;
    sel.appendChild(o);
  });
  if (current && values.includes(current)) sel.value = current;
}

async function openLists(startDb){
  LISTS.db = startDb || state.view;
  if (!(LISTS.meta && LISTS.meta.lists)) {
    try { LISTS.meta = await api('api/schema'); }
    catch (e) { toast('Could not load lists — ' + e.message, true); return; }
  }
  const sheets = Object.keys(LISTS.meta.lists).filter(k => (D.data[k] || []).length ||
                                                           k === 'business');
  fillSel($('#lsheet'), sheets, LISTS.db,
          sheets.map(k => k === 'business' ? 'OG Gainz' : k === 'personal' ? 'Personal'
                                                                          : 'Appa'));
  LISTS.db = $('#lsheet').value;
  fillSel($('#lcls'), LISTS.meta.classes || [], null);
  $('#scrim').classList.add('on');
  $('#listdrawer').classList.add('on');
  await loadLists();
}

async function loadLists(){
  const props = (LISTS.meta.lists[LISTS.db] || []).map(p => p[0]);
  const labels = (LISTS.meta.lists[LISTS.db] || []).map(p => p[1]);
  fillSel($('#lprop'), props, LISTS.prop, labels);
  LISTS.prop = $('#lprop').value;
  $('#listwho').textContent = 'Changes here update Notion for every entry.';
  $('#listrows').textContent = 'Loading…';
  $('#listerr').textContent = '';
  try {
    LISTS.data = await api('api/lists?db=' + encodeURIComponent(LISTS.db));
    D.pairs = LISTS.data.pairs || D.pairs;
  } catch (e) {
    $('#listrows').textContent = '';
    $('#listerr').textContent = e.message; return;
  }
  renderLists();
}

function renderLists(){
  const isBizCat = LISTS.db === 'business' && LISTS.prop === 'Category';
  const isSub = LISTS.prop === 'Subcategory';
  $('#lclswrap').style.display = isBizCat ? '' : 'none';
  $('#lparentwrap').style.display = isSub ? '' : 'none';

  const cats = ((LISTS.data.lists || {})['Category'] || {}).options || [];
  if (isSub) fillSel($('#lparent'), [''].concat(cats.map(o => o.name)), null,
                     ['— not attached yet —'].concat(cats.map(o => o.name)));

  const box = $('#listrows'); box.textContent = '';
  const entry = (LISTS.data.lists || {})[LISTS.prop];
  if (!entry){ box.appendChild(emptyMsg('This sheet has no such list.')); return; }

  const pairs = (D.pairs || {})[LISTS.db] || {};
  const parentOf = {};
  for (const c in pairs) for (const s of pairs[c]) parentOf[s] = c;

  const hd = document.createElement('div');
  hd.className = 'grouphd';
  hd.textContent = entry.options.length + ' ' + entry.label.toLowerCase() + ' options';
  box.appendChild(hd);

  for (const o of entry.options){
    const row = document.createElement('div'); row.className = 'optrow';
    const nm = document.createElement('div'); nm.className = 'nm';
    nm.textContent = o.name;
    if (isSub){
      const p = document.createElement('div'); p.className = 'parent';
      p.textContent = parentOf[o.name] ? 'under ' + parentOf[o.name]
                                       : 'not attached to a category';
      nm.appendChild(p);
    }
    const used = document.createElement('span'); used.className = 'used';
    used.textContent = o.used ? o.used + ' used' : 'unused';

    const ren = document.createElement('button');
    ren.className = 'btn'; ren.type = 'button'; ren.textContent = 'Rename';
    ren.addEventListener('click', () => doRename(o));

    const del = document.createElement('button');
    del.className = 'btn danger'; del.type = 'button'; del.textContent = 'Delete';
    del.addEventListener('click', () => doDelete(o, entry));

    row.appendChild(nm); row.appendChild(used); row.appendChild(ren); row.appendChild(del);

    if (isSub){
      const mv = document.createElement('button');
      mv.className = 'btn'; mv.type = 'button'; mv.textContent = 'Move';
      mv.title = 'Put this subcategory under a different category';
      mv.addEventListener('click', () => doMove(o, cats, parentOf[o.name]));
      row.appendChild(mv);
    }
    box.appendChild(row);
  }
}

async function doRename(o){
  const to = prompt(
    'Rename "' + o.name + '" to:\n\n' +
    (o.used ? o.used + ' entr' + (o.used === 1 ? 'y' : 'ies') + ' will move across.\n' : '') +
    'Notion cannot rename a dropdown option directly, so this creates the new one, ' +
    'moves every entry, then removes the old one.',
    o.name);
  if (!to || to.trim() === o.name) return;
  try {
    const j = await api('api/option/rename',
      { db:LISTS.db, prop:LISTS.prop, from:o.name, to:to.trim() });
    D.pairs = j.pairs || D.pairs;
    await refreshAfterListChange();
    toast('Renamed' + (j.moved ? ' · ' + j.moved + ' entries moved' : ''));
  } catch (e){ $('#listerr').textContent = e.message; toast(e.message, true); }
}

async function doDelete(o, entry){
  let reassign = null;
  if (o.used){
    const others = entry.options.filter(x => x.name !== o.name).map(x => x.name);
    reassign = prompt(
      '"' + o.name + '" is used by ' + o.used + ' entr' + (o.used === 1 ? 'y' : 'ies') +
      '.\n\nType exactly what to move them to, or Cancel:\n\n' + others.join('\n'));
    if (!reassign) return;
  } else if (!confirm('Delete "' + o.name + '"? Nothing uses it.')) return;
  try {
    const j = await api('api/option/delete',
      { db:LISTS.db, prop:LISTS.prop, name:o.name, reassign_to:reassign });
    D.pairs = j.pairs || D.pairs;
    await refreshAfterListChange();
    toast('Deleted' + (j.moved ? ' · ' + j.moved + ' entries moved' : ''));
  } catch (e){ $('#listerr').textContent = e.message; toast(e.message, true); }
}

async function doMove(o, cats, currentParent){
  const to = prompt('Which category should "' + o.name + '" sit under?\n\n' +
    'Type one exactly, or leave blank to detach:\n\n' + cats.map(c => c.name).join('\n'),
    currentParent || '');
  if (to === null) return;
  try {
    const j = await api('api/pairs', { db:LISTS.db, sub:o.name, parent:to.trim() || null });
    D.pairs = j.pairs || D.pairs;
    renderLists();
    toast(to.trim() ? 'Moved under ' + to.trim() : 'Detached');
  } catch (e){ $('#listerr').textContent = e.message; toast(e.message, true); }
}

async function addOption(){
  const name = $('#lnew').value.trim();
  if (!name){ $('#listerr').textContent = 'Type a name first.'; return; }
  const body = { db:LISTS.db, prop:LISTS.prop, name };
  if (LISTS.db === 'business' && LISTS.prop === 'Category') body.cls = $('#lcls').value;
  if (LISTS.prop === 'Subcategory') body.parent = $('#lparent').value || null;
  const btn = $('#ladd'); btn.disabled = true;
  try {
    const j = await api('api/option/add', body);
    D.pairs = j.pairs || D.pairs;
    $('#lnew').value = '';
    await refreshAfterListChange();
    toast(j.note ? 'Added — ' + j.note : 'Added "' + j.name + '"');
  } catch (e){ $('#listerr').textContent = e.message; toast(e.message, true); }
  finally { btn.disabled = false; }
}

/* Lists changed, so the edit dropdowns and the row data behind them are stale. */
async function refreshAfterListChange(){
  try { EDIT.schema = (await api('api/schema')).schema || EDIT.schema; } catch (e) {}
  await loadLists();
  try {
    const fresh = await api('api/rows');
    if (fresh && fresh.data) {
      // This endpoint returns rows straight from Notion, without the model's
      // derived fields. Re-derive or the payer column and filter go blank.
      (fresh.data.business || []).forEach(deriveRow);
      D.data = fresh.data;
      render();
    }
  } catch (e) { /* rows endpoint optional — charts refresh on next rebuild */ }
}

/* ---------------- table ---------------- */
function renderTable(rows){
  const wrap = $('#tbl'); wrap.textContent = '';
  if (!rows.length){ wrap.appendChild(emptyMsg('No entries match these filters.')); return; }
  const cols = [
    { k:'date', t:'Date', f:r => r.date.slice(8) + ' ' + monShort(r.date) },
    { k:'what', t:'Item', f:r => r.what || '—', cls:'wrapcell' },
    { k:'category', t:'Category', f:r => r.category || '—', pill:true },
  ];
  if (state.view === 'dad'){
    cols.push({ k:'to_business', t:'Goes to OG Gainz',
      f:r => r.to_business === true ? 'Yes' : r.to_business === false ? 'No' : '—' });
  } else {
    cols.push({ k:'subcategory', t:'Sub', f:r => r.subcategory || '—' });
    if (state.view === 'business'){
      cols.push({ k:'payer', t:'Paid by', f:r => r.payer || '—' });
      cols.push({ k:'vendor', t:'Vendor', f:r => r.vendor || '—' });
      cols.push({ k:'monthly_cost', t:'Cost / mo', num:true,
        f:r => (r.spread_months > 1 ? money(r.monthly_cost) : '—') });
    } else {
      cols.push({ k:'payment', t:'Paid by', f:r => r.payment || '—' });
    }
  }
  cols.push({ k:'amount', t:'Amount', f:r => money(r.amount), num:true });
  const t = document.createElement('table');
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  if (EDIT.on){
    const th = document.createElement('th'); th.className = 'pickcell';
    const all = document.createElement('input');
    all.type = 'checkbox';
    all.setAttribute('aria-label', 'Select every visible row');
    // Bulk edits post one db for the whole selection, so only rows that really
    // live in this tab's database may be selected. Appa's rows folded into the
    // OG Gainz view are his - a bulk write would aim them at the wrong database.
    const bulkable = rows.filter(r => r.db === state.view);
    all.checked = bulkable.length > 0 && bulkable.every(r => sel.has(r.id));
    all.disabled = bulkable.length === 0;
    all.addEventListener('change', () => {
      bulkable.forEach(r => all.checked ? sel.add(r.id) : sel.delete(r.id));
      syncBulk(); renderTable(rows);
    });
    th.appendChild(all); hr.appendChild(th);
  }
  for (const c of cols){
    const th = document.createElement('th');
    th.className = c.num ? 'num' : '';
    th.setAttribute('role','button'); th.tabIndex = 0;
    th.textContent = c.t + (state.sort === c.k ? (state.dir === 1 ? '  ↑' : '  ↓') : '');
    const go = () => { if (state.sort === c.k) state.dir *= -1;
      else { state.sort = c.k; state.dir = c.k === 'amount' || c.k === 'date' ? -1 : 1; }
      render(); };
    th.addEventListener('click', go);
    th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ')
      { e.preventDefault(); go(); } });
    hr.appendChild(th);
  }
  thead.appendChild(hr); t.appendChild(thead);

  const tb = document.createElement('tbody');
  const sorted = [...rows].sort((a,b) => {
    const k = state.sort;
    let va = a[k], vb = b[k];
    if (k === 'amount'){ va = va || 0; vb = vb || 0; return (va - vb) * state.dir; }
    va = (va || '').toString().toLowerCase(); vb = (vb || '').toString().toLowerCase();
    return va < vb ? -state.dir : va > vb ? state.dir : 0;
  });
  for (const r of sorted){
    const tr = document.createElement('tr');
    if (EDIT.on){
      tr.classList.add('editable');
      if (sel.has(r.id)) tr.classList.add('picked');
      const td = document.createElement('td'); td.className = 'pickcell';
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = sel.has(r.id);
      cb.setAttribute('aria-label', 'Select ' + (r.what || 'row'));
      if (r.db !== state.view){
        cb.disabled = true;
        cb.title = 'On Appa’s sheet — open it to edit, or use the Appa tab';
      }
      cb.addEventListener('click', ev => ev.stopPropagation());
      cb.addEventListener('change', () => {
        cb.checked ? sel.add(r.id) : sel.delete(r.id);
        tr.classList.toggle('picked', cb.checked);
        syncBulk();
      });
      td.appendChild(cb); tr.appendChild(td);
      tr.tabIndex = 0;
      tr.addEventListener('click', () => openEditor(r));
      tr.addEventListener('keydown', ev => {
        if (ev.key === 'Enter'){ ev.preventDefault(); openEditor(r); }
      });
    }
    for (const c of cols){
      const td = document.createElement('td');
      td.className = (c.num ? 'num ' : '') + (c.cls || '');
      if (c.pill && r.cost_type){
        const s = document.createElement('span'); s.className = 'pill';
        const i = document.createElement('i'); i.style.background = slotColor(r.cost_type);
        s.appendChild(i);
        s.appendChild(document.createTextNode(c.f(r)));   // untrusted -> text node
        td.appendChild(s);
      } else {
        td.textContent = c.f(r);
      }
      if (c.k === 'what' && r.notes) td.title = r.notes;
      tr.appendChild(td);
    }
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  wrap.appendChild(t);
}
function monShort(iso){
  return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    [parseInt(iso.slice(5,7),10) - 1];
}

/* ---------------- kpis ---------------- */
function renderKPIs(rows){
  const k = $('#kpis'); k.textContent = '';
  const total = rows.reduce((a,r) => a + r.amount, 0);
  const days = new Set(rows.map(r => r.date)).size || 1;
  const cats = sumBy(rows, 'category');
  const tiles = [];

  tiles.push({ lab: labelFor() + ' · ' + rangeLabel(), val: money(total), hero:true,
    sub: rows.length + ' entries across ' + days + (days === 1 ? ' day' : ' days') });
  tiles.push({ lab:'Average per active day', val: money(total / days) });
  if (cats.length) tiles.push({ lab:'Biggest category', val: moneyK(cats[0][1]),
    sub: cats[0][0] });

  if (state.view === 'dad'){
    // Appa buys for the kitchen and the house out of one pocket. The split is the
    // whole point of this view, so lead with it.
    const toBiz = rows.filter(r => r.to_business === true)
                      .reduce((a,r) => a + r.amount, 0);
    const unset = rows.filter(r => r.to_business === null || r.to_business === undefined).length;
    tiles.push({ lab:'Of that, goes to OG Gainz', val: money(toBiz),
      sub: total ? Math.round(toBiz/total*100) + '% of appa’s spend' : '' });
    tiles.push({ lab:'Stays at home', val: money(total - toBiz),
      sub: unset ? unset + ' row(s) not marked yet' : 'every row marked' });
  }

  if (state.view === 'business'){
    const rev = D.revenue;
    if (rev && rev.recognised) {
      tiles.push({ lab:'Earned revenue · ' + rev.month, val: moneyK(rev.recognised),
        sub: rev.delivered + ' meals delivered' });
    }
    const del = rows.filter(r => r.category === 'COGS · Delivery');
    if (del.length){
      const spend = del.reduce((a,r) => a + r.amount, 0);
      const orders = del.reduce((a,r) => a + (r.orders_covered || 0), 0);
      tiles.push({ lab:'Delivery cost', val: moneyK(spend),
        sub: orders ? money(spend/orders) + ' per order delivered'
                    : del.length + ' runs · orders not logged' });
    }
  }

  for (const t of tiles){
    const d = document.createElement('div');
    d.className = 'kpi' + (t.hero ? ' hero' : '');
    const l = document.createElement('div'); l.className = 'lab'; l.textContent = t.lab;
    const v = document.createElement('div'); v.className = 'val'; v.textContent = t.val;
    d.appendChild(l); d.appendChild(v);
    if (t.sub){ const s = document.createElement('div'); s.className = 'sub';
      s.textContent = t.sub; d.appendChild(s); }
    k.appendChild(d);
  }
}
function labelFor(){
  return state.view === 'business' ? 'OG Gainz spend'
       : state.view === 'personal' ? 'My spend' : "Appa's spend";
}
function rangeLabel(){
  return state.range === 'month' ? 'this month'
       : state.range === '7' ? 'last 7 days'
       : state.range === '30' ? 'last 30 days' : 'all time';
}

/* ---------------- filter options ---------------- */
function refreshOptions(){
  const base = rowsFor(state.view).filter(r => inRange(r.date));
  fill($('#fcat'), [...new Set(base.map(r => r.category).filter(Boolean))].sort(),
       state.cat, 'All categories', 'cat');
  const subBase = state.cat ? base.filter(r => r.category === state.cat) : base;
  fill($('#fsub'), [...new Set(subBase.map(r => r.subcategory).filter(Boolean))].sort(),
       state.sub, 'All subcategories', 'sub');
  const payers = [...new Set(base.map(r => r.payer).filter(Boolean))]
    .sort((a,b) => (D.payerOrder.indexOf(a) + 99) % 99 - (D.payerOrder.indexOf(b) + 99) % 99);
  fill($('#fpayer'), payers, state.payer, 'Anyone paid', 'payer');
  fill($('#fbucket'), D.bucketOrder.filter(b => base.some(r => r.bucket === b)),
       state.bucket, 'All cost types', 'bucket');
}
function fill(sel, values, current, allLabel, key){
  sel.textContent = '';
  const o0 = document.createElement('option'); o0.value = ''; o0.textContent = allLabel;
  sel.appendChild(o0);
  let found = false;
  for (const v of values){
    const o = document.createElement('option'); o.value = v; o.textContent = v;
    if (v === current){ o.selected = true; found = true; }
    sel.appendChild(o);
  }
  // The chosen value no longer exists in this tab or date range. Blanking only the
  // dropdown used to leave state.<key> still filtering - an invisible filter that
  // emptied the table with no way to see why. Drop the state with it.
  if (!found && current){ sel.value = ''; if (key) state[key] = ''; }
}


/* =================================================================
   MONEY CONTROL
   The panels above the filter row answer three different questions that
   were previously mashed into one "total spend" number:
     what left my pockets  /  what the month really cost  /  what I still own
   They are scoped to ONE MONTH, deliberately. "Operating profit for the last
   7 days" is not a thing, so these never listen to the row filters below.
   ================================================================= */

function scrollToTable(){
  const t = $('#tbl');
  // Guarded: not every mobile browser ships scrollIntoView options, and a
  // failed scroll must never take the click handler down with it.
  if (t && typeof t.scrollIntoView === 'function'){
    try { t.scrollIntoView({ behavior:'smooth', block:'start' }); }
    catch (e) { t.scrollIntoView(); }
  }
}

function sumNow(){ return (D.summaries || {})[state.month] || null; }
function pct(n){ return (n === null || n === undefined) ? '—' : Math.round(n) + '%'; }
function signed(n){ return (n < 0 ? '−' : '') + money(Math.abs(n)); }

function incomeFor(s){
  if (state.basis === 'billed') return { v: s.billed_revenue,
    note: 'what you invoiced in ' + s.month + ', whenever it gets delivered' };
  if (state.basis === 'cash')   return { v: s.received,
    note: 'money that actually reached you — ' + money(s.outstanding) + ' still owed' };
  return { v: s.income,
    note: s.meals + ' meals delivered, valued at their plan price, + fees + trials' };
}

/* ---------- the three numbers ---------- */
function renderThree(s){
  const box = $('#three'); box.textContent = '';
  const invested = s.capex_cash;
  const items = [
    { cls:'a', q:'Left your pockets in ' + s.month, n: money(s.cash_out),
      d: invested ? money(invested) + ' of that was equipment you still own — '
                    + 'not a running cost'
                  : 'every rupee logged as going out this month' },
    { cls:'b', q:'What the month actually cost to run', n: money(s.operating_cost),
      d: 'ingredients + running costs, with bulk stock spread over the months it lasts'
         + (s.financing ? '. Scooter EMI (' + money(s.financing) + ') sits on its own line.' : '') },
    { cls:'c', q:'Still yours — invested, not spent', n: money(invested),
      d: invested ? 'equipment and setup. It leaves the P&L; only '
                    + money(s.depreciation) + '/mo of wear is a real cost'
                  : 'nothing bought this month' },
  ];
  for (const it of items){
    const d = document.createElement('div'); d.className = 'box ' + it.cls;
    const q = document.createElement('div'); q.className = 'q'; q.textContent = it.q;
    const n = document.createElement('div'); n.className = 'n'; n.textContent = it.n;
    const s2 = document.createElement('div'); s2.className = 'd'; s2.textContent = it.d;
    d.append(q, n, s2); box.appendChild(d);
  }
}

/* ---------- P&L ladder ---------- */
function pnlRow(tb, label, sub, value, opts){
  opts = opts || {};
  const tr = document.createElement('tr');
  if (opts.cls) tr.className = opts.cls;
  const td = document.createElement('td'); td.className = 'lbl';
  const b = document.createElement('b'); b.textContent = label; td.appendChild(b);
  if (sub){ const sp = document.createElement('span'); sp.className = 'sub';
    sp.textContent = sub; td.appendChild(sp); }
  const tv = document.createElement('td'); tv.className = 'v';
  if (value === null || value === undefined) tv.textContent = '—';
  else {
    tv.textContent = (opts.minus ? '− ' : '') + money(Math.abs(value));
    if (opts.colour) tv.classList.add(value >= 0 ? 'pos' : 'neg');
  }
  const tn = document.createElement('td'); tn.className = 'n';
  tn.textContent = opts.note || '';
  tr.append(td, tv, tn); tb.appendChild(tr);
}

function renderPnl(s){
  const inc = incomeFor(s);
  const ingredients = s.cogs - s.delivery.outbound;
  const gross = inc.v - s.cogs;
  const oper = gross - s.opex;
  const after = oper - s.financing;

  const tb = $('#pnl'); tb.textContent = '';
  pnlRow(tb, 'Money in', inc.note, inc.v);
  pnlRow(tb, 'Ingredients, packaging & freight',
         'everything that goes into a box', ingredients,
         { minus:true, cls:'minor',
           note: s.meals ? money(ingredients / s.meals) + '/meal' : '' });
  pnlRow(tb, 'Getting meals to customers',
         'Porter runs + your own fuel', s.delivery.outbound,
         { minus:true, cls:'minor',
           note: s.meals ? money(s.delivery.outbound / s.meals) + '/meal' : '' });
  pnlRow(tb, 'Gross profit', 'what the food itself earns', gross,
         { cls:'sum', colour:true, note: inc.v ? pct(gross / inc.v * 100) + ' margin' : '' });
  pnlRow(tb, 'Running costs', 'staff, subscriptions, consumables, content', s.opex,
         { minus:true, cls:'minor',
           note: s.meals ? money(s.opex / s.meals) + '/meal' : '' });
  pnlRow(tb, 'Operating profit', 'what the business made by operating', oper,
         { cls:'big', colour:true, note: inc.v ? pct(oper / inc.v * 100) + ' margin' : '' });
  if (s.financing){
    pnlRow(tb, 'Scooter EMI', 'a loan repayment, not a cost of cooking', s.financing,
           { minus:true, cls:'minor' });
    pnlRow(tb, 'Left after the EMI', 'the number your bank balance feels', after,
           { cls:'sum', colour:true });
  }

  const bl = $('#belowline'); bl.textContent = '';
  pnlRow(bl, 'Invested in equipment & setup',
         'yours afterwards — deliberately not an expense', s.capex_cash,
         { note: money(s.depreciation) + '/mo of wear' });
  if (s.draw_cash)
    pnlRow(bl, 'Taken out of the business', 'owner draw, never a cost', s.draw_cash);
  $('#belowwrap').style.display = (s.capex_cash || s.draw_cash) ? '' : 'none';
}

/* ---------- cash becomes cost ---------- */
function renderReconcile(s){
  const r = s.reconcile, tb = $('#recon'); tb.textContent = '';
  pnlRow(tb, 'Cash that left your pockets', '', r.cash_out);
  if (r.capex) pnlRow(tb, 'Equipment & setup bought',
    'you still own it', r.capex, { minus:true, cls:'minor' });
  if (r.financing) pnlRow(tb, 'Scooter EMI', 'loan repayment', r.financing,
    { minus:true, cls:'minor' });
  if (r.draw) pnlRow(tb, 'Owner draw', '', r.draw, { minus:true, cls:'minor' });
  if (r.prepaid_out) pnlRow(tb, 'Bulk stock that lasts past this month',
    'tape, packaging, oil — charged to the months it gets used', r.prepaid_out,
    { minus:true, cls:'minor' });
  if (r.carried_in) pnlRow(tb, 'Earlier bulk stock used up this month',
    'bought before, consumed now', r.carried_in, { cls:'minor' });
  pnlRow(tb, 'What running the kitchen cost', '', r.operating_cost, { cls:'sum' });
}

/* ---------- out of whose pocket ---------- */
function renderPayers(s){
  const wrap = $('#payers'); wrap.textContent = '';
  const pairs = D.payerOrder
    .filter(p => s.by_payer[p])
    .map(p => [p, s.by_payer[p]]);
  for (const k in s.by_payer) if (!D.payerOrder.includes(k)) pairs.push([k, s.by_payer[k]]);
  const max = Math.max(...pairs.map(p => p[1]), 1);
  const COL = { 'Me (personal)':'--s1', 'Business bank':'--s2',
                'Appa':'--s3', 'Abishek':'--s4', 'Appa / Abishek':'--s3',
                'Not set':'--axis' };
  for (const [name, amt] of pairs){
    const row = document.createElement('div');
    row.className = 'payer click' + (state.payer === name ? ' on' : '');
    row.tabIndex = 0; row.setAttribute('role', 'button');
    row.setAttribute('aria-label', 'Filter entries paid by ' + name);
    const nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = name;
    const tr = document.createElement('div'); tr.className = 'track';
    const fb = document.createElement('div'); fb.className = 'fillbar';
    fb.style.width = (amt / max * 100) + '%';
    fb.style.background = cssv(COL[name] || '--s2');
    tr.appendChild(fb);
    const am = document.createElement('div'); am.className = 'amt'; am.textContent = money(amt);
    row.append(nm, tr, am);
    const pick = () => {
      state.payer = (state.payer === name ? '' : name);
      const sel = $('#fpayer'); if (sel) sel.value = state.payer;
      render();
      scrollToTable();
    };
    row.addEventListener('click', pick);
    row.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); pick(); } });
    wrap.appendChild(row);
  }
  const o = $('#owed'); o.textContent = '';
  const mine = document.createElement('div');
  mine.innerHTML = '';
  const b1 = document.createElement('b'); b1.textContent = money(s.out_of_pocket);
  mine.appendChild(b1);
  mine.appendChild(document.createTextNode(
    ' came out of your own pocket for the business in ' + s.month + '.'));
  o.appendChild(mine);
  if (s.fronted_by_family){
    const fam = document.createElement('div'); fam.style.marginTop = '5px';
    const b2 = document.createElement('b'); b2.textContent = money(s.fronted_by_family);
    fam.appendChild(b2);
    fam.appendChild(document.createTextNode(' was fronted by Appa / Abishek — money the business owes back.'));
    o.appendChild(fam);
  }
  if (s.payer_unknown){
    const u = document.createElement('div'); u.style.marginTop = '5px';
    u.style.color = 'var(--muted)';
    u.textContent = money(s.payer_unknown) + ' has nobody marked as the payer, so both '
                  + 'numbers above are understated by up to that much.';
    o.appendChild(u);
  }
}

/* ---------- delivery economics ---------- */
function factList(mount, facts){
  mount.textContent = '';
  for (const f of facts){
    if (f.v === null || f.v === undefined) continue;
    const d = document.createElement('div'); d.className = 'fact';
    const k = document.createElement('div'); k.className = 'k'; k.textContent = f.k;
    const v = document.createElement('div'); v.className = 'v'; v.textContent = f.v;
    d.append(k, v);
    if (f.h){ const h = document.createElement('div'); h.className = 'h'; h.textContent = f.h;
      d.appendChild(h); }
    mount.appendChild(d);
  }
}

function renderDelivery(s){
  const d = s.delivery;
  factList($('#delfacts'), [
    { k:'Charged to customers', v: money(d.recovered), h:'delivery fees billed' },
    { k:'Cost to deliver', v: money(d.outbound), h:'Porter runs + your own fuel' },
    { k:'Net on delivery', v: signed(d.net),
      h: d.net >= 0 ? 'fees cover it' : 'you are subsidising delivery' },
    { k:'Porter, per run', v: d.per_porter_run ? money(d.per_porter_run) : '—',
      h: d.porter_runs + ' runs outsourced' },
    { k:'Your own bike', v: d.own_spend ? money(d.own_spend) : '—',
      h: d.own_per_meal ? money(d.own_per_meal) + '/meal across ~' + d.self_delivered + ' meals'
                        : 'no fuel logged' },
    { k:'Average per meal', v: d.per_meal ? money(d.per_meal) : '—',
      h:'all outbound cost ÷ meals delivered' },
  ]);
  const n = $('#delnote'); n.textContent = '';
  if (d.inbound){
    n.textContent = money(d.inbound) + ' of Hyperpure / Instamart / Blinkit freight and '
      + 'handling used to be counted here too. That is the cost of getting ingredients IN, '
      + 'not meals out — it now sits with ingredients, which is why cost-per-delivery '
      + 'finally looks sane.';
  }
}

/* ---------- per meal ---------- */
function renderPerMeal(s){
  const inc = incomeFor(s);
  const rev = s.meals ? inc.v / s.meals : null;
  const profit = s.meals ? (inc.v - s.cogs - s.opex) / s.meals : null;
  factList($('#mealfacts'), [
    { k:'Meals delivered', v: s.meals ? String(s.meals) : '—' },
    { k:'Revenue per meal', v: rev ? money(rev) : '—' },
    { k:'Food + delivery cost', v: s.cost_per_meal ? money(s.cost_per_meal) : '—',
      h:'the true cost of one box' },
    { k:'Share of running costs', v: s.opex_per_meal ? money(s.opex_per_meal) : '—' },
    { k:'Profit per meal', v: profit ? signed(profit) : '—',
      h: (rev && profit) ? pct(profit / rev * 100) + ' of what you charge' : '' },
  ]);
}

/* ---------- data health ---------- */
function renderHealth(s){
  const h = (D.health || {})[state.month];
  const card = $('#cardHealth');
  if (!h){ card.style.display = 'none'; return; }
  card.style.display = '';
  const bar = $('#hbar'); bar.textContent = '';
  const clean = Math.max(0, h.tracked - h.at_risk);
  for (const [v, c] of [[clean, '--s1'], [h.at_risk, '--s3']]){
    if (v <= 0) continue;
    const i = document.createElement('i');
    i.style.width = (v / (h.tracked || 1) * 100) + '%';
    i.style.background = cssv(c);
    bar.appendChild(i);
  }
  $('#hsum').textContent =
    money(h.tracked) + ' logged this month. ' + money(h.at_risk)
    + ' of it is missing something that changes a number above'
    + (h.mirrored_count ? ' · ' + h.mirrored_count + ' of Appa\u2019s rows appear on both '
       + 'sheets and are counted once, not twice' : '') + '.';

  const list = $('#hlist'); list.textContent = '';

  /* The only check that can see money you never logged at all: meals went out,
     and those meals had to be made of something. */
  const cc = (sumNow() || {}).cost_check;
  if (cc && cc.material){
    const row = document.createElement('div'); row.className = 'issue';
    const dot = document.createElement('span');
    dot.className = 'dot ' + (cc.direction === 'under' ? 'warn' : 'info');
    const mid = document.createElement('div');
    const t = document.createElement('div'); t.className = 't';
    t.textContent = cc.direction === 'under'
      ? 'Food cost looks too low for the meals you delivered'
      : 'Food cost is running above your costed menu';
    const w = document.createElement('div'); w.className = 'w';
    w.textContent = 'You logged ' + money(cc.logged_per_meal) + ' of ingredients and '
      + 'packaging per meal across ' + cc.meals + ' meals. Your own costing says a meal '
      + 'should cost about ' + money(cc.expected_per_meal) + '.';
    const f = document.createElement('div'); f.className = 'f';
    f.textContent = cc.direction === 'under'
      ? 'Either roughly ' + money(Math.abs(cc.gap)) + ' of buying never got logged, or '
        + 'you are sourcing cheaper than the costing assumes. Worth knowing which — it '
        + 'is the difference between a real margin and a flattering one.'
      : 'Ingredients are costing more than planned. Check chicken and produce prices '
        + 'against the price book.';
    mid.append(t, w, f);
    const amt = document.createElement('div'); amt.className = 'amt';
    const b = document.createElement('b'); b.textContent = signed(Math.abs(cc.gap));
    const sp = document.createElement('span');
    sp.textContent = cc.direction === 'under' ? 'possibly unlogged' : 'over budget';
    amt.append(b, sp);
    row.append(dot, mid, amt);
    list.appendChild(row);
  }

  if (!h.issues.length && !(cc && cc.material)){
    const ok = document.createElement('div'); ok.className = 'empty';
    ok.textContent = 'Nothing to fix. Every entry has a payer, a category and a lifespan.';
    list.appendChild(ok); return;
  }
  if (!h.issues.length) return;
  for (const i of h.issues){
    const row = document.createElement('div'); row.className = 'issue';
    const dot = document.createElement('span'); dot.className = 'dot ' + i.level;
    const mid = document.createElement('div');
    const t = document.createElement('div'); t.className = 't'; t.textContent = i.title;
    const w = document.createElement('div'); w.className = 'w'; w.textContent = i.why;
    const f = document.createElement('div'); f.className = 'f'; f.textContent = 'Fix: ' + i.fix;
    mid.append(t, w, f);
    if (i.ids && i.ids.length){
      const btn = document.createElement('button');
      btn.className = 'iconbtn'; btn.type = 'button';
      btn.textContent = 'Show these ' + i.n + ' entries';
      btn.addEventListener('click', () => {
        clearRowFilters();               // clears HFOCUS too, so set it after
        HFOCUS = new Set(i.ids);
        state.range = 'month'; $('#frange').value = 'month';
        render();
        scrollToTable();
      });
      mid.appendChild(btn);
    }
    const amt = document.createElement('div'); amt.className = 'amt';
    const b = document.createElement('b'); b.textContent = money(i.amount);
    const sp = document.createElement('span'); sp.textContent = i.n + (i.n === 1 ? ' entry' : ' entries');
    amt.append(b, sp);
    row.append(dot, mid, amt);
    list.appendChild(row);
  }
}

/* ---------- the simple front page ----------
   One question, answered in words he would use himself: did this month make
   money, and what should he do about it. Everything that needs a second look
   lives behind the button at the bottom. */
function renderSimple(){
  const box = $('#simple');
  box.textContent = '';
  const add = (el, cls, txt) => { const d = document.createElement(cls === 'b' ? 'b' : 'div');
    if (cls && cls !== 'b') d.className = cls; if (txt) d.textContent = txt;
    el.appendChild(d); return d; };

  if (state.view !== 'business'){
    const rows = rowsFor(state.view).filter(r => inRange(r.date));
    const total = rows.reduce((a, r) => a + r.amount, 0);
    add(box, 'head', labelFor() + ' · ' + rangeLabel());
    add(box, 'hero', money(total));
    add(box, 'herosub', rows.length + ' entries');
    const list = add(box, 'rows');
    for (const [name, amt] of sumBy(rows, 'category').slice(0, 5)){
      const r = add(list, 'srow');
      add(r, 'k', name);
      add(r, 'v', money(amt));
    }
    moreButton(box, 'Show every entry and the charts');
    return;
  }

  const s = sumNow();
  if (!s){ add(box, 'herosub', 'No data for this month yet.'); return; }
  const inc = incomeFor(s);
  const profit = inc.v - s.cogs - s.opex;

  add(box, 'head', 'OG Gainz · ' + s.month);
  const hero = add(box, 'hero', (profit >= 0 ? '' : '−') + money(Math.abs(profit)));
  hero.classList.add(profit >= 0 ? 'pos' : 'neg');
  add(box, 'herosub', profit >= 0
      ? 'is what the business made this month, after everything it costs to run.'
      : 'is what the business lost this month.');

  const list = add(box, 'rows');
  const line = (k, sub, v) => {
    const r = add(list, 'srow');
    const kk = add(r, 'k', k);
    if (sub){ const sm = document.createElement('small'); sm.textContent = sub;
      kk.appendChild(sm); }
    add(r, 'v', v);
    return r;
  };
  line('Money in', s.meals + ' meals delivered, plus fees and trials', money(inc.v));
  line('Cost to run the kitchen', 'ingredients, delivery, staff, subscriptions',
       '− ' + money(s.cogs + s.opex));
  const t = line('Made this month', '', (profit >= 0 ? '' : '−') + money(Math.abs(profit)));
  t.classList.add('total');

  if (s.capex_cash){
    const a = add(box, 'aside');
    a.appendChild(document.createTextNode('You also spent '));
    add(a, 'b', money(s.capex_cash));
    a.appendChild(document.createTextNode(
      ' on equipment this month — the oven, the drum, the work table. That is not '
      + 'a running cost, it is yours now, so it is left out of the number above.'));
  }
  if (s.outstanding){
    const a = add(box, 'aside');
    add(a, 'b', money(s.outstanding));
    a.appendChild(document.createTextNode(
      ' is still to be collected from customers. That is the money worth chasing.'));
  }
  if (s.out_of_pocket){
    const a = add(box, 'aside');
    add(a, 'b', money(s.out_of_pocket));
    a.appendChild(document.createTextNode(' of this month\u2019s spending came out of your own pocket'
      + (s.fronted_by_family ? ', and ' + money(s.fronted_by_family)
         + ' was fronted by Appa and Abishek.' : '.')));
  }
  const cc = s.cost_check;
  if (cc && cc.material && cc.direction === 'under'){
    const a = add(box, 'aside warn');
    a.appendChild(document.createTextNode('Careful: the food cost looks too low for '
      + cc.meals + ' meals. Around '));
    add(a, 'b', money(Math.abs(cc.gap)));
    a.appendChild(document.createTextNode(
      ' of buying probably never got logged, so the figure above is likely '
      + 'better than the truth.'));
  }
  moreButton(box, 'Show me the full breakdown');
}

function moreButton(box, label){
  const b = document.createElement('button');
  b.className = 'more'; b.type = 'button'; b.textContent = label;
  b.addEventListener('click', () => setDetail(true));
  box.appendChild(b);
}

function setDetail(on){
  state.detail = on;
  try { localStorage.setItem('ogfin-detail', on ? '1' : '0'); } catch (e) {}
  render();
  // Guarded for the same reason scrollToTable is: a scroll that fails must never
  // take the rest of the handler down with it.
  if (!on){
    try { window.scrollTo({ top: 0, behavior: 'smooth' }); }
    catch (e) { try { window.scrollTo(0, 0); } catch (e2) {} }
  }
}

function renderMoney(){
  const on = state.view === 'business';
  for (const id of ['#three','#cardPnl','#cardRecon','#cardPayer',
                    '#cardDelivery','#cardMeal','#cardHealth'])
    $(id).style.display = on ? '' : 'none';
  $('#monthbar').style.display = '';
  $('#basis').style.display = on ? '' : 'none';
  $('#fmonth').style.display = on ? '' : 'none';
  if (!on) return;
  const s = sumNow();
  if (!s){ for (const id of ['#three','#cardPnl','#cardRecon','#cardPayer',
                             '#cardDelivery','#cardMeal']) $(id).style.display = 'none';
           return; }
  $('#fmonth').value = state.month;
  document.querySelectorAll('#basis button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.basis === state.basis)));
  renderThree(s); renderPnl(s); renderReconcile(s);
  renderPayers(s); renderDelivery(s); renderPerMeal(s); renderHealth(s);
}

/* ---------------- render ---------------- */
function render(){
  refreshOptions();
  renderSimple();
  $('#detail').hidden = !state.detail;
  $('#simple').hidden = state.detail;
  $('#tosimple').style.display = state.detail ? '' : 'none';
  if (!state.detail) return;      // nothing below is on screen; don't draw it
  renderMoney();
  let rows = filtered();
  if (HFOCUS) rows = rows.filter(r => HFOCUS.has(r.id));
  renderKPIs(rows);
  $('#cardCost').style.display = state.view === 'business' ? '' : 'none';
  $('#cardVendor').style.display = state.view === 'business' ? '' : 'none';
  $('#cardSub').style.display =
    (state.view === 'dad' && !rows.some(r => r.subcategory)) ? 'none' : '';
  $('#fbiz').style.display = state.view === 'dad' ? '' : 'none';
  dailyChart($('#chDaily'), rows);
  if (state.view === 'business'){
    costTypeBar($('#chCost'), rows);
    rankedBars($('#chVendor'), sumBy(rows, 'vendor'),
      { aria:'Top vendors', labelWidth:150, limit:10,
        onPick: v => { state.q = v; $('#fq').value = v; render(); } });
  }
  rankedBars($('#chCat'), sumBy(rows, 'category'),
    { aria:'Spend by category', labelWidth:172,
      onPick: c => { state.cat = (state.cat === c ? '' : c); state.sub = ''; render(); } });
  rankedBars($('#chSub'), sumBy(rows, 'subcategory'),
    { aria:'Spend by subcategory', labelWidth:150,
      onPick: s => { if (s === '—') return;
        state.sub = (state.sub === s ? '' : s); render(); } });
  renderTable(rows);
  renderActiveFilters(rows.length);
}

/* Say out loud what is currently hiding rows, with one tap to undo it. */
function renderActiveFilters(shown){
  const bar = $('#activefilters'); bar.textContent = '';
  const bits = [];
  if (HFOCUS) bits.push('flagged entries only');
  if (state.payer) bits.push('paid by ' + state.payer);
  if (state.bucket) bits.push(state.bucket);
  if (state.cat) bits.push(state.cat);
  if (state.sub) bits.push(state.sub);
  if (state.biz === 'yes') bits.push('kitchen only');
  if (state.biz === 'no') bits.push('household only');
  if (state.q) bits.push('matching \u201c' + state.q + '\u201d');
  if (!bits.length){ bar.style.display = 'none'; return; }
  bar.style.display = '';
  bar.appendChild(document.createTextNode(
    'Showing ' + shown + (shown === 1 ? ' entry' : ' entries') + ' — ' + bits.join(' · ')));
  const b = document.createElement('button');
  b.className = 'chipclear'; b.type = 'button'; b.textContent = 'Show everything';
  b.addEventListener('click', () => { clearRowFilters(); render(); });
  bar.appendChild(b);
}

/* ---------------- wiring ---------------- */
document.querySelectorAll('.views button').forEach(b => {
  b.addEventListener('click', () => {
    if (b.disabled) return;
    document.querySelectorAll('.views button')
      .forEach(x => x.setAttribute('aria-selected', String(x === b)));
    state.view = b.dataset.view;
    clearRowFilters();          // search box included - it used to survive the switch
    state.sort = 'date'; state.dir = -1;
    sel.clear(); syncBulk();     // ids from the old tab must never carry over
    render();
  });
});
$('#frange').addEventListener('change', e => { HFOCUS = null; state.range = e.target.value; render(); });
$('#fcat').addEventListener('change', e => { HFOCUS = null; state.cat = e.target.value; state.sub = ''; render(); });
$('#fsub').addEventListener('change', e => { HFOCUS = null; state.sub = e.target.value; render(); });
$('#fbiz').addEventListener('change', e => { HFOCUS = null; state.biz = e.target.value; render(); });
let qt;
$('#fq').addEventListener('input', e => {
  clearTimeout(qt);
  qt = setTimeout(() => { HFOCUS = null; state.q = e.target.value; render(); }, 140);
});
$('#fclear').addEventListener('click', () => {
  clearRowFilters();
  state.range = 'month'; $('#frange').value = 'month';
  render();
});
$('#fpayer').addEventListener('change', e => { HFOCUS = null; state.payer = e.target.value; render(); });
$('#fbucket').addEventListener('change', e => { HFOCUS = null; state.bucket = e.target.value; render(); });
$('#fmonth').addEventListener('change', e => { state.month = e.target.value; render(); });
document.querySelectorAll('#basis button').forEach(b => {
  b.addEventListener('click', () => { state.basis = b.dataset.basis; render(); });
});
$('#tosimple').addEventListener('click', () => setDetail(false));
$('#theme').addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : cur === 'light' ? 'dark'
    : (prefersDark() ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('ogfin-theme', next); } catch (e) {}
  render();
});
try {
  const saved = localStorage.getItem('ogfin-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
} catch (e) {}
/* Every browser has matchMedia, but this runs at top level on his phone: if it
   ever throws, the whole dashboard blanks. A theme listener is not worth that. */
function prefersDark(){
  try { return window.matchMedia('(prefers-color-scheme: dark)').matches; }
  catch (e) { return false; }
}
try {
  window.matchMedia('(prefers-color-scheme: dark)')
        .addEventListener('change', render);
} catch (e) {}
let rzt;
window.addEventListener('resize', () => {
  hideTip();
  clearTimeout(rzt); rzt = setTimeout(render, 180);   // charts are sized to container
});

/* ---- editor wiring ---- */
$('#dsave').addEventListener('click', saveEditor);
$('#dcancel').addEventListener('click', closeEditor);
$('#scrim').addEventListener('click', () => { closeEditor(); closeLists(); });
$('#darchive').addEventListener('click', async () => {
  const row = EDIT.row; if (!row) return;
  if (!confirm('Archive "' + (row.what || 'this entry') + '"?\n\n' +
               'It leaves the table but stays recoverable from Notion’s trash.')) return;
  try {
    await archiveRows([row.id]);
    sel.delete(row.id);
    closeEditor(); syncBulk(); render();
    toast('Archived');
  } catch (e){ toast('Not archived — ' + e.message, true); }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape'){
    if ($('#drawer').classList.contains('on')) closeEditor();
    else if ($('#listdrawer').classList.contains('on')) closeLists();
    else if (sel.size){ sel.clear(); syncBulk(); render(); }
  }
});

$('#manage').addEventListener('click', () => openLists(state.view));
$('#lclose').addEventListener('click', closeLists);
$('#lsheet').addEventListener('change', () => {
  LISTS.db = $('#lsheet').value; LISTS.prop = 'Category'; loadLists();
});
$('#lprop').addEventListener('change', () => { LISTS.prop = $('#lprop').value; renderLists(); });
$('#ladd').addEventListener('click', addOption);
$('#lnew').addEventListener('keydown', e => { if (e.key === 'Enter') addOption(); });
function closeLists(){
  $('#listdrawer').classList.remove('on');
  if (!$('#drawer').classList.contains('on')) $('#scrim').classList.remove('on');
}

$('#bulkfield').addEventListener('change', syncBulkValues);
$('#bulkapply').addEventListener('click', applyBulk);
$('#bulkcancel').addEventListener('click', () => { sel.clear(); syncBulk(); render(); });
$('#bulkarchive').addEventListener('click', async () => {
  const n = sel.size;
  if (!confirm('Archive ' + n + ' entr' + (n === 1 ? 'y' : 'ies') + '?\n\n' +
               'They leave the table but stay recoverable from Notion’s trash.')) return;
  const btn = $('#bulkarchive'); btn.disabled = true;
  try {
    const j = await archiveRows([...sel]);
    sel.clear(); syncBulk(); render();
    toast((j.done || []).length + ' archived');
  } catch (e){ toast('Not archived — ' + e.message, true); }
  finally { btn.disabled = false; }
});

// #personal / #dad opens straight on that tab, so a view can be bookmarked.
function initView(){
  const want = (location.hash || '').replace('#','');
  if (!want) return;
  const b = document.querySelector('.views button[data-view="' + CSS.escape(want) + '"]');
  if (b && !b.disabled) b.click();
}

render();
initView();
// Editing lights up only if the server answers; render again so the table grows its
// checkboxes. Ordering matters — the read-only render must never depend on the fetch.
initEdit().then(() => { if (EDIT.on) render(); });
"""


def _view_button(key, label, enabled, selected):
    dis = "" if enabled else " disabled"
    sel = "true" if selected else "false"
    title = "" if enabled else ' title="Not connected yet"'
    return (f'<button data-view="{key}" role="tab" aria-selected="{sel}"'
            f'{dis}{title}>{label}</button>')


def render_dashboard(monthly, expenses, personal, dad, cur, dad_status=None,
                     summaries=None, health=None, months=None):
    """Return the complete dashboard HTML as a string.

    monthly    list of per-month dicts from build_monthly()
    expenses   business rows (shape_expenses), enriched by model.enrich()
    personal   personal rows (shape_personal)
    dad        appa rows, or None when that database isn't shared yet
    cur        the month being featured, e.g. "Aug-2026"
    summaries  month -> model.month_summary(), drives the money-control panels
    health     month -> model.health()
    months     every month with data, oldest first
    """
    cur_row = next((m for m in monthly if m["month"] == cur), None) or {}
    revenue = {
        "month": cur,
        "recognised": round(cur_row.get("recognised_revenue") or 0),
        "delivered": cur_row.get("delivered") or 0,
    }
    months = months or [cur]
    if cur not in months:
        months = list(months) + [cur]

    payload = {
        "today": datetime.now().strftime("%Y-%m-%d"),
        "generated": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "revenue": revenue,
        "pairs": load_pairs(),
        "editFields": EDIT_FIELDS,
        "currentMonth": cur,
        "months": months,
        "summaries": summaries or {},
        "health": health or {},
        "payerOrder": model.PAYER_ORDER,
        "bucketOrder": model.BUCKET_ORDER,
        # Sent rather than duplicated in JS, so an edit made on the phone derives
        # payer and cost type exactly the way model.py would.
        "payerMap": model._PAYER_MAP,
        "financingCategories": sorted(model.FINANCING_CATEGORIES),
        "financingWords": list(model.FINANCING_WORDS),
        "data": {
            "business": expenses or [],
            "personal": personal or [],
            "dad": dad or [],
        },
    }
    month_opts = "".join(
        '<option value="%s"%s>%s</option>' % (m, " selected" if m == cur else "",
                                               m.replace("-", " "))
        for m in reversed(months))
    # </script> can't appear inside the JSON island
    blob = json.dumps(payload, default=str, ensure_ascii=False).replace("<", "\\u003c")

    has_dad = bool(dad)
    dad_note = ""
    if not has_dad:
        dad_note = (
            '<div class="note"><b>Appa\'s sheet isn\'t connected yet.</b> '
            "Open that database in Notion → ••• menu → Connections → add the "
            "same integration, then run Update-Dashboard.bat again. The tab switches "
            "itself on once rows come through — nothing else to change."
            + (f" <span style=\"color:var(--muted)\">({dad_status})</span>" if dad_status else "")
            + "</div>")

    views = "".join([
        _view_button("business", "OG Gainz", True, True),
        _view_button("personal", "Personal", True, False),
        _view_button("dad", "Appa", has_dad, False),
    ])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OG Gainz · Money</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

  <div class="top">
    <div>
      <div class="brandmark">OG Gainz <span>· Money</span></div>
    </div>
    <div class="stamp">
      <span>Built {payload['generated']}</span>
      <button class="iconbtn" id="manage" type="button" style="display:none">Manage lists</button>
      <button class="iconbtn" id="theme" type="button">Light / dark</button>
    </div>
  </div>

  <div class="views" role="tablist" aria-label="Which money">{views}</div>

  {dad_note}

  <div class="monthbar" id="monthbar">
    <select id="fmonth" aria-label="Which month">{month_opts}</select>
    <button class="iconbtn backsimple" id="tosimple" type="button"
            style="display:none">&larr; Simple view</button>
    <div class="seg" id="basis" role="group" aria-label="How income is counted">
      <button type="button" data-basis="earned" aria-pressed="true">Earned</button>
      <button type="button" data-basis="billed" aria-pressed="false">Billed</button>
      <button type="button" data-basis="cash" aria-pressed="false">Cash in</button>
    </div>
  </div>

  <div class="simple" id="simple"></div>

  <div id="detail">

  <div class="three" id="three"></div>

  <div class="card" id="cardPnl">
    <h2>Did the month actually make money?</h2>
    <p class="hint">Equipment you bought is left out on purpose — it is an investment,
       not a running cost. Bulk stock is charged to the months it actually gets used.
       <b>Earned</b> counts meals delivered · <b>Billed</b> counts what you invoiced ·
       <b>Cash in</b> counts what reached you.</p>
    <table class="pnl"><tbody id="pnl"></tbody></table>
    <div id="belowwrap">
      <p class="hint" style="margin:18px 0 4px">Below the line — real money, but not
         a cost of running this month</p>
      <table class="pnl"><tbody id="belowline"></tbody></table>
    </div>
  </div>

  <div class="grid2">
    <div class="card" id="cardRecon">
      <h2>Where the difference went</h2>
      <p class="hint">Why the cash that left your pockets is bigger than what the
         month cost. This balances to the rupee.</p>
      <table class="pnl"><tbody id="recon"></tbody></table>
    </div>

    <div class="card" id="cardPayer">
      <h2>Out of whose pocket</h2>
      <p class="hint">Tap anyone to filter every entry below to what they paid for.</p>
      <div class="payers" id="payers"></div>
      <div class="owed" id="owed"></div>
    </div>
  </div>

  <div class="grid2">
    <div class="card" id="cardDelivery">
      <h2>Delivery, split properly</h2>
      <p class="hint">Meals going out to customers only. Freight on ingredients
         coming in is counted with the ingredients, where it belongs.</p>
      <div class="facts" id="delfacts"></div>
      <p class="hint" id="delnote" style="margin:14px 0 0"></p>
    </div>

    <div class="card" id="cardMeal">
      <h2>One meal, end to end</h2>
      <p class="hint">What a single box earns and what it really costs.</p>
      <div class="facts" id="mealfacts"></div>
    </div>
  </div>

  <div class="card" id="cardHealth">
    <h2>How much can you trust these numbers?</h2>
    <p class="hint">Only counts what you logged — spend that never got entered can't
       be measured from here.</p>
    <div class="hbar" id="hbar"></div>
    <p class="hint" id="hsum"></p>
    <div id="hlist"></div>
  </div>

  <div class="filters">
    <select id="frange" aria-label="Date range">
      <option value="month" selected>This month</option>
      <option value="7">Last 7 days</option>
      <option value="30">Last 30 days</option>
      <option value="all">All time</option>
    </select>
    <select id="fpayer" aria-label="Who paid"></select>
    <select id="fbucket" aria-label="Cost type"></select>
    <select id="fcat" aria-label="Category"></select>
    <select id="fsub" aria-label="Subcategory"></select>
    <select id="fbiz" aria-label="Business or household" style="display:none">
      <option value="">Business + household</option>
      <option value="yes">Goes to OG Gainz only</option>
      <option value="no">Household only</option>
    </select>
    <input id="fq" type="search" placeholder="Search item, vendor, note…"
           aria-label="Search">
    <button class="chipclear" id="fclear" type="button">Reset</button>
  </div>

  <div class="note" id="activefilters" style="display:none"></div>

  <div id="kpis" class="kpis"></div>

  <div class="card" id="cardCost">
    <h2>How the spend splits</h2>
    <p class="hint">COGS scales with orders · OPEX is flat whatever you sell ·
       CAPEX you still own afterwards · DRAW is money you took out</p>
    <div id="chCost"></div>
  </div>

  <div class="card">
    <h2>Daily spend</h2>
    <p class="hint">Quiet days show as zero. Hover anywhere for that day's total.</p>
    <div id="chDaily"></div>
  </div>

  <div class="grid2">
    <div class="card">
      <h2>By category</h2>
      <p class="hint">Biggest first. Click any bar to filter the whole page to it;
         click it again to clear.</p>
      <div id="chCat"></div>
    </div>
    <div class="card" id="cardSub">
      <h2>By subcategory</h2>
      <p class="hint">The actual thing you bought. Click to filter.</p>
      <div id="chSub"></div>
    </div>
  </div>

  <div class="card" id="cardVendor">
    <h2>Top vendors</h2>
    <p class="hint">Who you pay most.</p>
    <div id="chVendor"></div>
  </div>

  <div class="card">
    <h2>Every entry</h2>
    <p class="hint" id="tblhint">Sortable. Everything the charts show is readable here too.</p>
    <div class="tblwrap scroll" id="tbl"></div>
  </div>

  </div><!-- /detail -->

</div>

<div class="tip" id="tip" role="status" aria-live="polite"></div>

<div class="bulkbar" id="bulkbar">
  <span class="n" id="bulkn">0 selected</span>
  <select id="bulkfield" aria-label="Field to change">
    <option value="category">Set category</option>
    <option value="subcategory">Set subcategory</option>
    <option value="payment">Set payment method</option>
    <option value="paid_from">Set paid from</option>
    <option value="to_business">Set goes to OG Gainz</option>
  </select>
  <select id="bulkvalue" aria-label="New value"></select>
  <button class="btn primary" id="bulkapply" type="button">Apply</button>
  <button class="btn danger" id="bulkarchive" type="button">Archive</button>
  <button class="btn" id="bulkcancel" type="button">Cancel</button>
</div>

<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-labelledby="drawertitle">
  <h3 id="drawertitle">Edit entry</h3>
  <div class="who" id="drawerwho"></div>
  <div id="drawerfields"></div>
  <div class="err" id="drawererr"></div>
  <div class="acts">
    <button class="btn primary" id="dsave" type="button">Save to Notion</button>
    <button class="btn" id="dcancel" type="button">Cancel</button>
    <span class="spacer"></span>
    <button class="btn danger" id="darchive" type="button">Archive</button>
  </div>
</aside>

<aside class="drawer wide" id="listdrawer" role="dialog" aria-modal="true"
       aria-labelledby="listtitle">
  <h3 id="listtitle">Manage lists</h3>
  <div class="who" id="listwho"></div>

  <div class="fld">
    <label for="lsheet">Sheet</label>
    <select id="lsheet"></select>
  </div>
  <div class="fld">
    <label for="lprop">List</label>
    <select id="lprop"></select>
  </div>

  <div class="addbox">
    <div class="fld" id="lclswrap" style="display:none">
      <label for="lcls">Class</label>
      <select id="lcls"></select>
    </div>
    <div class="fld" id="lparentwrap" style="display:none">
      <label for="lparent">Sits under which category</label>
      <select id="lparent"></select>
    </div>
    <div class="fld">
      <label for="lnew">Add a new one</label>
      <div class="row">
        <input id="lnew" type="text" placeholder="name">
        <button class="btn primary" id="ladd" type="button">Add</button>
      </div>
    </div>
  </div>

  <div class="err" id="listerr"></div>
  <div id="listrows"></div>
  <div class="acts">
    <button class="btn" id="lclose" type="button">Done</button>
  </div>
</aside>

<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script>window.__FIN__ = {blob};</script>
<script>{JS}</script>
</body>
</html>
"""
