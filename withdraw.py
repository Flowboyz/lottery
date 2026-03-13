from flask import Blueprint, render_template, request, redirect, url_for, session
import sqlite3

withdraw_bp = Blueprint("withdraw", __name__)


# ========= Withdraw Page =========
@withdraw_bp.route("/withdraw-page")
def withdraw_page():
    return render_template("withdraw.html")


# ========= Withdraw Logic =========
@withdraw_bp.route("/withdraw", methods=["POST"])
def withdraw():

    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    amount = request.form.get("amount")
    method = request.form.get("method")

    if not amount or float(amount) < 10:
        return "❌ Minimum withdrawal is $10"

    conn = sqlite3.connect("lottery.db")
    cur = conn.cursor()

    # get current balance
    cur.execute("SELECT balance FROM users WHERE id=?", (session["user_id"],))
    balance = cur.fetchone()[0]

    if float(amount) > balance:
        conn.close()
        return "❌ Insufficient balance"

    # update balance
    new_balance = balance - float(amount)
    cur.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, session["user_id"]))

    # save transaction for admin
    cur.execute(
        "INSERT INTO transactions (user_id, action, amount) VALUES (?, ?, ?)",
        (session["user_id"], "WITHDRAW", -float(amount))
    )

    conn.commit()
    conn.close()

    return redirect(url_for("home"))
