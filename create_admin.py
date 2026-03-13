import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect("lottery.db")
cur = conn.cursor()

username = "flowboy"
password = generate_password_hash("samedo333")  # password hashing for security

cur.execute("""
INSERT OR IGNORE INTO users (username, password, balance)
VALUES (?, ?, 100)
""", (username, password))

conn.commit()
conn.close()

print("Admin created securely.")
