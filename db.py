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

-- ===================== CUSTOMER REQUESTS =====================
-- Skip / pause / resume asked for by the customer in the OG Gainz app.
-- A row is written for EVERY action, including ones applied instantly
-- (outcome = 'auto_applied'), because without that history there is nothing to
-- screen a repeat late-asker against.
--
-- cutoff_at and minutes_late are STORED, never recomputed. The cutoff is a
-- setting and it will change; history has to keep saying how late a request was
-- against the rule that was actually in force at the time.
CREATE TABLE IF NOT EXISTS skip_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     TEXT NOT NULL,
    subscription_id TEXT,
    kind            TEXT NOT NULL,   -- skip | pause | resume | undo
    target_date     TEXT,            -- for skip / resume
    start_date      TEXT,            -- for pause
    end_date        TEXT,
    requested_at    TEXT NOT NULL,
    cutoff_at       TEXT NOT NULL,   -- the deadline that applied AT THE TIME
    minutes_late    INTEGER NOT NULL DEFAULT 0,
    reason          TEXT,
    outcome         TEXT NOT NULL,   -- auto_applied | pending | allowed | declined
    decided_at      TEXT,
    decided_by      TEXT,
    decline_reason  TEXT,
    source          TEXT DEFAULT 'app'
);
CREATE INDEX IF NOT EXISTS idx_skipreq_pending
    ON skip_requests(outcome, requested_at);
CREATE INDEX IF NOT EXISTS idx_skipreq_customer
    ON skip_requests(customer_id, requested_at);

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

-- A slot can be split into several routes ridden by different people — Balaji
-- takes the West run while his father takes OMR, and a hired rider takes one
-- whole batch. One row per (date, slot, batch). Everything that used to be
-- per-shift — km, allowance, who rode it — is per BATCH once a slot is split,
-- because two people riding two directions must be paid on their own distance.
CREATE TABLE IF NOT EXISTS route_batches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,               -- YYYY-MM-DD
    slot       TEXT NOT NULL,               -- Lunch / Dinner
    batch_no   INTEGER NOT NULL,            -- 1..n, the printed run number
    name       TEXT,                        -- 'West run', 'OMR run'
    rider      TEXT,                        -- free text; a datalist keeps spelling steady
    est_km     REAL,                        -- computed for this batch alone
    actual_km  REAL,                        -- rider-corrected; payroll uses this
    allowance  REAL,
    depart_at  TEXT,
    notes      TEXT,
    created_at TEXT,
    UNIQUE(date, slot, batch_no)
);
CREATE INDEX IF NOT EXISTS idx_batches_day ON route_batches(date, slot);

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

-- ---------------------------------------------------------------------------
-- Per-subscription DELIVERY SCHEDULE (Aug 2026)
-- ---------------------------------------------------------------------------
-- Not everyone eats every kitchen day, and not everyone eats the same box every
-- day. Two real cases this exists for:
--   Razia    — Mon, Wed, Fri only. The other days must not generate an order at
--              all, so her 20 meals last ~7 weeks instead of 4.
--   Vaithi   — Signature and Protein Salad on alternating days, and Thursday is
--              a veg box (paneer instead of chicken, legumes instead of egg).
-- Both are the same question asked once per weekday: do we deliver, in which
-- slot, and which box? So one table answers it.
--
-- A subscription with NO rows here behaves exactly as it always has (an order on
-- every operating day) — that is what subscriptions.schedule_mode='daily' means
-- and it is the default for every existing pack.
CREATE TABLE IF NOT EXISTS subscription_day_plan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT NOT NULL,
    weekday         INTEGER NOT NULL,        -- 0=Mon .. 6=Sun
    active          INTEGER DEFAULT 1,       -- 0 = no delivery on this weekday
    slot            TEXT,                    -- NULL = the subscription's usual slot
    variant_id      INTEGER,                 -- NULL = the subscription's default box
    meals           INTEGER DEFAULT 1,       -- standing two-box days
    note            TEXT,
    UNIQUE(subscription_id, weekday),
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

-- A named alternative box for ONE subscription — "Protein Salad", "Veg Signature".
-- Its contents live in subscription_components with this id in variant_id, so a
-- variant is built and edited with exactly the same machinery as the normal box.
-- plan_id only records which pack recipe it was seeded from (for the label and so
-- the prep list can name it); the saved components are what actually gets cooked.
CREATE TABLE IF NOT EXISTS subscription_variants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT NOT NULL,
    name            TEXT NOT NULL,
    plan_id         INTEGER,
    sort            INTEGER DEFAULT 100,
    created_at      TEXT,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);

