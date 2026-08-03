"""
OG Gainz Command Center — database layer.

A single SQLite file (oggainz.db) is the source of truth for the whole business.
Everything is plain SQL + the Python stdlib so it is easy to read, back up
(just copy the file), and never silently lose a record.
"""

import os
import sqlite3
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# The database file. Override with the OG_DB_PATH env var to point the app at a
# different copy (handy for safe testing against a throwaway DB without touching
# the live oggainz.db). Defaults to the file next to the code.
DB_PATH = os.environ.get("OG_DB_PATH") or os.path.join(APP_DIR, "oggainz.db")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          TEXT PRIMARY KEY,      -- C001, C002 ...
    name        TEXT NOT NULL,
    phone       TEXT,
    locality    TEXT,
    address     TEXT,
    notes       TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    category          TEXT,
    price_monthly     REAL,
    price_weekly      REAL,
    price_trial       REAL,
    validity_days     INTEGER,
    servings_monthly  INTEGER,
    servings_weekly   INTEGER,
    meals_per_day     INTEGER DEFAULT 1,
    default_slot      TEXT,
    active            INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                    TEXT PRIMARY KEY,   -- S001, S002 ...
    customer_id           TEXT,
    customer_name         TEXT,               -- denormalised for fast lists
    plan_name             TEXT,               -- free text (supports Custom packs)
    duration              TEXT,               -- Monthly / Weekly / Custom
    notes                 TEXT,
    slot                  TEXT,               -- Lunch / Dinner
    preferred_timing      TEXT,
    booking_date          TEXT,
    start_date            TEXT,
    month                 TEXT,               -- Apr-2026 ...
    subscription_price    REAL,
    delivery_fee          REAL,
    total_amount          REAL,
    amount_received       REAL,
    payment_status        TEXT,               -- Paid / Unpaid / Partial
    total_meals_scheduled INTEGER,
    src_delivered         INTEGER,            -- snapshot from the sheet (reference)
    src_pending           INTEGER,
    src_skipped           INTEGER,
    delivered_count       INTEGER DEFAULT 0,  -- live, computed from deliveries
    skipped_count         INTEGER DEFAULT 0,  -- live, computed from deliveries
    remaining_meals       INTEGER DEFAULT 0,  -- scheduled - delivered (skips carry forward)
    status                TEXT,               -- Active / Ending Soon / Completed / Paused
    last_delivery_date    TEXT,
    inactive_days         INTEGER,
    order_no              TEXT,
    created_at            TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT,                     -- YYYY-MM-DD
    subscription_id TEXT,                     -- set for subscription meals
    trial_id        TEXT,                     -- set for trial meals (one of the two)
    customer_id     TEXT,
    name            TEXT,
    meal_type       TEXT,                     -- Lunch / Dinner
    status          TEXT,                     -- Delivered / Skipped / Pending / Off
    month           TEXT,
    notes           TEXT,
    created_at      TEXT,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

