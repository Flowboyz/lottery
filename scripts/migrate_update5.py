"""
Migration script for Update 5.
Run on PythonAnywhere AFTER pulling the new code.
This safely adds new columns and tables WITHOUT deleting existing data.

Usage: python scripts/migrate_update5.py
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

app = create_app()

# Find the database file
db_path = None
with app.app_context():
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if "sqlite:///" in db_uri:
        db_path = db_uri.replace("sqlite:///", "")
    else:
        # Default location
        db_path = os.path.join(app.instance_path, "ditto_dinky.db")

if not db_path or not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    print("If your DB is elsewhere, edit this script.")
    sys.exit(1)

print(f"Migrating database: {db_path}")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()


def column_exists(table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def table_exists(table):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


# ─── Add new columns to users table ───
new_user_columns = [
    ("is_suspended", "BOOLEAN DEFAULT 0"),
    ("suspended_until", "DATETIME"),
    ("is_banned", "BOOLEAN DEFAULT 0"),
    ("ban_reason", "VARCHAR(200)"),
]

for col_name, col_type in new_user_columns:
    if not column_exists("users", col_name):
        cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        print(f"  [+] Added users.{col_name}")
    else:
        print(f"  [=] users.{col_name} already exists")


# ─── Create game_settings table ───
if not table_exists("game_settings"):
    cursor.execute("""
        CREATE TABLE game_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key VARCHAR(50) UNIQUE NOT NULL,
            value VARCHAR(200) NOT NULL,
            label VARCHAR(100),
            updated_at DATETIME,
            updated_by INTEGER REFERENCES users(id)
        )
    """)
    print("  [+] Created game_settings table")
else:
    print("  [=] game_settings table already exists")


# ─── Create announcements table ───
if not table_exists("announcements"):
    cursor.execute("""
        CREATE TABLE announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title VARCHAR(200) NOT NULL,
            message TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_by INTEGER REFERENCES users(id),
            created_at DATETIME,
            expires_at DATETIME
        )
    """)
    print("  [+] Created announcements table")
else:
    print("  [=] announcements table already exists")


conn.commit()
conn.close()

print("\nMigration complete! Reload your app on PythonAnywhere.")
