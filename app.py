from flask import Flask, flash, render_template, request, session, redirect, url_for
import random
import sqlite3
import os
import time
from dotenv import load_dotenv

from db import init_db
from auth import auth_bp
from deposit import deposit_bp
from withdraw import withdraw_bp
from admin_route import admin_bp
from superadmin import superadmin_bp

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

init_db()

app.register_blueprint(auth_bp)
app.register_blueprint(deposit_bp)
app.register_blueprint(withdraw_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(superadmin_bp)

# 🎯 CONTROLLED WIN PROBABILITY
WIN_PROBABILITY = 0.1
PAYOUT_MULTIPLIER = 5
COOLDOWN_SECONDS = 5  # seconds

#home route
@app.route("/")
def home():

    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    conn = sqlite3.connect("lottery.db")
    cur = conn.cursor()

    cur.execute("SELECT balance FROM users WHERE id=?", (session["user_id"],))
    balance = cur.fetchone()[0]
    
    cur.execute("""
    SELECT lucky_number
    FROM transactions
    WHERE user_id=?
    ORDER BY time DESC
    LIMIT 5 
    """, (session["user_id"],))
    
    recent_lucky = [row[0] for row in cur.fetchall()]
    
    current_time = int(time.time())

    cur.execute("SELECT last_play_time FROM users WHERE id=?", (session["user_id"],))
    last_play = cur.fetchone()[0]

    remaining_cooldown = 0

    if last_play:
        elapsed = current_time - last_play
        if elapsed < COOLDOWN_SECONDS:
            remaining_cooldown = COOLDOWN_SECONDS - elapsed

    conn.close()

    return render_template( "index.html", 
                            balance=balance, 
                            history=[],
                            win_probability=int(WIN_PROBABILITY * 100),
                            remaining_cooldown=remaining_cooldown,
                            recent_lucky=recent_lucky
                        )

#play route from the index
@app.route("/play", methods=["POST"])
def play():

    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    conn = sqlite3.connect("lottery.db")
    cur = conn.cursor()

    cur.execute("SELECT balance FROM users WHERE id=?", (session["user_id"],))
    balance = cur.fetchone()[0]

    bet = float(request.form.get("bet", 0))
    
    #⏳ Cooldown check============================
    current_time = int(time.time())

    cur.execute("SELECT last_play_time FROM users WHERE id=?", (session["user_id"],))
    last_play = cur.fetchone()[0]

    if last_play and current_time - last_play < COOLDOWN_SECONDS:
        flash(f"⏳ Wait {COOLDOWN_SECONDS} seconds before playing again", "error")
        conn.close()
        return redirect(url_for("home"))
    
    cur.execute("UPDATE users SET last_play_time=? WHERE id=?",(current_time, session["user_id"]))
    #====================================
    
    #===============grab the input numbers and validate them=============
    if bet <= 0:
        flash("❌ Bet must be greater than 0", "error")
        return redirect(url_for("home"))

    if bet > balance:
        flash("❌ Insufficient balance", "error")
        return redirect(url_for("home"))

    try:
        num1 = int(request.form.get("num1", 0))
        num2 = int(request.form.get("num2", 0))
        num3 = int(request.form.get("num3", 0))
    except:
        flash("❌ Invalid input", "error")
        return redirect(url_for("home"))

    if not num1 or not num2 or not num3:
        flash("❌ Select all numbers", "error")
        return redirect(url_for("home"))

    total = num1 + num2 + num3
    #=============================================
    
    #==============win block=================
    win = random.random() < WIN_PROBABILITY

    if win:
        lucky = total
        payout = bet * PAYOUT_MULTIPLIER
        balance += payout
        result = f"🎉 YOU WON ${payout}"
        action = "WIN"
        amount = payout
    else:
        lucky = random.randint(3, 15)
        while lucky == total:
            lucky = random.randint(3, 15)

        balance -= bet
        result = f"❌ You lost ${bet}"
        action = "LOSS"
        amount = -bet
    #====================================

    # 🔐 Always update DB
    cur.execute("UPDATE users SET balance=? WHERE id=?", (balance, session["user_id"]))

    cur.execute(
        """INSERT INTO transactions 
        (user_id, action, amount, lucky_number, picked_total, bet_amount)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (session["user_id"], action, amount, lucky, total, bet)
    )

    conn.commit()
    conn.close()

    flash({
        "result": result,
        "balance": balance,
        "lucky": lucky,
        "total": total
    }, "game_result")

    return redirect(url_for("home"))

@app.route("/history") #each users history
def history():

    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    conn = sqlite3.connect("lottery.db")
    cur = conn.cursor()

    cur.execute("""
        SELECT action, amount, method, time
        FROM transactions
        WHERE user_id=?
        ORDER BY time DESC
    """, (session["user_id"],))

    history = cur.fetchall()
    conn.close()

    return render_template("history.html", history=history)



if __name__ == "__main__":
    app.run(debug=True)
