"""
Migration script for Update 6 — adds referral_tier_claimed column.
Run on PythonAnywhere AFTER pulling new code.
Usage: python scripts/migrate_update6.py
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app

app = create_app()
with app.app_context():
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    db_path = db_uri.replace("sqlite:///", "") if "sqlite:///" in db_uri else os.path.join(app.instance_path, "ditto_dinky.db")

if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    sys.exit(1)

print(f"Migrating: {db_path}")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(users)")
columns = [row[1] for row in cursor.fetchall()]

if "referral_tier_claimed" not in columns:
    cursor.execute("ALTER TABLE users ADD COLUMN referral_tier_claimed INTEGER DEFAULT 0")
    print("  [+] Added users.referral_tier_claimed")
else:
    print("  [=] users.referral_tier_claimed already exists")

conn.commit()
conn.close()
print("\nMigration complete! Reload your app.")