CREATE TABLE IF NOT EXISTS trial_orders (
    id                   TEXT PRIMARY KEY,    -- T001 ...
    date                 TEXT,
    name                 TEXT,
    phone                TEXT,
    order_text           TEXT,
    amount               REAL,
    payment_status       TEXT,
    month                TEXT,
    conversion           TEXT,                -- Converted / blank
    address              TEXT,
    notes                TEXT,
    delivery_date        TEXT,                -- when the trial meal is delivered
    meal_type            TEXT,                -- Lunch / Dinner (for the day's prep list)
    delivery_status      TEXT,                -- Pending / Delivered / Skipped
    converted_customer_id TEXT,
    created_at           TEXT
);

-- Force a specific date open or closed regardless of the weekday default.
-- is_open = 1 -> kitchen ON (e.g. a working Saturday); 0 -> kitchen OFF (holiday).
CREATE TABLE IF NOT EXISTS day_overrides (
    date    TEXT PRIMARY KEY,
    is_open INTEGER NOT NULL,
    reason  TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS holidays (
    date   TEXT PRIMARY KEY,
    reason TEXT
);

-- A temporary PAUSE / FREEZE window for one subscription. While "today" falls
-- inside any window the subscription is treated as Paused: no new order rows are
-- auto-generated for those days and its meals carry forward (nothing is consumed).
-- end_date NULL = open-ended (paused until manually resumed). A closed window
-- (end_date set) stays on file so the paused days never re-appear as Pending and
-- the freeze history is auditable.
CREATE TABLE IF NOT EXISTS subscription_pauses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT,
    start_date      TEXT,                -- inclusive YYYY-MM-DD (first paused day)
    end_date        TEXT,                -- inclusive YYYY-MM-DD; NULL = open-ended
    reason          TEXT,
    created_at      TEXT,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

-- A temporary SLOT MOVE for one subscription: deliver in a different slot
-- (e.g. Dinner instead of the usual Lunch) for a date range, then move back.
-- The day's order rows pick up `slot` while a window covers them; past delivered
-- rows keep whatever slot they were delivered in, so the history is preserved.
-- end_date NULL = open-ended (until moved back / window removed).
CREATE TABLE IF NOT EXISTS subscription_slot_moves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT,
    start_date      TEXT,                -- inclusive YYYY-MM-DD
    end_date        TEXT,                -- inclusive YYYY-MM-DD; NULL = open-ended
    slot            TEXT,                -- Lunch / Dinner to use during the window
    reason          TEXT,
    created_at      TEXT,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

-- CRM: leads (prospects) tracked from first contact through to conversion.
CREATE TABLE IF NOT EXISTS leads (
    id              TEXT PRIMARY KEY,    -- L001, L002 ...
    name            TEXT NOT NULL,
    phone           TEXT,
    source          TEXT,                -- Instagram / WhatsApp / Referral / Walk-in / Friend / Ad / Other
    locality        TEXT,
    goal            TEXT,                -- Fat loss / Muscle gain / Maintenance / Other
    interest        TEXT,                -- plan/pack they're interested in
    stage           TEXT DEFAULT 'New',  -- New / Contacted / Trial / Negotiating / Won / Lost
    est_value       REAL,                -- potential rupee value of the deal
    next_follow_up  TEXT,                -- YYYY-MM-DD next action date
    last_contact    TEXT,                -- YYYY-MM-DD last touched
    notes           TEXT,
    lost_reason     TEXT,
    trial_id        TEXT,                -- linked trial order, if they did a trial
    customer_id     TEXT,                -- set when converted (Won)
    subscription_id TEXT,                -- subscription created on conversion, if any
    created_at      TEXT,
    updated_at      TEXT
);

-- CRM: a timeline of updates / touches for each lead.
CREATE TABLE IF NOT EXISTS lead_updates (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id   TEXT,
    at        TEXT,        -- YYYY-MM-DD HH:MM:SS
    kind      TEXT,        -- Note / Call / WhatsApp / Stage / Follow-up / Trial / System
    text      TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(id)
);

-- ===================== MENU ENGINE =====================
-- The editable food catalog: every buildable item (chicken, egg, carb, veggies…).
-- unit: 'g' (aggregated in grams with a size breakdown), 'pcs', or 'serving'.
-- step: the natural increment (chicken 50g steps, egg 1pc). addon_price: price per
-- step when offered as a member add-on (NULL = not sold as an add-on).
CREATE TABLE IF NOT EXISTS components (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    unit        TEXT NOT NULL DEFAULT 'serving',   -- g | pcs | serving
    step        REAL DEFAULT 1,
    default_qty REAL DEFAULT 1,
    addon_price REAL,
    protein     TEXT,                              -- display only, e.g. '11g P / 50g'
    emoji       TEXT,
    sort        INTEGER DEFAULT 100,
    active      INTEGER DEFAULT 1
);

-- What a standard pack contains (editable recipe per plan).
CREATE TABLE IF NOT EXISTS plan_components (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id      INTEGER NOT NULL,
    component_id INTEGER NOT NULL,
    qty          REAL NOT NULL DEFAULT 1,
    note         TEXT,
    FOREIGN KEY (plan_id) REFERENCES plans(id),
    FOREIGN KEY (component_id) REFERENCES components(id)
);

-- What THIS subscription actually gets every meal: the pack recipe snapshot
-- (kind='base') plus paid add-ons (kind='addon') or a fully custom build.
CREATE TABLE IF NOT EXISTS subscription_components (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT NOT NULL,
    component_id    INTEGER NOT NULL,
    qty             REAL NOT NULL DEFAULT 1,
    kind            TEXT DEFAULT 'base',           -- base | addon
    note            TEXT,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
    FOREIGN KEY (component_id) REFERENCES components(id)
);

-- Standing per-subscription tweaks the prep list applies automatically:
-- e.g. swap Chicken → Paneer on Tue+Fri, or add an extra Egg on Mondays.
-- weekdays: CSV of weekday numbers (Mon=0); NULL = every day.
CREATE TABLE IF NOT EXISTS component_rules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id   TEXT NOT NULL,
    weekdays          TEXT,
    start_date        TEXT,
    end_date          TEXT,
    action            TEXT NOT NULL,               -- swap | add | remove
    from_component_id INTEGER,
    to_component_id   INTEGER,
    qty               REAL,
    note              TEXT,
    source            TEXT DEFAULT 'manual',       -- manual | agent
    active            INTEGER DEFAULT 1,
    created_at        TEXT,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

-- ===================== INVOICES =====================
-- items is a JSON list of {desc, qty, unit, amount}. token makes the private
-- customer-facing link (/i/<token>) unguessable without needing a login.
CREATE TABLE IF NOT EXISTS invoices (
    id              TEXT PRIMARY KEY,              -- INV-001 ...
    token           TEXT UNIQUE,
    customer_id     TEXT,
    subscription_id TEXT,
    trial_id        TEXT,
    bill_name       TEXT,
    bill_phone      TEXT,
    bill_address    TEXT,
    inv_date        TEXT,
    items           TEXT,
    discount        REAL DEFAULT 0,
    total           REAL DEFAULT 0,
    received        REAL DEFAULT 0,
    payment_status  TEXT DEFAULT 'Unpaid',
    notes           TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

-- ===================== OG AGENT =====================
CREATE TABLE IF NOT EXISTS notifications (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT,
    kind    TEXT,                                  -- digest | alert | reminder | agent
    title   TEXT,
    body    TEXT,
    link    TEXT,
    is_read INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT,
    role    TEXT,                                  -- user | assistant
    content TEXT
);

-- OG Agent's long-term memory: facts Balaji teaches it ("we now deliver on
-- Saturdays too", "Prakash prefers extra spicy"). Injected into every chat so
-- the agent's knowledge grows over time. Facts are only saved after Balaji
-- confirms them with one tap — the agent never writes its own memory silently.
CREATE TABLE IF NOT EXISTS agent_facts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT,
    topic   TEXT,                                  -- short label, e.g. 'pricing', 'delivery'
    fact    TEXT NOT NULL,
    source  TEXT DEFAULT 'chat',                   -- chat | manual
    active  INTEGER DEFAULT 1
);

-- Prep-list tick-offs: one row per (day, item) Balaji has marked as prepped.
-- Purely a kitchen self-check; never affects orders or subscriptions.
CREATE TABLE IF NOT EXISTS prep_ticks (
    date TEXT NOT NULL,                            -- YYYY-MM-DD
    key  TEXT NOT NULL,                            -- 'Lunch|Chicken', 'All|Egg' ...
    PRIMARY KEY (date, key)
);

-- ===================== BROCHURE =====================
-- The always-editable digital brochure (public page /brochure). Body text uses a
-- tiny markup: '- ' bullets, '| a | b |' tables, **bold**, blank line = new para.
CREATE TABLE IF NOT EXISTS brochure_sections (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sort     INTEGER DEFAULT 100,
    title    TEXT NOT NULL,
    subtitle TEXT,
    body     TEXT,
    active   INTEGER DEFAULT 1,
    updated_at TEXT
);

-- ---------------------------------------------------------------------------
-- Evening ops: geography, routing and the rider's shift record
-- ---------------------------------------------------------------------------

-- A customer can have more than one delivery location: home, office, gym. Some
-- subscribers want the office Mon–Wed and home Thu–Fri, and sometimes a one-day
-- change told the evening before. Each address carries its own pin, so the route,
-- the zone and the petrol allowance follow whichever one is used that day.
CREATE TABLE IF NOT EXISTS customer_addresses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    label       TEXT,                      -- Home / Office / Gym / PG
    address     TEXT,
    lat         REAL,
    lng         REAL,
    zone        TEXT,                      -- override; otherwise computed from distance
    corridor    TEXT,                      -- override; otherwise inferred from locality
    drop_type   TEXT,                      -- Home / PG / Office / Gym
    map_note    TEXT,                      -- 'gate 2, call on arrival'
    weekdays    TEXT,                      -- standing schedule: '0,1,2' = Mon,Tue,Wed
    is_default  INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1,
    sort        INTEGER DEFAULT 100,
    created_at  TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_cust_addr ON customer_addresses(customer_id);

-- Porter handoff points. A maintained list, never hardcoded — they move as
-- orders and routes change. The rider carries a far order to one of these on a
-- corridor he already rides, and a Porter is booked from there instead of from
-- the kitchen, so the paid Porter leg is much shorter.
CREATE TABLE IF NOT EXISTS porter_points (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    code   TEXT,                      -- PP-1, PP-2 ...
    name   TEXT NOT NULL,             -- 'Sholinganallur junction, opp. <landmark>'
    lat    REAL,
    lng    REAL,
    serves TEXT,                      -- corridor codes this point covers, comma separated
    note   TEXT,                      -- landmark wording spoken to the Porter driver
    active INTEGER DEFAULT 1,
    sort   INTEGER DEFAULT 100
);

-- One row per date+slot: the frozen record of a delivery shift. The route is
-- snapshotted into route_json so the printed sheet always matches what actually
-- ran, even if a subscription changes afterwards.
CREATE TABLE IF NOT EXISTS shifts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,               -- YYYY-MM-DD
    slot       TEXT NOT NULL,               -- Lunch / Dinner
    staff_name TEXT,
    route_json TEXT,
    maps_url   TEXT,
    est_km     REAL,                        -- computed (api | estimate)
    actual_km  REAL,                        -- rider-corrected at close-out; payroll uses this
    km_source  TEXT,                        -- api | estimate | manual
    allowance  REAL,                        -- km x petrol_rate
    depart_at  TEXT,
    return_at  TEXT,
    issues     TEXT,
    signed     INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(date, slot)
);

-- Per-stop assignment inside a shift. Note there is deliberately no `delivered`
-- column: delivery state lives on deliveries.status so there is exactly one
-- source of truth. packed/checked are shift-only micro-states.
CREATE TABLE IF NOT EXISTS shift_stops (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id        INTEGER NOT NULL,
    delivery_id     INTEGER,
    seq             INTEGER,                -- 1..n, the printed riding order
    zone            TEXT,                   -- Hyperlocal / Near / Mid / Long
    mode            TEXT,                   -- rider | porter_handoff | porter_direct
    porter_point_id INTEGER,
    eta             TEXT,                   -- rough clock time, tells Balaji when to book
    km_from_prev    REAL,
    packed          INTEGER DEFAULT 0,
    checked         INTEGER DEFAULT 0,
    delivered_at    TEXT,
    FOREIGN KEY (shift_id) REFERENCES shifts(id)
);

-- Distance cache keyed by the ordered stop signature, so a route shape that
-- repeats daily is only ever measured once.
CREATE TABLE IF NOT EXISTS route_km_cache (
    signature TEXT PRIMARY KEY,
    km        REAL,
    source    TEXT,
    at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_shift_stops_shift ON shift_stops(shift_id);
CREATE INDEX IF NOT EXISTS idx_shifts_date       ON shifts(date);

CREATE INDEX IF NOT EXISTS idx_sub_comps      ON subscription_components(subscription_id);
CREATE INDEX IF NOT EXISTS idx_plan_comps     ON plan_components(plan_id);
CREATE INDEX IF NOT EXISTS idx_comp_rules_sub ON component_rules(subscription_id);
CREATE INDEX IF NOT EXISTS idx_notif_read     ON notifications(is_read);

CREATE INDEX IF NOT EXISTS idx_deliveries_date ON deliveries(date);
CREATE INDEX IF NOT EXISTS idx_deliveries_sub  ON deliveries(subscription_id);
CREATE INDEX IF NOT EXISTS idx_subs_status     ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subs_customer   ON subscriptions(customer_id);
CREATE INDEX IF NOT EXISTS idx_leads_stage     ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_leads_follow    ON leads(next_follow_up);
CREATE INDEX IF NOT EXISTS idx_lead_updates    ON lead_updates(lead_id);
CREATE INDEX IF NOT EXISTS idx_pauses_sub      ON subscription_pauses(subscription_id);
CREATE INDEX IF NOT EXISTS idx_slot_moves_sub  ON subscription_slot_moves(subscription_id);
"""

# Columns added after v1 shipped — added in-place so existing databases keep their data.
# day_note = a one-off note for THIS specific day's order (slot swap, same-price plan
# change, a customisation just for today, a food issue to remember). Distinct from the
# subscription's standing `notes` and from deliveries.notes (which holds the plan label).
#
# Customer geography (added Jul 2026 for the evening delivery route system):
# free-text addresses do not geocode reliably in Chennai, so every delivering
# customer gets a hand-dropped lat/lng pin. Everything downstream — zone banding,
# route order, total km, the ₹3/km petrol allowance — measures from those pins.
MIGRATIONS = {
    # address_id = "this particular day's delivery goes to this address instead of
    # the usual one" — the one-off override that beats the weekday schedule.
    # porter_point_id = hand this order to a Porter at that point instead of
    # letting the automatic rule decide. NULL = decide automatically (the default,
    # so nothing changes for orders that were never touched); 0 = the rider
    # definitely delivers it himself; >0 = hand off at that porter point.
    # This is a routing instruction only — the customer's address is untouched.
    "deliveries":    [("trial_id", "TEXT"), ("day_note", "TEXT"), ("day_note_at", "TEXT"),
                      ("address_id", "INTEGER"), ("porter_point_id", "INTEGER")],
    "trial_orders":  [("notes", "TEXT"), ("delivery_date", "TEXT"),
                      ("meal_type", "TEXT"), ("delivery_status", "TEXT"),
                      ("plan_id", "INTEGER")],   # which pack the trial is of → prep list
    "customers":     [("lat", "REAL"), ("lng", "REAL"),
                      ("zone", "TEXT"),          # auto from distance, overridable
                      ("corridor", "TEXT"),      # OMR-N / West / OMR-S
                      ("drop_type", "TEXT"),     # Home / PG / Office / Gym
                      ("map_note", "TEXT")],     # 'gate 2, call on arrival'
    # Hand edits made to a printed sheet before it is sent out: {element_key: html}.
    # Kept separate from the generated data so regenerating never loses them, and
    # so the underlying orders are never silently rewritten by a print-time tweak.
    "shifts":        [("overrides", "TEXT")],
    # Per-leg distances alongside the total, so the sheet can show both the
    # distance to each stop and the distance of each hop without a second call.
    "route_km_cache": [("legs", "TEXT")],
    # slot: a schedule can apply to Lunch only, Dinner only, or both (NULL).
    # "lunch at home on Mon and Fri, office the rest, dinner always home" needs it.
    #
    # porter_point_id: the STANDING handoff for this address — "anything going to
    # Nanganallur is handed over at Medavakkam". Saves re-assigning the same
    # customer every single evening. A per-delivery value overrides it for one day.
    "customer_addresses": [("slot", "TEXT"), ("porter_point_id", "INTEGER")],
}


def migrate(conn):
    for table, cols in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    # indexes that depend on migrated columns
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_trial ON deliveries(trial_id)")
    conn.commit()
    backfill_addresses(conn)


def backfill_addresses(conn):
    """Give every customer who has an address a matching default address row.

    Runs once. Multi-address support reads from customer_addresses, so without
    this every existing customer would look like they had no delivery location
    at all. The original columns on `customers` are left untouched as a safety
    net — nothing is moved or deleted, only copied.
    """
    if get_setting(conn, "addresses_backfilled_v1"):
        return
    rows = conn.execute(
        "SELECT id, address, locality, lat, lng, zone, corridor, drop_type, map_note "
        "FROM customers").fetchall()
    made = 0
    for r in rows:
        has_any = conn.execute(
            "SELECT 1 FROM customer_addresses WHERE customer_id=?", (r["id"],)).fetchone()
        if has_any:
            continue
        if not ((r["address"] or "").strip() or r["lat"] is not None):
            continue                       # nothing worth copying
        label = r["drop_type"] or "Home"
        conn.execute(
            "INSERT INTO customer_addresses(customer_id,label,address,lat,lng,zone,"
            "corridor,drop_type,map_note,is_default,active,sort,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,1,10,?)",
            (r["id"], label, r["address"], r["lat"], r["lng"], r["zone"],
             r["corridor"], r["drop_type"], r["map_note"], now_iso()))
        made += 1
    set_setting(conn, "addresses_backfilled_v1", "1")
    conn.commit()
    return made


def get_db(path=DB_PATH):
    """Return a SQLite connection with sensible, safe defaults."""
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")     # safer concurrent reads/writes
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db(path=DB_PATH):
    """Create all tables if they do not exist, then apply column migrations."""
    conn = get_db(path)
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)
    return conn


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Initialised database at {DB_PATH}")
