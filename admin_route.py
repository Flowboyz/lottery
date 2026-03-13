from flask import Blueprint, render_template, session, redirect, url_for, request
import sqlite3

admin_bp = Blueprint("admin", __name__)

# ================= ADMIN DASHBOARD =================
@admin_bp.route("/admin")
def admin_dashboard():

    # 🔐 Authentication
    if "user_id" not in session: 
        return redirect(url_for("auth.login"))

    # 🔐 Authorization
    if session.get("role") not in ["admin", "superadmin"]:
        return "❌ Access denied", 403

    conn = sqlite3.connect("lottery.db")
    cur = conn.cursor()

    # 📋 Get all users
    cur.execute("SELECT id, username, balance, role FROM users")
    users = cur.fetchall()

    # 📜 Get all transactions
    cur.execute("""
        SELECT users.username, transactions.action, transactions.amount,
               transactions.method, transactions.time
        FROM transactions
        JOIN users ON users.id = transactions.user_id
        ORDER BY transactions.time DESC
    """)
    transactions = cur.fetchall()

    conn.close()

    return render_template(
        "admin.html",
        users=users,
        transactions=transactions,
        role=session["role"]
    )
