"""
Clean migration script for Aviator (new shared round system) + Color Prediction.
Run this on PythonAnywhere:

    python scripts/migrate_aviator_color.py

Safe to run multiple times.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from app.extensions import db

app = create_app()

MIGRATIONS = [
    # ===================== NEW AVIATOR SHARED ROUND =====================
    """
    CREATE TABLE IF NOT EXISTS aviator_rounds (
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
    """,
    "CREATE INDEX IF NOT EXISTS ix_aviator_rounds_status ON aviator_rounds(status)",
    "CREATE INDEX IF NOT EXISTS ix_aviator_rounds_round_number ON aviator_rounds(round_number)",

    """
    CREATE TABLE IF NOT EXISTS aviator_entries (
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
    """,
    "CREATE INDEX IF NOT EXISTS ix_aviator_entries_round_id ON aviator_entries(round_id)",
    "CREATE INDEX IF NOT EXISTS ix_aviator_entries_user_id ON aviator_entries(user_id)",

    # ===================== COLOR PREDICTION =====================
    """
    CREATE TABLE IF NOT EXISTS color_rounds (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        round_number INTEGER UNIQUE NOT NULL,
        result       TEXT,
        status       TEXT DEFAULT 'open',
        started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        closed_at    DATETIME,
        seed         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_color_rounds_status ON color_rounds(status)",

    """
    CREATE TABLE IF NOT EXISTS color_entries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id    INTEGER NOT NULL REFERENCES color_rounds(id),
        user_id     INTEGER NOT NULL REFERENCES users(id),
        choice      TEXT NOT NULL,
        bet_amount  REAL NOT NULL,
        payout      REAL DEFAULT 0.0,
        result      TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_color_entries_round_id ON color_entries(round_id)",
    "CREATE INDEX IF NOT EXISTS ix_color_entries_user_id ON color_entries(user_id)",
]

SEED_SETTINGS = [
    # Aviator Settings
    ("AVIATOR_ENABLED",    "1",     "Aviator: Enabled (1=yes, 0=no)"),
    ("AVIATOR_HOUSE_EDGE", "4",     "Aviator: House Edge %"),
    ("AVIATOR_MIN_BET",    "50",    "Aviator: Min Bet (₦)"),
    ("AVIATOR_MAX_BET",    "50000", "Aviator: Max Bet (₦)"),

    # Color Settings
    ("COLOR_ENABLED",        "1",    "Color: Enabled (1=yes, 0=no)"),
    ("COLOR_RED_PAYOUT",     "2.0",  "Color: Red Payout"),
    ("COLOR_GREEN_PAYOUT",   "2.0",  "Color: Green Payout"),
    ("COLOR_VIOLET_PAYOUT",  "4.5",  "Color: Violet Payout"),
    ("COLOR_ROUND_DURATION", "30",   "Color: Round Duration (seconds)"),
]

with app.app_context():
    conn = db.engine.raw_connection()
    cur = conn.cursor()

    print("Running migration for Aviator + Color...")

    # Run table creation
    for sql in MIGRATIONS:
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"  ⚠️  Warning: {e}")

    # Add precomputed_data column if missing (for existing databases)
    try:
        cur.execute("PRAGMA table_info(aviator_rounds)")
        columns = [col[1] for col in cur.fetchall()]
        if 'precomputed_data' not in columns:
            print("  → Adding missing column: precomputed_data to aviator_rounds")
            cur.execute("ALTER TABLE aviator_rounds ADD COLUMN precomputed_data TEXT")
            print("  ✅ Added precomputed_data column")
    except Exception as e:
        print(f"  ⚠️  Could not add precomputed_data column: {e}")

    # Seed default settings (only if they don't exist)
    for key, value, description in SEED_SETTINGS:
        cur.execute("SELECT 1 FROM game_settings WHERE key = ?", (key,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO game_settings (key, value, description)
                VALUES (?, ?, ?)
            """, (key, value, description))
            print(f"  ✅ Seeded setting: {key}")

    conn.commit()
    conn.close()

    print("\n✅ Migration completed successfully!")
    print("   - aviator_rounds + aviator_entries created/verified")
    print("   - color_rounds + color_entries created/verified")
    print("   - Default game settings seeded (if missing)")