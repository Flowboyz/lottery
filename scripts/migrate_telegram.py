"""
Migration for Telegram Bot columns.
Run on PythonAnywhere after pulling code.
Usage: python scripts/migrate_telegram.py
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

# Get existing columns
cursor.execute("PRAGMA table_info(users)")
columns = [col[1] for col in cursor.fetchall()]

if "telegram_user_id" not in columns:
    cursor.execute("ALTER TABLE users ADD COLUMN telegram_user_id VARCHAR(50)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_user_id ON users(telegram_user_id)")
    print("  [+] Added telegram_user_id column and index")
else:
    print("  [=] telegram_user_id column already exists")

if "telegram_link_token" not in columns:
    cursor.execute("ALTER TABLE users ADD COLUMN telegram_link_token VARCHAR(32)")
    print("  [+] Added telegram_link_token column")
else:
    print("  [=] telegram_link_token column already exists")

if "telegram_link_expires" not in columns:
    cursor.execute("ALTER TABLE users ADD COLUMN telegram_link_expires DATETIME")
    print("  [+] Added telegram_link_expires column")
else:
    print("  [=] telegram_link_expires column already exists")

conn.commit()
conn.close()
print("\nMigration complete! Reload your app.")
