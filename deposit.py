from flask import Blueprint, render_template, request, redirect, url_for, session
from datetime import datetime

deposit_bp = Blueprint("deposit", __name__)

# ========= Helper function =========
def add_transaction(action, amount, method=None):
    if "history" not in session:
        session["history"] = []

    session["history"].append({
        "action": action,
        "amount": amount,
        "method": method,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


# ========= Deposit Page =========
@deposit_bp.route("/deposit-page")
def deposit_page():
    return render_template("deposit.html")


# ========= Deposit Logic =========
@deposit_bp.route("/deposit", methods=["POST"])
def deposit():

    amount = request.form.get("amount")
    age = request.form.get("age")
    payment = request.form.get("payment")

    if "balance" not in session:
        session["balance"] = 0

    if age != "yes":
        return "❌ Must be 18+ to deposit"

    if not amount or float(amount) < 10:
        return "❌ Minimum deposit is $10"

    session["balance"] += float(amount)

    add_transaction("DEPOSIT", float(amount), payment)

    return redirect(url_for("home"))
