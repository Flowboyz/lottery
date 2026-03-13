from flask import Blueprint, session, redirect, url_for, request
import sqlite3

superadmin_bp = Blueprint("superadmin", __name__)

@superadmin_bp.route("/admin/update-balance", methods=["POST"])
def update_balance():

    # 🔐 Must be logged in
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    # 🔐 SUPERADMIN ONLY
    if session.get("role") != "superadmin":
        return "❌ Access denied", 403

    user_id = request.form.get("user_id")
    new_balance = request.form.get("balance")

    if not user_id or new_balance is None:
        return "❌ Invalid data", 400

    conn = sqlite3.connect("lottery.db")
    cur = conn.cursor()

    cur.execute(
        "UPDATE users SET balance=? WHERE id=?",
        (float(new_balance), user_id)
    )

    conn.commit()
    conn.close()

    return redirect(url_for("admin.admin_dashboard"))
