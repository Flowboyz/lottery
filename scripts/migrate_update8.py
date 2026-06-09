"""
Migration for Update 8 — creates game_plays table.
Run on PythonAnywhere AFTER pulling new code.
Usage: python scripts/migrate_update8.py
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

cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_plays'")
if not cursor.fetchone():
    cursor.execute("""
        CREATE TABLE game_plays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            game_type VARCHAR(20) NOT NULL,
            bet_amount FLOAT NOT NULL,
            payout FLOAT DEFAULT 0.0,
            result VARCHAR(10),
            result_data TEXT,
            created_at DATETIME
        )
    """)
    cursor.execute("CREATE INDEX idx_game_plays_user ON game_plays(user_id)")
    cursor.execute("CREATE INDEX idx_game_plays_type ON game_plays(game_type)")
    print("  [+] Created game_plays table with indexes")
else:
    print("  [=] game_plays table already exists")

conn.commit()
conn.close()
print("\nMigration complete! Reload your app.")
