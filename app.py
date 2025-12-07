from flask import Flask, render_template, request, session
import random

# Import blueprints
from deposit import deposit_bp
from withdraw import withdraw_bp
from admin_route import *


app = Flask(__name__)
app.secret_key = "super-secret-key"

# Register blueprints
app.register_blueprint(deposit_bp)
app.register_blueprint(withdraw_bp)
app.register_blueprint(admin_bp)


# ========== HOME ==========
@app.route("/")
def home():

    if "balance" not in session:
        session["balance"] = 0

    if "history" not in session:
        session["history"] = []

    return render_template(
        "index.html",
        balance=session["balance"],
        history=session["history"]
    )


# ========== GAME ==========
@app.route("/play", methods=["POST"])
def play():

    if session.get("balance", 0) < 10:
        return render_template(
            "index.html",
            balance=session["balance"],
            result="❌ You must deposit before playing"
        )

    num1 = int(request.form["num1"])
    num2 = int(request.form["num2"])
    num3 = int(request.form["num3"])

    total = num1 + num2 + num3
    lucky = random.randint(3, 15)

    if total == lucky:
        session["balance"] += 100

        session["history"].append({
            "action": "WIN",
            "amount": 100
        })

        result = f"🎉 YOU WON $100 | Total: {total} | Lucky: {lucky}"

    else:
        session["balance"] -= 10

        if session["balance"] < 0:
            session["balance"] = 0

        session["history"].append({
            "action": "LOSS",
            "amount": -10
        })

        result = f"❌ You Lost $10 | Total: {total} | Lucky: {lucky}"

    return render_template(
        "index.html",
        balance=session["balance"],
        result=result,
        history=session["history"]
    )

@app.route("/reset")
def reset():
    if ("reset"):
        session["balance"] == 0,
        return render_template("deposit.html",
                               result = "you have cleared your balance")
        
    
    

if __name__ == "__main__":
    app.run(debug=True)
