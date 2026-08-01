"""
Profile blueprint - user details, edit profile, saved bank accounts.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import BankAccount, User

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


@profile_bp.route("/")
@login_required
def index():
    from app.models import GamePlay
    from app.utils import get_setting, check_daily_bet_limit

    banks = BankAccount.query.filter_by(user_id=current_user.id).order_by(
        BankAccount.is_default.desc(), BankAccount.created_at.desc()
    ).all()
    referral_count = User.query.filter_by(referred_by=current_user.id).count()
    # Recent game history (all game types inc. aviator & color)
    game_history = GamePlay.query.filter_by(
        user_id=current_user.id
    ).order_by(GamePlay.created_at.desc()).limit(20).all()

    # Daily betting limit (force date check/reset for accurate display)
    check_daily_bet_limit(current_user, 0)
    daily_spent = round(current_user.daily_bet_total or 0.0, 2)
    max_daily = get_setting("MAX_DAILY_BET", 50000)

    from datetime import datetime
    from flask import current_app
    is_link_active = False
    if current_user.telegram_link_token and current_user.telegram_link_expires:
        if current_user.telegram_link_expires > datetime.utcnow():
            is_link_active = True
    bot_username = get_setting("TELEGRAM_BOT_USERNAME") or current_app.config.get("TELEGRAM_BOT_USERNAME") or "your_bot_username"

    return render_template("profile/index.html", banks=banks,
                           referral_count=referral_count, game_history=game_history,
                           daily_spent=daily_spent, max_daily=max_daily,
                           is_link_active=is_link_active, bot_username=bot_username)


# ────────────────────────── EDIT PROFILE ──────────────────────────
@profile_bp.route("/edit", methods=["POST"])
@login_required
def edit_profile():
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    # Update email
    if email and email != (current_user.email or ""):
        existing = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing:
            flash("That email is already in use by another account.", "error")
            return redirect(url_for("profile.index"))
        current_user.email = email

    # Update phone
    if phone != (current_user.phone or ""):
        current_user.phone = phone if phone else None

    # Update password (only if they filled in the password fields)
    if new_password:
        if not current_password:
            flash("Enter your current password to change it.", "error")
            return redirect(url_for("profile.index"))
        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("profile.index"))
        if len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
            return redirect(url_for("profile.index"))
        if new_password != confirm_password:
            flash("New passwords do not match.", "error")
            return redirect(url_for("profile.index"))
        current_user.set_password(new_password)

    db.session.commit()
    flash("Profile updated successfully.", "success")
    return redirect(url_for("profile.index"))


# ────────────────────────── ADD BANK ACCOUNT ──────────────────────────
@profile_bp.route("/add-bank", methods=["POST"])
@login_required
def add_bank():
    bank_name = request.form.get("bank_name", "").strip()
    account_number = request.form.get("account_number", "").strip()
    account_name = request.form.get("account_name", "").strip()

    if not bank_name or not account_number or not account_name:
        flash("All bank fields are required.", "error")
        return redirect(url_for("profile.index"))

    if len(account_number) != 10 or not account_number.isdigit():
        flash("Account number must be exactly 10 digits.", "error")
        return redirect(url_for("profile.index"))

    # Check for duplicate
    existing = BankAccount.query.filter_by(
        user_id=current_user.id,
        account_number=account_number,
        bank_name=bank_name,
    ).first()
    if existing:
        flash("This bank account is already saved.", "info")
        return redirect(url_for("profile.index"))

    # If first account, make it default
    has_accounts = BankAccount.query.filter_by(user_id=current_user.id).count()
    is_default = has_accounts == 0

    bank = BankAccount(
        user_id=current_user.id,
        bank_name=bank_name,
        account_number=account_number,
        account_name=account_name,
        is_default=is_default,
    )
    db.session.add(bank)
    db.session.commit()

    flash(f"Bank account ({bank_name} - {account_number}) saved.", "success")
    return redirect(url_for("profile.index"))


# ────────────────────────── DELETE BANK ACCOUNT ──────────────────────────
@profile_bp.route("/delete-bank/<int:bank_id>", methods=["POST"])
@login_required
def delete_bank(bank_id):
    bank = db.session.get(BankAccount, bank_id)
    if not bank or bank.user_id != current_user.id:
        flash("Bank account not found.", "error")
        return redirect(url_for("profile.index"))

    was_default = bank.is_default
    db.session.delete(bank)
    db.session.commit()

    if was_default:
        next_bank = BankAccount.query.filter_by(user_id=current_user.id).first()
        if next_bank:
            next_bank.is_default = True
            db.session.commit()

    flash("Bank account removed.", "success")
    return redirect(url_for("profile.index"))


# ────────────────────────── SET DEFAULT BANK ──────────────────────────
@profile_bp.route("/set-default-bank/<int:bank_id>", methods=["POST"])
@login_required
def set_default_bank(bank_id):
    bank = db.session.get(BankAccount, bank_id)
    if not bank or bank.user_id != current_user.id:
        flash("Bank account not found.", "error")
        return redirect(url_for("profile.index"))

    BankAccount.query.filter_by(user_id=current_user.id).update({"is_default": False})
    bank.is_default = True
    db.session.commit()

    flash(f"{bank.bank_name} - {bank.account_number} set as default.", "success")
    return redirect(url_for("profile.index"))