-- CRM: the WhatsApp replies Balaji sends over and over. Editable, because the
-- wording is his and it changes; kept in the database rather than in the code so
-- he can fix a sentence from his phone without a deploy.
CREATE TABLE IF NOT EXISTS lead_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,      -- 'First reply', 'Price list', 'Still interested?'
    stage      TEXT,               -- shown first on this stage; NULL = always shown
    body       TEXT NOT NULL,      -- supports {name} {first} {interest} {locality} {brochure}
    advance_to TEXT,               -- stage to move the lead to after sending, if any
    followup_days INTEGER,         -- auto-set the next follow-up this many days out
    sort       INTEGER DEFAULT 100,
    active     INTEGER DEFAULT 1,
    created_at TEXT
);

-- CRM: what was spent to get leads, by month and channel. Without this, cost per
-- lead and cost per customer are unanswerable, which is the whole point of
-- running ads with a CRM attached.
CREATE TABLE IF NOT EXISTS marketing_spend (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    month      TEXT NOT NULL,      -- YYYY-MM
    channel    TEXT NOT NULL,      -- Meta ads / Google / Pamphlet / Influencer ...
    campaign   TEXT,               -- optional: the specific campaign
    amount     REAL NOT NULL DEFAULT 0,
    note       TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_dayplan_sub  ON subscription_day_plan(subscription_id);
CREATE INDEX IF NOT EXISTS idx_variants_sub ON subscription_variants(subscription_id);

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
CREATE INDEX IF NOT EXISTS idx_leads_phone     ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_spend_month     ON marketing_spend(month);
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
    # batch_no = which of the slot's routes this order rides on. NULL = not split
    # (one route, exactly as before), so existing days are untouched.
    # is_extra = a meal added BY HAND on top of that day's normal order — the
    # customer asked for a second box in another slot, or a bigger one today, and
    # drew the serving from the pack they had already bought. The flag matters
    # because auto-generation must never create one and the self-heal deletes must
    # never remove one: an extra is a decision, not something the app inferred.
    # counts_against_pack = 1 (the normal case) consumes one of the pack's meals,
    # so 20 meals finish a day earlier. 0 is for a meal sold separately once the
    # pack is already used up — it shows in the day's cooking but not in the count.
    "deliveries":    [("trial_id", "TEXT"), ("day_note", "TEXT"), ("day_note_at", "TEXT"),
                      ("address_id", "INTEGER"), ("porter_point_id", "INTEGER"),
                      ("batch_no", "INTEGER"),
                      ("is_extra", "INTEGER DEFAULT 0"),
                      ("counts_against_pack", "INTEGER DEFAULT 1"),
                      # Which box was cooked for this order, when the day plan says
                      # it is not the subscription's usual one. Stored on the row so
                      # the history still shows what actually went out even if the
                      # schedule is changed months later.
                      ("variant_id", "INTEGER"),
                      # --- added Aug 2026 ---
                      # Who took this day off. 'customer' means they did it in
                      # the app; NULL means Balaji marked it by hand, which is
                      # every Skipped row that existed before this.
                      #
                      # A customer's skip now MARKS the order Skipped instead of
                      # deleting it. It used to create a one-day pause window,
                      # which removed the row entirely — so the day disappeared
                      # from the kitchen sheet and left no record that anything
                      # had been skipped at all. A skipped day that leaves no
                      # trace cannot be counted, queried, or discussed later.
                      ("skip_source", "TEXT"),
                      ("skipped_at", "TEXT")],
    "trial_orders":  [("notes", "TEXT"), ("delivery_date", "TEXT"),
                      ("meal_type", "TEXT"), ("delivery_status", "TEXT"),
                      ("plan_id", "INTEGER")],   # which pack the trial is of → prep list
    # email: customers were only ever reached on WhatsApp, so this never existed.
    # The app signs people in with Google, so knowing a customer's email lets a
    # sign-up be matched to them automatically instead of by hand.
    "customers":     [("email", "TEXT"),
                      ("lat", "REAL"), ("lng", "REAL"),
                      ("zone", "TEXT"),          # auto from distance, overridable
                      ("corridor", "TEXT"),      # OMR-N / West / OMR-S
                      ("drop_type", "TEXT"),     # Home / PG / Office / Gym
                      ("map_note", "TEXT")],     # 'gate 2, call on arrival'
    # Hand edits made to a printed sheet before it is sent out: {element_key: html}.
    # Kept separate from the generated data so regenerating never loses them, and
    # so the underlying orders are never silently rewritten by a print-time tweak.
    "shifts":        [("overrides", "TEXT")],
    # --- CRM, Aug 2026: lead management for real, not just a list of names ---
    # Every column here is additive and nullable, so the leads table behaves
    # exactly as before until something actually writes to them.
    #
    # kind separates the three very different things now living in one pipeline:
    #   new      — a fresh inquiry, the ordinary case and the default
    #   trial    — someone who already ate a trial box and was never closed
    #   winback  — a past customer whose pack ended and never renewed
    # They convert at wildly different rates, so a report that mixes them tells
    # Balaji nothing about whether his marketing works.
    #
    # campaign is what makes ad spend answerable: 'Meta — Aug fat-loss' on the
    # lead, the same string on a marketing_spend row, and cost per lead falls out.
    # source_detail holds the human bit — WHICH friend referred them, which post.
    #
    # stage_at / won_at / lost_at exist because updated_at moves on every edit,
    # so it could never answer "how long has this been stuck?" or "how many did
    # we win in July?" once a lead was touched again in August.
    "leads":         [("kind", "TEXT DEFAULT 'new'"),
                      ("campaign", "TEXT"),
                      ("source_detail", "TEXT"),
                      ("temp", "TEXT"),              # Hot / Warm / Cold
                      ("zone", "TEXT"),              # guessed from the locality
                      ("corridor", "TEXT"),          # Local / West / OMR-N / OMR-S
                      ("stage_at", "TEXT"),          # when the CURRENT stage began
                      ("won_at", "TEXT"),
                      ("lost_at", "TEXT"),
                      ("first_reply_at", "TEXT"),    # when he first answered them
                      ("touches", "INTEGER DEFAULT 0"),
                      # The furthest step this lead ever REACHED, which is not the
                      # same as where it sits now. Someone who got to Trial and then
                      # said no is stored as 'Lost', so the current stage alone can
                      # never say how far they got — and a drop-off report built on
                      # it silently undercounts every step of the funnel.
                      ("peak_stage", "TEXT")],

    # Per-leg distances alongside the total, so the sheet can show both the
    # distance to each stop and the distance of each hop without a second call.
    "route_km_cache": [("legs", "TEXT")],
    # slot: a schedule can apply to Lunch only, Dinner only, or both (NULL).
    # "lunch at home on Mon and Fri, office the rest, dinner always home" needs it.
    #
    # porter_point_id: the STANDING handoff for this address — "anything going to
    # Nanganallur is handed over at Medavakkam". Saves re-assigning the same
    # customer every single evening. A per-delivery value overrides it for one day.
    # batch_no here is the STANDING preference — "Nanganallur always rides with
    # the West run" — so only the exceptions need touching each evening.
    "customer_addresses": [("slot", "TEXT"), ("porter_point_id", "INTEGER"),
                           ("batch_no", "INTEGER"),
                           # Who put this pin here. A customer can drop their own
                           # from the app, and Balaji can correct any of them by
                           # hand — when the two disagree the admin pin wins until
                           # the customer edits it again, so we have to know which
                           # of the two set the one currently stored.
                           ("pin_source", "TEXT"),      # customer | admin
                           ("pin_set_at", "TEXT")],
    # A trial is a real box that a real rider carries to a real door, so it needs
    # a pin like any other stop. Without these it could never have one: the trial
    # had nowhere to store a location, so every trial rode as "no pin", with no
    # distance and no share of the km allowance, and never appeared on the pin page.
    "trial_orders": [("lat", "REAL"), ("lng", "REAL")],
    # --- added Aug 2026 for the OG Gainz app (og-gainz-app/docs/03 §2.1) ---
    # ALL of these are defaults-only: every one of them leaves existing packs
    # behaving exactly as they do today. Nothing here switches anything on.
    #
    # enforce_validity: packs expire in the brochure but nothing has ever
    # enforced it, and most current packs were sold with validity waived or never
    # mentioned. So validity is opt-in PER SUBSCRIPTION and defaults to OFF — not
    # one existing pack starts expiring because this shipped. Turning it on is a
    # decision Balaji makes one pack at a time.
    # late_delivery_agreed: a few far orders legitimately arrive after the 8 PM
    # promise, agreed with the customer before they subscribed. Nothing records
    # that today, so an ETA past 8 PM cannot tell "expected" from "problem".
    # schedule_mode decides who gets an order generated on an operating day:
    #   daily     — every kitchen day. The default, and what every pack sold so
    #               far does, so this migration changes nothing for anyone.
    #   weekly    — only the weekdays ticked in subscription_day_plan (Razia's
    #               Mon/Wed/Fri, Raja's Mon-Thu, Vaithi's pack-per-day plan).
    #   ondemand  — nothing is generated. The customer confirms a day at a time
    #               and the days are entered in the week planner.
    "subscriptions": [("enforce_validity", "INTEGER DEFAULT 0"),
                      ("expires_on", "TEXT"),
                      ("validity_note", "TEXT"),
                      ("late_delivery_agreed", "INTEGER DEFAULT 0"),
                      ("schedule_mode", "TEXT DEFAULT 'daily'"),
                      # --- added Aug 2026, Balaji's instruction (validity.py) ---
                      # validity_override lets ONE pack disagree with the global
                      # switch below: NULL = follow the global switch, 'on' /
                      # 'off' = an explicit decision that wins regardless of it.
                      # enforce_validity (above) stays the column every reader
                      # already checks — validity.sync() keeps it in step with
                      # whichever of these two actually applies. Default NULL
                      # means "not yet decided", which resolves to the global
                      # switch, which defaults OFF — so this changes nothing
                      # until Balaji turns something on.
                      ("validity_override", "TEXT"),
                      ("validity_changed_at", "TEXT"),
                      ("validity_changed_by", "TEXT"),
                      # --- added Aug 2026, Balaji's instruction (flexibility.py) ---
                      # Whether THIS pack's customer may skip a day or pause a
                      # stretch, built to the same shape as validity_override
                      # above because he asked for it in those words: NULL =
                      # follow the global switch, 'on' / 'off' = an explicit
                      # decision for one customer that wins regardless of it.
                      #
                      # It replaces `freeze_eligible_plans`, which could only
                      # ever say "Elite and Athlete may pause" — a rule about
                      # PLANS when the thing he actually needed to control was
                      # a PERSON. Default NULL resolves to the global switch,
                      # which defaults ON, so every pack can skip and pause
                      # until he decides otherwise.
                      ("flex_override", "TEXT"),
                      ("flex_note", "TEXT"),
                      ("flex_changed_at", "TEXT"),
                      ("flex_changed_by", "TEXT")],
    # The negotiated add-on rate. The catalog price is a starting suggestion, not
    # a constraint — Ashok pays ~₹49 per 50g of chicken where the catalog says
    # ₹60. Today that difference gets absorbed into the typed total and the
    # reason disappears, so six months later nobody can reconstruct the bill.
    # variant_id: NULL = a row of the subscription's normal box (every row that
    # exists today), >0 = a row belonging to that named variant. Because the
    # default is NULL, existing compositions are untouched and every query that
    # never heard of variants keeps returning the normal box.
    "subscription_components": [("agreed_price", "REAL"), ("price_note", "TEXT"),
                                ("variant_id", "INTEGER")],
}

