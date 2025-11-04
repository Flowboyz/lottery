from flask import Flask, render_template, request
import random

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/play", methods=["POST"])
def play():
    try:
        num1 = int(request.form["num1"])
        num2 = int(request.form["num2"])
        num3 = int(request.form["num3"])
        total = num1 + num2 + num3
        lucky_number = random.randint(3, 15)

        if total == lucky_number:
            result = f"You won $100! 🎉 Your total: {total}, Lucky number: {lucky_number}"
        else:
            result = f"Sorry, you lost. Your total: {total}, Lucky number: {lucky_number}"

        return render_template("index.html", result=result)

    except ValueError:
        return render_template("index.html", result="Please enter valid numbers between 1 and 5.")

if __name__ == "__main__":
    app.run(debug=True)