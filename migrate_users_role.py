import sqlite3

conn = sqlite3.connect("lottery.db")
cur = conn.cursor()

print("🔄 Starting migration...")

# 1. Create new users table with role column
cur.execute("""
CREATE TABLE IF NOT EXISTS users_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    password TEXT,
    balance REAL DEFAULT 0,
    role TEXT DEFAULT 'user'
)
""")

# 2. Copy old users into new table
# If old table had no role, everyone becomes 'user'
cur.execute("""
INSERT INTO users_new (id, username, password, balance, role)
SELECT id, username, password, balance, 'user'
FROM users
""")

# 3. Drop old users table
cur.execute("DROP TABLE users")

# 4. Rename new table
cur.execute("ALTER TABLE users_new RENAME TO users")

conn.commit()
conn.close()

print("✅ Migration completed successfully!")
