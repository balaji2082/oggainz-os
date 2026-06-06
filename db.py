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
DB_PATH = os.path.join(APP_DIR, "oggainz.db")

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

CREATE INDEX IF NOT EXISTS idx_deliveries_date ON deliveries(date);
CREATE INDEX IF NOT EXISTS idx_deliveries_sub  ON deliveries(subscription_id);
CREATE INDEX IF NOT EXISTS idx_subs_status     ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subs_customer   ON subscriptions(customer_id);
"""

# Columns added after v1 shipped — added in-place so existing databases keep their data.
MIGRATIONS = {
    "deliveries":    [("trial_id", "TEXT")],
    "trial_orders":  [("notes", "TEXT"), ("delivery_date", "TEXT"),
                      ("meal_type", "TEXT"), ("delivery_status", "TEXT")],
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
