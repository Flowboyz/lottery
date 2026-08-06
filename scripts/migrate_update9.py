"""
Migration for Update 9 — adds last_active to users table.
Run on PythonAnywhere AFTER pulling new code.
Usage: python scripts/migrate_update9.py
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

# Check if last_active column exists
cursor.execute("PRAGMA table_info(users)")
columns = [col[1] for col in cursor.fetchall()]

if "last_active" not in columns:
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_active DATETIME")
        print("  [+] Added last_active to users table")
    except Exception as e:
        print(f"  [!] Failed to add last_active: {e}")
else:
    print("  [=] last_active column already exists in users table")

conn.commit()
conn.close()
print("\nMigration complete! Also make sure to run 'python scripts/upgrade_db.py' to create the whot_challenges table.")
