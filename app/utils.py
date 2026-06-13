"""
Utility helpers.
"""
import secrets
import string
from datetime import datetime, date
from functools import wraps

from flask import current_app, flash, redirect, url_for, request, session
from flask_login import current_user

from app.extensions import db
from app.models import Transaction, Notification, AuditLog


# ---------------------------------------------------------------------------
# Currency Formatting
# ---------------------------------------------------------------------------
def format_money(amount):
    symbol = current_app.config.get("CURRENCY_SYMBOL", "₦")
    return f"{symbol}{amount:,.2f}"


def naira(amount):
    return f"₦{amount:,.2f}"


# ---------------------------------------------------------------------------
# Game Settings (DB-backed with config fallback)
# ---------------------------------------------------------------------------

def get_setting(key, default=None):
    """
    Get setting value and try to convert it to the same type as the default.
    This prevents 'int vs str' and similar type errors.
    """
    from app.models import GameSettings

    try:
        setting = GameSettings.query.filter_by(key=key).first()
        if not setting:
            return default

        value = setting.value

        # If no value, return default
        if value is None or value == "":
            return default

        # Try to convert to the same type as default
        if isinstance(default, bool):
            return str(value).lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            return int(float(value))  # handles "10.0" -> 10
        elif isinstance(default, float):
            return float(value)
        else:
            return value

    except Exception:
        return default
# ---------------------------------------------------------------------------
# Wallet Operations (ledger-safe)
# ---------------------------------------------------------------------------
def credit_wallet(user, amount, action, description="", method=None, reference=None):
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
    if not to_email:
        return
    import time
    from flask_mail import Message
    from app.extensions import mail
    for attempt in range(3):
        try:
            msg = Message(
                subject=subject,
                recipients=[to_email],
                body=body,
                sender=current_app.config.get("MAIL_DEFAULT_SENDER", "noreply@dittodinky.com"),
            )
            mail.send(msg)
            return
        except Exception as e:
            current_app.logger.warning(f"Email attempt {attempt+1} failed to {to_email}: {e}")
            if attempt < 2:
                time.sleep(3)
# ---------------------------------------------------------------------------
# Admin Alert (email superadmin on critical actions)
# ---------------------------------------------------------------------------
def admin_alert(action, details):
    """Send email to all superadmins about critical admin actions."""
    from app.models import User
    superadmins = User.query.filter_by(role="superadmin").all()
    for sa in superadmins:
        if sa.email:
            send_email(
                sa.email,
                f"Admin Alert: {action} - Ditto Dinky",
                f"ADMIN ALERT\n\n"
                f"Action: {action}\n"
                f"By: {current_user.username if current_user.is_authenticated else 'Unknown'}\n"
                f"Details: {details}\n"
                f"Time: {datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')}\n"
                f"IP: {get_real_ip()}\n\n"
                f"- Ditto Dinky Security"
            )


# ---------------------------------------------------------------------------
# Referral Tier Rewards
# ---------------------------------------------------------------------------
REFERRAL_TIERS = [
    (5, 2000, "Bronze Referrer"),
    (10, 5000, "Silver Referrer"),
    (20, 10000, "Gold Referrer"),
    (50, 25000, "Diamond Referrer"),
]


def check_referral_tiers(referrer):
    """Check if a referrer has crossed a new tier and award bonus."""
    from app.models import User
    referral_count = User.query.filter_by(referred_by=referrer.id).count()
    already_claimed = referrer.referral_tier_claimed or 0

    for threshold, bonus, title in REFERRAL_TIERS:
        if referral_count >= threshold and already_claimed < threshold:
            credit_wallet(referrer, bonus, "REFERRAL",
                          description=f"{title} reward — {threshold} referrals!")
            notify_user(referrer.id, f"{title} Unlocked!",
                        f"You've referred {threshold} people! Bonus: ₦{bonus:,.0f} credited!", "info")
            referrer.referral_tier_claimed = threshold
            db.session.commit()


# ---------------------------------------------------------------------------
# Real IP Helper
# ---------------------------------------------------------------------------
def get_real_ip():
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
        # Admin session timeout check
        last_active = session.get("admin_last_active")
        if last_active:
            from datetime import datetime
            elapsed = (datetime.utcnow() - datetime.fromisoformat(last_active)).total_seconds()
            timeout = current_app.config.get("ADMIN_SESSION_TIMEOUT", 1200)  # 20 min
            if elapsed > timeout:
                session.pop("admin_last_active", None)
                session.pop("admin_2fa_verified", None)
                flash("Admin session expired. Please login again.", "error")
                return redirect(url_for("auth.logout"))
        session["admin_last_active"] = datetime.utcnow().isoformat()
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
        session["admin_last_active"] = datetime.utcnow().isoformat()
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Daily Bet Limit Check
# ---------------------------------------------------------------------------
def check_daily_bet_limit(user, bet_amount):
    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
        db.session.commit()
    max_daily = get_setting("MAX_DAILY_BET", 50000)
    return (user.daily_bet_total + bet_amount) <= max_daily
