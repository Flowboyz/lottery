"""
Utility helpers.
"""
import secrets
import string
from datetime import datetime, date
from functools import wraps

from flask import current_app, flash, redirect, url_for, request
from flask_login import current_user

from app.extensions import db
from app.models import Transaction, Notification, AuditLog


# ---------------------------------------------------------------------------
# Currency Formatting
# ---------------------------------------------------------------------------
def format_money(amount):
    """Format a number as Nigerian Naira."""
    symbol = current_app.config.get("CURRENCY_SYMBOL", "₦")
    return f"{symbol}{amount:,.2f}"


def naira(amount):
    """Shorthand for Jinja templates."""
    return f"₦{amount:,.2f}"


# ---------------------------------------------------------------------------
# Wallet Operations (ledger-safe)
# ---------------------------------------------------------------------------
def credit_wallet(user, amount, action, description="", method=None, reference=None):
    """Add money to user wallet with ledger record."""
    balance_before = user.balance
    user.balance += amount
    balance_after = user.balance

    txn = Transaction(
        user_id=user.id,
        action=action,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        reference=reference or generate_reference(),
        description=description,
        method=method,
        status="completed",
    )
    db.session.add(txn)
    db.session.commit()
    return txn


def debit_wallet(user, amount, action, description="", method=None, reference=None):
    """Remove money from user wallet with ledger record."""
    if user.balance < amount:
        return None
    balance_before = user.balance
    user.balance -= amount
    balance_after = user.balance

    txn = Transaction(
        user_id=user.id,
        action=action,
        amount=-amount,
        balance_before=balance_before,
        balance_after=balance_after,
        reference=reference or generate_reference(),
        description=description,
        method=method,
        status="completed",
    )
    db.session.add(txn)
    db.session.commit()
    return txn


# ---------------------------------------------------------------------------
# Reference Generator
# ---------------------------------------------------------------------------
def generate_reference(prefix="DD"):
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    rand = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"{prefix}-{ts}-{rand}"


# ---------------------------------------------------------------------------
# Notification Helper
# ---------------------------------------------------------------------------
def notify_user(user_id, title, message, category="info"):
    n = Notification(
        user_id=user_id,
        title=title,
        message=message,
        category=category,
    )
    db.session.add(n)
    db.session.commit()


# ---------------------------------------------------------------------------
# Email Helper
# ---------------------------------------------------------------------------
def send_email(to_email, subject, body):
    """Send an email. Fails silently if mail is not configured."""
    if not to_email:
        return
    try:
        from flask_mail import Message
        from app.extensions import mail
        msg = Message(
            subject=subject,
            recipients=[to_email],
            body=body,
            sender=current_app.config.get("MAIL_DEFAULT_SENDER", "noreply@dittodinky.com"),
        )
        mail.send(msg)
    except Exception as e:
        # Log but don't crash if email fails
        current_app.logger.warning(f"Email send failed to {to_email}: {e}")


# ---------------------------------------------------------------------------
# Real IP Helper (PythonAnywhere / reverse proxy support)
# ---------------------------------------------------------------------------
def get_real_ip():
    """Get the real client IP behind a reverse proxy."""
    if request:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.remote_addr
    return None


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------
def log_audit(action, details=None, user_id=None):
    entry = AuditLog(
        user_id=user_id or (current_user.id if current_user and current_user.is_authenticated else None),
        action=action,
        details=details,
        ip_address=get_real_ip(),
    )
    db.session.add(entry)
    db.session.commit()


# ---------------------------------------------------------------------------
# Role Decorators
# ---------------------------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if current_user.role not in ("admin", "superadmin"):
            flash("Access denied.", "error")
            return redirect(url_for("game.home"))
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if current_user.role != "superadmin":
            flash("Access denied.", "error")
            return redirect(url_for("game.home"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Daily Bet Limit Check
# ---------------------------------------------------------------------------
def check_daily_bet_limit(user, bet_amount):
    """Returns True if bet is within daily limit."""
    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
        db.session.commit()
    max_daily = current_app.config.get("MAX_DAILY_BET", 50000)
    return (user.daily_bet_total + bet_amount) <= max_daily
