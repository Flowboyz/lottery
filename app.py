from flask import Flask, render_template, request, redirect, url_for, session
import random
import sys

app = Flask(__name__)
app.secret_key = "secret_key_here"  # Needed for session tracking

@app.route("/")
def home():
    # Initialize balance if not set
    if "balance" not in session:
        session["balance"] = 0.00
    return render_template("index.html", balance=session["balance"])

@app.route("/play", methods=["POST"])
def play():
    try:
        num1 = int(request.form["num1"])
        num2 = int(request.form["num2"])
        num3 = int(request.form["num3"])

        # Validate inputs
        for n in [num1, num2, num3]:
            if n < 1 or n > 5:
                return render_template("index.html", 
                                       result="⚠️ Numbers must be between 1 and 5!", 
                                       result_class="warning",
                                       balance=session["balance"])

        # Game logic
        total = num1 + num2 + num3
        lucky_number = random.randint(3, 15)

        if total == lucky_number:
            session["balance"] += 100
            result = f"🎉 You won $100! Your total: {total}, Lucky number: {lucky_number}"
            result_class = "win"
        elif session["balance"] <= 10:
            result = f"you dont have enough balance, click get demo funds to add balance"
            result_class = "Warning"
        else:
            session["balance"] = max(0, session["balance"] - 10)
            result = f"😢 You lost $10. Your total: {total}, Lucky number: {lucky_number}"
            result_class = "lose"

        return render_template("index.html", 
                               result=result, 
                               result_class=result_class, 
                               balance=session["balance"])

    except Exception as e:
        return render_template("index.html", 
                               result=f"❌ Error: {str(e)}", 
                               result_class="warning", 
                               balance=session["balance"])

@app.route("/demo-funds")
def demo_funds():
    if "balance" not in session:
        session["balance"] = 0.00
    session["balance"] += 100
    return redirect(url_for("home"))

@app.route("/reset")
def reset_balance():
    session["balance"] = 0.00
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)
