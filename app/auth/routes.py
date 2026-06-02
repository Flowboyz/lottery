"""
Authentication blueprint - register, login, logout, OTP, password reset.
"""
import time
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db, login_manager, mail
from app.models import User, OTP
from app.utils import notify_user, log_audit, credit_wallet

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ────────────────────────── REGISTER ──────────────────────────
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("game.home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        referral = request.form.get("referral_code", "").strip()

        # Validation
        errors = []
        if len(username) < 3:
            errors.append("Username must be at least 3 characters.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != password2:
            errors.append("Passwords do not match.")
        if User.query.filter_by(username=username).first():
            errors.append("Username already taken.")
        if email and User.query.filter_by(email=email).first():
            errors.append("Email already registered.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/register.html")

        user = User(username=username, email=email or None)
        user.set_password(password)
        user.generate_referral_code()
        db.session.add(user)
        db.session.commit()

        # Signup bonus
        bonus = current_app.config.get("SIGNUP_BONUS", 100)
        if bonus > 0:
            credit_wallet(user, bonus, "BONUS", description="Welcome signup bonus")
            notify_user(user.id, "Welcome Bonus!", f"You received ₦{bonus:,.0f} as a signup bonus!", "info")

        # Referral handling
        if referral:
            referrer = User.query.filter_by(referral_code=referral).first()
            if referrer and referrer.id != user.id:
                user.referred_by = referrer.id
                db.session.commit()
                ref_bonus = current_app.config.get("REFERRAL_BONUS", 200)
                credit_wallet(referrer, ref_bonus, "REFERRAL",
                              description=f"Referral bonus for inviting {username}")
                notify_user(referrer.id, "Referral Bonus!",
                            f"You earned ₦{ref_bonus:,.0f} for referring {username}!", "info")

        log_audit("REGISTER", f"New user registered: {username}", user.id)
        flash("Account created successfully! Please login.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


# ────────────────────────── LOGIN ──────────────────────────
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("game.home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if not user:
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html")

        # Check lockout
        if user.is_locked:
            remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
            flash(f"Account locked. Try again in {remaining} minutes.", "error")
            return render_template("auth/login.html")

        if not user.check_password(password):
            user.failed_login_attempts += 1
            max_attempts = current_app.config.get("MAX_LOGIN_ATTEMPTS", 5)
            lockout_min = current_app.config.get("LOGIN_LOCKOUT_MINUTES", 15)
            if user.failed_login_attempts >= max_attempts:
                user.locked_until = datetime.utcnow() + timedelta(minutes=lockout_min)
                log_audit("ACCOUNT_LOCKED", f"User {username} locked after {max_attempts} failed attempts", user.id)
            db.session.commit()
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("Your account has been deactivated.", "error")
            return render_template("auth/login.html")

        if user.is_self_excluded and user.self_exclusion_until and user.self_exclusion_until > datetime.utcnow():
            flash("You are currently self-excluded from playing.", "error")
            return render_template("auth/login.html")

        # Successful login
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_ip = request.remote_addr
        db.session.commit()

        login_user(user, remember=True)
        log_audit("LOGIN", f"User {username} logged in", user.id)

        next_page = request.args.get("next")
        return redirect(next_page or url_for("game.home"))

    return render_template("auth/login.html")


# ────────────────────────── LOGOUT ──────────────────────────
@auth_bp.route("/logout")
@login_required
def logout():
    log_audit("LOGOUT", f"User {current_user.username} logged out")
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# ────────────────────────── FORGOT PASSWORD ──────────────────────────
@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            code = OTP.generate(user.id, "reset_password",
                                current_app.config.get("OTP_EXPIRY_MINUTES", 10))
            # In production, send via email. For now, flash it.
            from app.utils import send_email
            send_email(email, "Password Reset - Ditto Dinky",
                    f"Hi,\n\nYour password reset code is: {code}\n\n"
                    f"This code expires in 10 minutes.\n\n"
                    f"If you did not request this, please ignore this email.\n\n"
                    f"- Ditto Dinky Team")
            flash("A reset code has been sent to your email.", "info")
            return redirect(url_for("auth.reset_password", user_id=user.id))
        flash("If that email exists, an OTP has been sent.", "info")

    return render_template("auth/forgot_password.html")


# ────────────────────────── RESET PASSWORD ──────────────────────────
@auth_bp.route("/reset-password/<int:user_id>", methods=["GET", "POST"])
def reset_password(user_id):
    if request.method == "POST":
        code = request.form.get("otp", "").strip()
        new_password = request.form.get("password", "")

        otp = OTP.query.filter_by(user_id=user_id, purpose="reset_password",
                                   code=code, is_used=False).first()
        if not otp or not otp.is_valid:
            flash("Invalid or expired OTP.", "error")
            return render_template("auth/reset_password.html", user_id=user_id)

        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("auth/reset_password.html", user_id=user_id)

        user = db.session.get(User, user_id)
        user.set_password(new_password)
        otp.is_used = True
        db.session.commit()

        log_audit("PASSWORD_RESET", f"Password reset for user {user.username}", user.id)
        flash("Password reset successfully! Please login.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", user_id=user_id)
