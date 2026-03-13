from flask import Blueprint, render_template, request, redirect, url_for, session
import sqlite3

deposit_bp = Blueprint("deposit", __name__)

# ========= Deposit Page =========
@deposit_bp.route("/deposit-page")
def deposit_page():
    return render_template("deposit.html")


# ========= Deposit Logic =========
@deposit_bp.route("/deposit", methods=["POST"])
def deposit():

    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    amount = request.form.get("amount")
    age = request.form.get("age")
    payment = request.form.get("payment")  # optional, for record later

    if age != "yes":
        return "❌ Must be 18+ to deposit"

    if not amount or float(amount) < 10:
        return "❌ Minimum deposit is $10"

    conn = sqlite3.connect("lottery.db")
    cur = conn.cursor()

    # get current balance
    cur.execute("SELECT balance FROM users WHERE id=?", (session["user_id"],))
    balance = cur.fetchone()[0]

    new_balance = balance + float(amount)

    # update balance
    cur.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, session["user_id"]))

    # save transaction for admin
    cur.execute(
        "INSERT INTO transactions (user_id, action, amount) VALUES (?, ?, ?)",
        (session["user_id"], "DEPOSIT", float(amount))
    )

    conn.commit()
    conn.close()

    return redirect(url_for("home"))
