from flask import Blueprint, render_template, request, redirect, url_for, session
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

auth_bp = Blueprint("auth", __name__)


# ========== REGISTER ==========
@auth_bp.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        hashed = generate_password_hash(password)

        conn = sqlite3.connect("lottery.db")
        cur = conn.cursor()

        try:
            cur.execute(
                "INSERT INTO users (username, password, balance) VALUES (?, ?, 0)",
                (username, hashed)
            )
            conn.commit()
            conn.close()
            return redirect(url_for("auth.login"))

        except:
            conn.close()
            error = "❌ Username already exists"

    return render_template("register.html")


# ========== LOGIN ==========

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect("lottery.db")
        cur = conn.cursor()

        cur.execute(
            "SELECT id, password, role FROM users WHERE username=?",
            (username,)
        )
        user = cur.fetchone()
        conn.close()

        if user:
            user_id, hashed_pw, role = user

            if check_password_hash(hashed_pw, password):
                session["user_id"] = user_id
                session["role"] = role   # 🔑 THIS IS STEP B
                return redirect(url_for("home"))

        return render_template("login.html", error="Invalid login")

    return render_template("login.html")



# ========== LOGOUT ==========
@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
