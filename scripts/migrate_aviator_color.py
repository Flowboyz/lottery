"""
Migration: Aviator (new shared round system) + Color Prediction
Run this on PythonAnywhere after pulling new code.

Usage: python scripts/migrate_aviator_color.py
"""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

app = create_app()

# Find database path
db_path = None
with app.app_context():
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if "sqlite:///" in db_uri:
        db_path = db_uri.replace("sqlite:///", "")
    else:
        db_path = os.path.join(app.instance_path, "ditto_dinky.db")

if not db_path or not os.path.exists(db_path):
    print(f"Database not found at: {db_path}")
    sys.exit(1)

print(f"Migrating database: {db_path}")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()


def table_exists(table_name):
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


def column_exists(table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


print("\n--- Running Aviator + Color Migration ---")

# ===================== AVIATOR ROUNDS =====================
if not table_exists("aviator_rounds"):
    cursor.execute("""
        CREATE TABLE aviator_rounds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            round_number    INTEGER UNIQUE NOT NULL,
            status          TEXT DEFAULT 'betting',
            crash_point     REAL,
            seed            TEXT,
            betting_ends_at DATETIME,
            started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            crashed_at      DATETIME,
            precomputed_data TEXT
        )
    """)
    print("  [+] Created table: aviator_rounds")
else:
    print("  [=] Table already exists: aviator_rounds")

# Add precomputed_data column if missing
if table_exists("aviator_rounds") and not column_exists("aviator_rounds", "precomputed_data"):
    cursor.execute("ALTER TABLE aviator_rounds ADD COLUMN precomputed_data TEXT")
    print("  [+] Added column: aviator_rounds.precomputed_data")


# ===================== AVIATOR ENTRIES =====================
if not table_exists("aviator_entries"):
    cursor.execute("""
        CREATE TABLE aviator_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id     INTEGER NOT NULL REFERENCES aviator_rounds(id),
            user_id      INTEGER NOT NULL REFERENCES users(id),
            bet_amount   REAL NOT NULL,
            cashout_at   REAL,
            payout       REAL DEFAULT 0.0,
            result       TEXT,
            auto_cashout REAL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("  [+] Created table: aviator_entries")
else:
    print("  [=] Table already exists: aviator_entries")


# ===================== COLOR ROUNDS =====================
if not table_exists("color_rounds"):
    cursor.execute("""
        CREATE TABLE color_rounds (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            round_number INTEGER UNIQUE NOT NULL,
            result       TEXT,
            status       TEXT DEFAULT 'open',
            started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at    DATETIME,
            seed         TEXT
        )
    """)
    print("  [+] Created table: color_rounds")
else:
    print("  [=] Table already exists: color_rounds")


# ===================== COLOR ENTRIES =====================
if not table_exists("color_entries"):
    cursor.execute("""
        CREATE TABLE color_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id    INTEGER NOT NULL REFERENCES color_rounds(id),
            user_id     INTEGER NOT NULL REFERENCES users(id),
            choice      TEXT NOT NULL,
            bet_amount  REAL NOT NULL,
            payout      REAL DEFAULT 0.0,
            result      TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("  [+] Created table: color_entries")
else:
    print("  [=] Table already exists: color_entries")


# ===================== SEED DEFAULT SETTINGS =====================
settings_to_seed = [
    ("AVIATOR_ENABLED",    "1",     "Aviator: Enabled (1=yes, 0=no)"),
    ("AVIATOR_HOUSE_EDGE", "4",     "Aviator: House Edge %"),
    ("AVIATOR_MIN_BET",    "50",    "Aviator: Minimum Bet (₦)"),
    ("AVIATOR_MAX_BET",    "50000", "Aviator: Maximum Bet (₦)"),
    ("COLOR_ENABLED",        "1",    "Color: Enabled (1=yes, 0=no)"),
    ("COLOR_RED_PAYOUT",     "2.0",  "Color: Red Payout Multiplier"),
    ("COLOR_GREEN_PAYOUT",   "2.0",  "Color: Green Payout Multiplier"),
    ("COLOR_VIOLET_PAYOUT",  "4.5",  "Color: Violet Payout Multiplier"),
]

for key, value, label in settings_to_seed:
    cursor.execute("SELECT 1 FROM game_settings WHERE key = ?", (key,))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO game_settings (key, value, label)
            VALUES (?, ?, ?)
        """, (key, value, label))
        print(f"  [+] Seeded setting: {key}")
    else:
        print(f"  [=] Setting already exists: {key}")


conn.commit()
conn.close()

print("\n✅ Migration completed successfully!")
print("   - aviator_rounds + aviator_entries ready")
print("   - color_rounds + color_entries ready")
print("   - Default game settings seeded")