# Settings the app reads, with the values that are true today. Seeded once and
# never overwritten — if Balaji changes the cutoff, a later deploy must not
# quietly put it back. There must be exactly ONE place that knows what time the
# cutoff is, and this is it: the countdown, the server-side check and the
# published terms all read from here rather than each holding their own constant.
APP_SETTINGS_DEFAULTS = {
    "skip_cutoff_time":        "18:00",
    "skip_cutoff_applies_to":  "calendar_day",
    "late_requests_allowed":   "1",
    "pause_max_days":          "",          # empty = unlimited
    "freeze_eligible_plans":   "Elite Meal Pack,Athlete Meal Pack",
    # Customer-facing delivery PROMISE — not the staff shift. All food is
    # delivered by 8 PM; shift_end (21:00) is a payroll number and must never be
    # shown to a customer as a delivery window.
    "delivery_promise_lunch":  "11:00-13:45",
    "delivery_promise_dinner": "17:30-20:00",
    # Delivery-FEE bands, which are not the same thing as the routing zone bands
    # (zone_hyperlocal_km / zone_near_km / zone_mid_km). Those decide the riding
    # order; these decide what the customer is charged, and today they exist only
    # in Balaji's head. Kept apart on purpose so changing one cannot move the other.
    "fee_band_free_km":        "5",
    "fee_band_mid_km":         "10",
    "fee_band_far_km":         "15",
    # The master validity switch (validity.py). OFF: no pack expires, matching
    # every day before this setting existed. ON: every pack whose own
    # validity_override is NULL is enforced against its plan's validity_days —
    # a pack Balaji has explicitly turned on or off keeps that explicit decision
    # regardless of this switch (docs/01 §7.2.0's [LAW] still holds: a promise
    # made in one WhatsApp conversation is never silently broken by a flag
    # flipped for everyone else).
    "validity_enforced_globally": "0",
    # The master skip/pause switch (flexibility.py). ON, because skipping and
    # pausing are the reason the customer app exists — a customer who cannot
    # take a day off has no use for it. A pack whose flex_override is set keeps
    # that explicit decision regardless of this switch.
    "flex_enabled_globally": "1",
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
    seed_app_settings(conn)


def seed_app_settings(conn):
    """Write each APP_SETTINGS_DEFAULTS key ONCE, only if it isn't there yet.

    Deliberately not `set_setting` — that would overwrite. A value Balaji has
    edited must survive every future deploy.
    """
    for key, value in APP_SETTINGS_DEFAULTS.items():
        row = conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?)", (key, value))
    conn.commit()


