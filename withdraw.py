from flask import Blueprint, render_template, request, redirect, url_for, session
from datetime import datetime

withdraw_bp = Blueprint("withdraw", __name__)

def add_transaction(action, amount, method=None):
    if "history" not in session:
        session["history"] = []

    session["history"].append({
        "action": action,
        "amount": amount,
        "method": method,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


# ========= Withdraw Page =========
@withdraw_bp.route("/withdraw-page")
def withdraw_page():
    return render_template("withdraw.html")


# ========= Withdraw Logic =========
@withdraw_bp.route("/withdraw", methods=["POST"])
def withdraw():

    amount = request.form.get("amount")
    method = request.form.get("method")

    if "balance" not in session:
        session["balance"] = 0

    if not amount or float(amount) < 10:
        return "❌ Minimum withdrawal is $10"

    if float(amount) > session["balance"]:
        return "❌ Insufficient balance"

    session["balance"] -= float(amount)

    add_transaction("WITHDRAW", float(amount), method)

    return redirect(url_for("home"))