def ensure_address_row(conn, cid):
    """Make sure customer `cid` has at least one delivery address row. Returns its id.

    Every map screen — the pin list, the route, the zone, the km allowance — reads
    `customer_addresses`, never the old columns on `customers`. So a customer with
    no row here is INVISIBLE to all of them: they cannot be pinned, and once pinned
    on the customer row they still never appear in the pinned list, because nothing
    on those screens ever looks at that row. This is the one place that guarantees
    a customer can be seen at all.

    Idempotent. When usable rows already exist the DEFAULT one is returned:
    callers use this to know where a single pin belongs, and for a customer with
    a home and an office that has to be the home they deliver to, not whichever
    row happened to be typed in first.

    ACTIVE beats default in the ordering, and a customer whose every address has
    been archived gets a fresh one. The guarantee this function exists to make is
    that a pin always lands somewhere the map screens can see — returning an
    archived row would quietly break it in exactly the way this whole fix is about.
    """
    live = conn.execute(
        "SELECT id FROM customer_addresses WHERE customer_id=? AND active=1 "
        "ORDER BY is_default DESC, id", (cid,)).fetchone()
    if live:
        return live["id"]
    r = conn.execute(
        "SELECT id, address, lat, lng, zone, corridor, drop_type, map_note "
        "FROM customers WHERE id=?", (cid,)).fetchone()
    if r is None:
        return None
    # Created even when there is no address text yet: an empty row is what puts
    # the customer on the "needs a pin" list, which is where the location gets
    # given. Skipping them is exactly how a new customer went missing.
    cur = conn.execute(
        "INSERT INTO customer_addresses(customer_id,label,address,lat,lng,zone,"
        "corridor,drop_type,map_note,is_default,active,sort,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,1,1,10,?)",
        (r["id"], r["drop_type"] or "Home", r["address"], r["lat"], r["lng"], r["zone"],
         r["corridor"], r["drop_type"], r["map_note"], now_iso()))
    conn.commit()
    return cur.lastrowid


def backfill_addresses(conn):
    """Give every customer a delivery address row, on every startup.

    This used to run ONCE, behind a settings flag. That was the bug: every
    customer created after it ran — by the customer form, by a lead conversion,
    by an import — got no address row, and so never appeared on the map pin page
    at all. They could not be pinned, and pinning them from the customer form
    (which writes the old `customers.lat`) left them still missing from the pinned
    list. It is cheap and idempotent, so it now self-heals instead.

    The original columns on `customers` are left untouched as a safety net —
    nothing is moved or deleted, only copied.
    """
    made = 0
    for r in conn.execute("SELECT id FROM customers").fetchall():
        has_any = conn.execute(
            "SELECT 1 FROM customer_addresses WHERE customer_id=?", (r["id"],)).fetchone()
        if not has_any and ensure_address_row(conn, r["id"]):
            made += 1
    if made:
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
