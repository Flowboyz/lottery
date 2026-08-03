"""
Admin blueprint - dashboard, user management, game settings, announcements, exports.
"""
import csv
import io
from datetime import datetime, timedelta, date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response, current_app)
from flask_login import login_required, current_user
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import (User, GamePlay, Transaction, Bet, WithdrawalRequest, PaymentRecord,
                        AuditLog, Notification, GameSettings, Announcement, BankAccount)
from app.models_games import AviatorEntry
from app.utils import (admin_required, superadmin_required, credit_wallet, debit_wallet,
                        notify_user, log_audit, format_money, send_email, admin_alert,
                        get_setting)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Helper: verify admin password for critical actions
# ---------------------------------------------------------------------------
def verify_admin_password():
    """Check if admin_password was submitted and matches current_user."""
    pw = request.form.get("admin_password", "")
    if not pw:
        return False
    return current_user.check_password(pw)


# ────────────────────────── REVENUE DASHBOARD ──────────────────────────
@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    def sum_txn(action, since=None):
        q = db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.action == action, Transaction.status == "completed")
        if since:
            q = q.filter(Transaction.created_at >= datetime.combine(since, datetime.min.time()))
        return abs(q.scalar() or 0)

    def count_since(model, since):
        return model.query.filter(model.created_at >= datetime.combine(since, datetime.min.time())).count()

    since_time = datetime.combine(today, datetime.min.time())
    bet_users = {u.user_id for u in Bet.query.filter(Bet.created_at >= since_time).all()}
    gp_users = {u.user_id for u in GamePlay.query.filter(GamePlay.created_at >= since_time).all()}
    aviator_users = {u.user_id for u in AviatorEntry.query.filter(AviatorEntry.created_at >= since_time).all()}
    active_users_today = len(bet_users.union(gp_users).union(aviator_users))

    stats = dict(
        total_users=User.query.count(),
        new_users_today=count_since(User, today),
        new_users_week=count_since(User, week_ago),

        total_deposits=sum_txn("DEPOSIT"),
        deposits_today=sum_txn("DEPOSIT", today),
        deposits_week=sum_txn("DEPOSIT", week_ago),
        deposits_month=sum_txn("DEPOSIT", month_ago),

        total_withdrawals=sum_txn("WITHDRAWAL"),
        withdrawals_today=sum_txn("WITHDRAWAL", today),

        total_bets=Bet.query.count(),
        bets_today=count_since(Bet, today),
        total_wagered=(db.session.query(db.func.sum(Bet.bet_amount)).scalar() or 0) +
                      (db.session.query(db.func.sum(GamePlay.bet_amount)).scalar() or 0),
        wagered_today=(db.session.query(db.func.sum(Bet.bet_amount)).filter(
            Bet.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0) +
                      (db.session.query(db.func.sum(GamePlay.bet_amount)).filter(
            GamePlay.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0),

        total_payouts=(db.session.query(db.func.sum(Bet.payout)).filter(
            Bet.result == "WIN").scalar() or 0) +
                      (db.session.query(db.func.sum(GamePlay.payout)).filter(
            GamePlay.result == "WIN").scalar() or 0),
        payouts_today=(db.session.query(db.func.sum(Bet.payout)).filter(
            Bet.result == "WIN",
            Bet.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0) +
                      (db.session.query(db.func.sum(GamePlay.payout)).filter(
            GamePlay.result == "WIN",
            GamePlay.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0),

        total_game_plays=GamePlay.query.count(),
        game_plays_today=count_since(GamePlay, today),

        pending_withdrawals=WithdrawalRequest.query.filter_by(status="pending").count(),
        active_users_today=active_users_today,
    )

    # Net profit = deposits - withdrawals - payouts + bets lost
    stats["net_profit_today"] = stats["deposits_today"] - stats["withdrawals_today"] - stats["payouts_today"]
    stats["net_profit_total"] = stats["total_deposits"] - stats["total_withdrawals"] - stats["total_payouts"]

    # Scan for backups
    import os
    backups_dir = os.path.join(current_app.root_path, "..", "backups")
    os.makedirs(backups_dir, exist_ok=True)
    backups = []
    try:
        for f in os.listdir(backups_dir):
            if f.endswith(".json") and f.startswith("backup_"):
                parts = f[:-5].split("_")
                if len(parts) >= 3:
                    reset_type = parts[1]
                    raw_time = "_".join(parts[2:])
                    try:
                        dt = datetime.strptime(raw_time, "%Y%m%d_%H%M%S")
                        time_str = dt.strftime("%d %b %Y, %I:%M %p")
                    except Exception:
                        time_str = raw_time
                else:
                    reset_type = "unknown"
                    time_str = f
                
                file_size = os.path.getsize(os.path.join(backups_dir, f))
                backups.append({
                    "filename": f,
                    "reset_type": reset_type.capitalize(),
                    "timestamp": time_str,
                    "size": f"{file_size / 1024:.1f} KB"
                })
    except Exception:
        pass
    backups.sort(key=lambda x: x["filename"], reverse=True)

    return render_template("admin/dashboard.html", stats=stats, backups=backups)


# ────────────────────────── USERS ──────────────────────────
@admin_bp.route("/users")
@login_required
@admin_required
def users():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "")
    status = request.args.get("status", "all")
    query = User.query
    if search:
        query = query.filter(
            db.or_(User.username.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
        )
    if status == "suspended":
        query = query.filter_by(is_suspended=True)
    elif status == "banned":
        query = query.filter_by(is_banned=True)
    elif status == "active":
        query = query.filter_by(is_suspended=False, is_banned=False)

    users_page = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=25, error_out=False
    )
    return render_template("admin/users.html", users=users_page, q=search, status_filter=status)


# ────────────────────────── USER DETAIL ──────────────────────────
@admin_bp.route("/users/<int:user_id>")
@login_required
@admin_required
def user_detail(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))

    # Lottery bets
    lottery_bets = Bet.query.filter_by(user_id=user_id).order_by(Bet.created_at.desc()).limit(15).all()

    # Other games (Wheel, Coinflip, Scratchcard, Color, etc.)
    other_games = GamePlay.query.filter_by(user_id=user_id).order_by(GamePlay.created_at.desc()).limit(15).all()

    # Aviator
    aviator_bets = AviatorEntry.query.filter_by(user_id=user_id).order_by(AviatorEntry.created_at.desc()).limit(15).all()

    # Combine and sort by date
    all_activity = []
    for b in lottery_bets:
        all_activity.append({
            'type': 'lottery',
            'created_at': b.created_at,
            'bet_amount': b.bet_amount,
            'payout': b.payout,
            'result': b.result,
            'game_name': 'Lottery'
        })
    for g in other_games:
        friendly_names = {
            "wheel": "Spin the Wheel",
            "coinflip": "Coin Flip",
            "scratchcard": "Scratch Card",
            "color": "Color Prediction",
            "lotto590": "Lotto 5/90",
            "football": "Football Predictor",
            "ludo": "Ludo Quick-Bet"
        }
        all_activity.append({
            'type': g.game_type,
            'created_at': g.created_at,
            'bet_amount': g.bet_amount,
            'payout': g.payout,
            'result': g.result,
            'game_name': friendly_names.get(g.game_type, g.game_type.capitalize())
        })
    for a in aviator_bets:
        all_activity.append({
            'type': 'aviator',
            'created_at': a.created_at,
            'bet_amount': a.bet_amount,
            'payout': a.payout or 0,
            'result': a.result,
            'game_name': 'Aviator',
            'cashout_at': a.cashout_at
        })

    # Sort by date descending
    all_activity.sort(key=lambda x: x['created_at'], reverse=True)
    recent_activity = all_activity[:20]

    recent_txns = Transaction.query.filter_by(user_id=user_id).order_by(
        Transaction.created_at.desc()).limit(20).all()
    withdrawals = WithdrawalRequest.query.filter_by(user_id=user_id).order_by(
        WithdrawalRequest.created_at.desc()).limit(10).all()
    banks = BankAccount.query.filter_by(user_id=user_id).all()
    referrals = User.query.filter_by(referred_by=user_id).all()

    user_stats = dict(
        total_bets = (
            Bet.query.filter_by(user_id=user_id).count() +
            GamePlay.query.filter_by(user_id=user_id).count() +
            AviatorEntry.query.filter_by(user_id=user_id).count()
        ),

        total_wins = (
            Bet.query.filter_by(user_id=user_id, result="WIN").count() +
            GamePlay.query.filter_by(user_id=user_id, result="WIN").count() +
            AviatorEntry.query.filter_by(user_id=user_id, result="WIN").count()
        ),

        total_wagered = (
            (db.session.query(db.func.sum(Bet.bet_amount)).filter_by(user_id=user_id).scalar() or 0) +
            (db.session.query(db.func.sum(GamePlay.bet_amount)).filter_by(user_id=user_id).scalar() or 0) +
            (db.session.query(db.func.sum(AviatorEntry.bet_amount)).filter_by(user_id=user_id).scalar() or 0)
        ),

        total_won = (
            (db.session.query(db.func.sum(Bet.payout)).filter(Bet.user_id == user_id, Bet.result == "WIN").scalar() or 0) +
            (db.session.query(db.func.sum(GamePlay.payout)).filter(GamePlay.user_id == user_id, GamePlay.result == "WIN").scalar() or 0) +
            (db.session.query(db.func.sum(AviatorEntry.payout)).filter(AviatorEntry.user_id == user_id, AviatorEntry.result == "WIN").scalar() or 0)
        ),

        total_deposited = db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id,
            Transaction.action == "DEPOSIT",
            Transaction.status == "completed"
        ).scalar() or 0,
    )

    return render_template("admin/user_detail.html", user=user,
                           recent_activity=recent_activity, recent_txns=recent_txns,
                           withdrawals=withdrawals, banks=banks,
                           referrals=referrals, user_stats=user_stats)

# ────────────────────────── UPDATE BALANCE (superadmin) ──────────────────────────
@admin_bp.route("/update-balance/<int:user_id>", methods=["POST"])
@login_required
@superadmin_required
def update_balance(user_id):
    if not verify_admin_password():
        flash("Admin password required for balance adjustments.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    try:
        amount = float(request.form.get("amount", 0))
    except (ValueError, TypeError):
        flash("Invalid amount.", "error")
        return redirect(url_for("admin.users"))

    action = request.form.get("action", "credit")
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.users"))

    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    if action == "credit":
        credit_wallet(user, amount, "ADMIN_ADJUST",
                      description=f"Admin credited {format_money(amount)}", method="admin")
    else:
        txn = debit_wallet(user, amount, "ADMIN_ADJUST",
                           description=f"Admin debited {format_money(amount)}", method="admin")
        if not txn:
            flash("Insufficient balance for debit.", "error")
            return redirect(url_for("admin.user_detail", user_id=user_id))

    log_audit("ADMIN_BALANCE_UPDATE",
              f"{action.title()} {format_money(amount)} for {user.username}")
    admin_alert("Balance Adjustment", f"{action.title()} {format_money(amount)} for {user.username}")
    flash(f"Balance updated for {user.username}.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


# ────────────────────────── BULK BALANCE ──────────────────────────
@admin_bp.route("/bulk-balance", methods=["POST"])
@login_required
@superadmin_required
def bulk_balance():
    if not verify_admin_password():
        flash("Admin password required for bulk balance.", "error")
        return redirect(url_for("admin.users"))

    user_ids = request.form.getlist("user_ids", type=int)
    if not user_ids:
        flash("No users selected.", "error")
        return redirect(url_for("admin.users"))

    try:
        amount = float(request.form.get("amount", 0))
    except (ValueError, TypeError):
        flash("Invalid amount.", "error")
        return redirect(url_for("admin.users"))

    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("admin.users"))

    action = request.form.get("action", "credit")
    success_count = 0
    fail_count = 0

    for uid in user_ids:
        user = db.session.get(User, uid)
        if not user:
            fail_count += 1
            continue

        if action == "credit":
            credit_wallet(user, amount, "ADMIN_ADJUST",
                          description=f"Bulk credit {format_money(amount)}", method="admin")
            success_count += 1
        else:
            txn = debit_wallet(user, amount, "ADMIN_ADJUST",
                               description=f"Bulk debit {format_money(amount)}", method="admin")
            if txn:
                success_count += 1
            else:
                fail_count += 1

    log_audit("BULK_BALANCE", f"{action.title()} {format_money(amount)} to {success_count} users ({fail_count} failed)")
    admin_alert("Bulk Balance", f"{action.title()} {format_money(amount)} to {success_count} users")
    flash(f"{action.title()} {format_money(amount)} applied to {success_count} users. {fail_count} failed.", "success")
    return redirect(url_for("admin.users"))


# ────────────────────────── SUSPEND / BAN ──────────────────────────
@admin_bp.route("/users/<int:user_id>/suspend", methods=["POST"])
@login_required
@admin_required
def suspend_user(user_id):
    if not verify_admin_password():
        flash("Admin password required.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    user = db.session.get(User, user_id)
    if not user or user.role in ("admin", "superadmin"):
        flash("Cannot suspend this user.", "error")
        return redirect(url_for("admin.users"))

    days = int(request.form.get("days", 7))
    reason = request.form.get("reason", "").strip()

    user.is_suspended = True
    user.suspended_until = datetime.utcnow() + timedelta(days=days)
    db.session.commit()

    notify_user(user.id, "Account Suspended",
                f"Your account has been suspended for {days} days. Reason: {reason or 'Policy violation'}", "system")
    log_audit("USER_SUSPENDED", f"Suspended {user.username} for {days} days. Reason: {reason}")
    admin_alert("User Suspended", f"{user.username} suspended for {days} days by {current_user.username}")
    flash(f"{user.username} suspended for {days} days.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/unsuspend", methods=["POST"])
@login_required
@admin_required
def unsuspend_user(user_id):
    user = db.session.get(User, user_id)
    if user:
        user.is_suspended = False
        user.suspended_until = None
        db.session.commit()
        log_audit("USER_UNSUSPENDED", f"Unsuspended {user.username}")
        flash(f"{user.username} unsuspended.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/ban", methods=["POST"])
@login_required
@superadmin_required
def ban_user(user_id):
    if not verify_admin_password():
        flash("Admin password required.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    user = db.session.get(User, user_id)
    if not user or user.role in ("admin", "superadmin"):
        flash("Cannot ban this user.", "error")
        return redirect(url_for("admin.users"))

    reason = request.form.get("reason", "").strip()
    user.is_banned = True
    user.ban_reason = reason or "Policy violation"
    db.session.commit()

    log_audit("USER_BANNED", f"Banned {user.username}. Reason: {user.ban_reason}")
    admin_alert("User Banned", f"{user.username} banned by {current_user.username}. Reason: {user.ban_reason}")
    flash(f"{user.username} has been banned.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@admin_bp.route("/users/<int:user_id>/unban", methods=["POST"])
@login_required
@superadmin_required
def unban_user(user_id):
    user = db.session.get(User, user_id)
    if user:
        user.is_banned = False
        user.ban_reason = None
        db.session.commit()
        log_audit("USER_UNBANNED", f"Unbanned {user.username}")
        flash(f"{user.username} unbanned.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


# ────────────────────────── WITHDRAWAL MANAGEMENT ──────────────────────────
@admin_bp.route("/withdrawals")
@login_required
@admin_required
def withdrawals():
    status_filter = request.args.get("status", "pending")
    page = request.args.get("page", 1, type=int)
    query = WithdrawalRequest.query
    if status_filter != "all":
        query = query.filter_by(status=status_filter)
    requests_page = query.order_by(
        WithdrawalRequest.created_at.desc()
    ).paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/withdrawals.html",
                           withdrawals=requests_page, status_filter=status_filter)


@admin_bp.route("/withdrawals/<int:wr_id>/process", methods=["POST"])
@login_required
@admin_required
def process_withdrawal(wr_id):
    if not verify_admin_password():
        flash("Admin password required to process withdrawals.", "error")
        return redirect(url_for("admin.withdrawals"))

    wr = db.session.get(WithdrawalRequest, wr_id)
    if not wr or wr.status != "pending":
        flash("Request not found or already processed.", "error")
        return redirect(url_for("admin.withdrawals"))

    action = request.form.get("action", "")

    if action == "approve":
        wr.status = "approved"
        wr.reviewed_by = current_user.id
        db.session.commit()
        notify_user(wr.user_id, "Withdrawal Approved",
                    f"Your withdrawal of {format_money(wr.amount)} has been approved!", "withdrawal")
        send_email(wr.user.email, "Withdrawal Approved - Ditto Dinky",
                   f"Hi {wr.user.username},\n\nYour withdrawal of {format_money(wr.amount)} has been approved.\n\n- Ditto Dinky Team")
        log_audit("WITHDRAWAL_APPROVED", f"Withdrawal #{wr.id} approved for {format_money(wr.amount)}")
        admin_alert("Withdrawal Approved", f"#{wr.id} — {format_money(wr.amount)} for {wr.user.username}")
        flash(f"Withdrawal #{wr.id} approved.", "success")

    elif action == "reject":
        user = db.session.get(User, wr.user_id)
        credit_wallet(user, wr.amount, "WITHDRAWAL_REFUND",
                      description=f"Withdrawal #{wr.id} rejected - refund", method="admin")
        wr.status = "rejected"
        wr.admin_note = request.form.get("reason", "No reason provided")
        wr.reviewed_by = current_user.id
        db.session.commit()
        notify_user(wr.user_id, "Withdrawal Rejected",
                    f"Your withdrawal of {format_money(wr.amount)} was rejected. Funds have been refunded.", "withdrawal")
        log_audit("WITHDRAWAL_REJECTED", f"Withdrawal #{wr.id} rejected")
        flash(f"Withdrawal #{wr.id} rejected and funds refunded.", "info")
    else:
        flash("Invalid action.", "error")

    return redirect(url_for("admin.withdrawals"))


# ────────────────────────── BETTING HISTORY ──────────────────────────
@admin_bp.route("/bets")
@login_required
@admin_required
def betting_history():
    page = request.args.get("page", 1, type=int)
    user_filter = request.args.get("user", "", type=str).strip()
    result_filter = request.args.get("result", "all", type=str)

    query = Bet.query.join(User)
    if user_filter:
        query = query.filter(User.username.ilike(f"%{user_filter}%"))
    if result_filter != "all":
        query = query.filter(Bet.result == result_filter.upper())

    bets_page = query.order_by(Bet.created_at.desc()).paginate(page=page, per_page=30, error_out=False)

    bet_users = db.session.query(User.username).join(Bet).distinct().order_by(User.username).all()
    bet_usernames = [u[0] for u in bet_users]

    return render_template("admin/bets.html", bets=bets_page,
                           user_filter=user_filter, result_filter=result_filter,
                           bet_usernames=bet_usernames)


# ────────────────────────── AUDIT LOGS ──────────────────────────
@admin_bp.route("/audit-logs")
@login_required
@superadmin_required
def audit_logs():
    page = request.args.get("page", 1, type=int)
    user_filter = request.args.get("user", "", type=str).strip()
    action_filter = request.args.get("action", "", type=str).strip()

    query = AuditLog.query.outerjoin(User, AuditLog.user_id == User.id)
    if user_filter:
        query = query.filter(User.username.ilike(f"%{user_filter}%"))
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    logs = query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=30, error_out=False)

    all_actions = db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    action_list = [a[0] for a in all_actions]
    log_users = db.session.query(User.username).join(
        AuditLog, User.id == AuditLog.user_id).distinct().order_by(User.username).all()
    user_list = [u[0] for u in log_users]

    return render_template("admin/audit_logs.html", logs=logs,
                           user_filter=user_filter, action_filter=action_filter,
                           action_list=action_list, user_list=user_list)


# ────────────────────────── GAME SETTINGS ──────────────────────────
@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@superadmin_required
def game_settings():
    if request.method == "POST":
        if not verify_admin_password():
            flash("Admin password required to change settings.", "error")
            return redirect(url_for("admin.game_settings"))

        settings_map = {
            "WIN_PROBABILITY": ("Win Probability (0-1)", request.form.get("win_probability")),
            "PAYOUT_MULTIPLIER": ("Payout Multiplier", request.form.get("payout_multiplier")),
            "MAX_DAILY_BET": ("Max Daily Bet (₦)", request.form.get("max_daily_bet")),
            "DAILY_CLAIM_AMOUNT": ("Daily Claim Amount (₦)", request.form.get("daily_claim_amount")),
            "COOLDOWN_SECONDS": ("Cooldown Seconds", request.form.get("cooldown_seconds")),
            "SIGNUP_BONUS": ("Signup Bonus (₦)", request.form.get("signup_bonus")),
            "REFERRAL_BONUS": ("Referral Bonus (₦)", request.form.get("referral_bonus")),
            "MIN_DEPOSIT": ("Min Deposit (₦)", request.form.get("min_deposit")),
            "MIN_WITHDRAWAL": ("Min Withdrawal (₦)", request.form.get("min_withdrawal")),
            "COINFLIP_PAYOUT": ("Coinflip Payout Multiplier", request.form.get("coinflip_payout")),
            "SCRATCH_WIN_CHANCE": ("Scratch Card Win Chance (0-1)", request.form.get("scratch_win_chance")),
            # Aviator
            "AVIATOR_ENABLED":    ("Aviator Enabled (1=yes, 0=no)", request.form.get("aviator_enabled")),
            "AVIATOR_RTP":        ("Aviator RTP %",                 request.form.get("aviator_rtp")),
            "AVIATOR_HOUSE_EDGE": ("Aviator House Edge %",          request.form.get("aviator_house_edge")),
            "AVIATOR_MIN_BET":    ("Aviator Min Bet (₦)",        request.form.get("aviator_min_bet")),
            "AVIATOR_MAX_BET":    ("Aviator Max Bet (₦)",        request.form.get("aviator_max_bet")),
            # Color Prediction
            "COLOR_ENABLED":        ("Color Prediction Enabled",        request.form.get("color_enabled")),
            "COLOR_RED_PAYOUT":     ("Color Red Payout Multiplier",     request.form.get("color_red_payout")),
            "COLOR_GREEN_PAYOUT":   ("Color Green Payout Multiplier",   request.form.get("color_green_payout")),
            "COLOR_VIOLET_PAYOUT":  ("Color Violet Payout Multiplier",  request.form.get("color_violet_payout")),
            "COLOR_ROUND_DURATION": ("Color Round Duration (seconds)",  request.form.get("color_round_duration")),
            # Lotto 5/90
            "LOTTO590_ENABLED":     ("Lotto 5/90 Enabled",              request.form.get("lotto590_enabled")),
            "LOTTO590_MIN_BET":     ("Lotto 5/90 Min Bet (₦)",          request.form.get("lotto590_min_bet")),
            "LOTTO590_MAX_BET":     ("Lotto 5/90 Max Bet (₦)",          request.form.get("lotto590_max_bet")),
            "LOTTO590_NAP2_PAYOUT":  ("Lotto 5/90 Nap 2 Payout Multiplier", request.form.get("lotto590_nap2_payout")),
            "LOTTO590_NAP3_PAYOUT":  ("Lotto 5/90 Nap 3 Payout Multiplier", request.form.get("lotto590_nap3_payout")),
            # Football Predictor
            "FOOTBALL_ENABLED":     ("Football Predictor Enabled",      request.form.get("football_enabled")),
            "FOOTBALL_MIN_BET":     ("Football Predictor Min Bet (₦)",  request.form.get("football_min_bet")),
            "FOOTBALL_MAX_BET":     ("Football Predictor Max Bet (₦)",  request.form.get("football_max_bet")),
            "FOOTBALL_ODDS":        ("Football Predictor Odds Multiplier", request.form.get("football_odds")),
            # Ludo Quick-Bet
            "LUDO_ENABLED":           ("Ludo Quick-Bet Enabled",          request.form.get("ludo_enabled")),
            "LUDO_MIN_BET":           ("Ludo Quick-Bet Min Bet (₦)",      request.form.get("ludo_min_bet")),
            "LUDO_MAX_BET":           ("Ludo Quick-Bet Max Bet (₦)",      request.form.get("ludo_max_bet")),
            "LUDO_PAYOUT_UNDER_OVER": ("Ludo Under/Over Payout Multiplier", request.form.get("ludo_payout_under_over")),
            "LUDO_PAYOUT_SEVEN":      ("Ludo Lucky 7 Payout Multiplier",  request.form.get("ludo_payout_seven")),
            "MAINTENANCE_MODE": ("Maintenance Mode", "on" if request.form.get("maintenance_mode") == "on" else "off"),
        }

        for key, (label, value) in settings_map.items():
            if value is not None and value.strip():
                setting = GameSettings.query.filter_by(key=key).first()
                if setting:
                    setting.value = value.strip()
                    setting.updated_by = current_user.id
                else:
                    setting = GameSettings(key=key, value=value.strip(),
                                           label=label, updated_by=current_user.id)
                    db.session.add(setting)

        db.session.commit()
        log_audit("SETTINGS_UPDATED", "Game settings modified")
        admin_alert("Settings Changed", f"Game settings updated by {current_user.username}")
        flash("Game settings updated.", "success")
        return redirect(url_for("admin.game_settings"))

    # Load current settings
    settings = {}
    defaults = {
        "WIN_PROBABILITY": 0.10, "PAYOUT_MULTIPLIER": 5, "MAX_DAILY_BET": 50000,
        "DAILY_CLAIM_AMOUNT": 500, "COOLDOWN_SECONDS": 10, "SIGNUP_BONUS": 100,
        "REFERRAL_BONUS": 200, "MIN_DEPOSIT": 500, "MIN_WITHDRAWAL": 1000,
        "COINFLIP_PAYOUT": 1.9, "SCRATCH_WIN_CHANCE": 0.30,
        "AVIATOR_ENABLED": 1, "AVIATOR_RTP": 96, "AVIATOR_HOUSE_EDGE": 4,
        "AVIATOR_MIN_BET": 50, "AVIATOR_MAX_BET": 50000,
        "COLOR_ENABLED": 1, "COLOR_RED_PAYOUT": 2.0, "COLOR_GREEN_PAYOUT": 2.0,
        "COLOR_VIOLET_PAYOUT": 4.5, "COLOR_ROUND_DURATION": 30,
        "LOTTO590_ENABLED": 1, "LOTTO590_MIN_BET": 50, "LOTTO590_MAX_BET": 50000,
        "LOTTO590_NAP2_PAYOUT": 240, "LOTTO590_NAP3_PAYOUT": 2100,
        "FOOTBALL_ENABLED": 1, "FOOTBALL_MIN_BET": 50, "FOOTBALL_MAX_BET": 50000, "FOOTBALL_ODDS": 1.8,
        "LUDO_ENABLED": 1, "LUDO_MIN_BET": 50, "LUDO_MAX_BET": 50000,
        "LUDO_PAYOUT_UNDER_OVER": 1.9, "LUDO_PAYOUT_SEVEN": 5.5,
    }
    for key, default in defaults.items():
        settings[key] = get_setting(key, default)

    return render_template("admin/settings.html", settings=settings)


# ────────────────────────── ANNOUNCEMENTS ──────────────────────────
@admin_bp.route("/announcements", methods=["GET", "POST"])
@login_required
@admin_required
def announcements():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        message = request.form.get("message", "").strip()
        expires_days = int(request.form.get("expires_days", 7))

        if not title or not message:
            flash("Title and message are required.", "error")
        else:
            ann = Announcement(
                title=title, message=message,
                created_by=current_user.id,
                expires_at=datetime.utcnow() + timedelta(days=expires_days),
            )
            db.session.add(ann)
            db.session.commit()
            log_audit("ANNOUNCEMENT_CREATED", f"Announcement: {title}")
            flash("Announcement published.", "success")

        return redirect(url_for("admin.announcements"))

    all_announcements = Announcement.query.order_by(Announcement.created_at.desc()).limit(20).all()
    return render_template("admin/announcements.html", announcements=all_announcements)


@admin_bp.route("/announcements/<int:ann_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_announcement(ann_id):
    ann = db.session.get(Announcement, ann_id)
    if ann:
        ann.is_active = not ann.is_active
        db.session.commit()
        status = "activated" if ann.is_active else "deactivated"
        flash(f"Announcement {status}.", "success")
    return redirect(url_for("admin.announcements"))


@admin_bp.route("/announcements/<int:ann_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_announcement(ann_id):
    ann = db.session.get(Announcement, ann_id)
    if ann:
        db.session.delete(ann)
        db.session.commit()
        flash("Announcement deleted.", "success")
    return redirect(url_for("admin.announcements"))


# ────────────────────────── EMAIL USERS ──────────────────────────
@admin_bp.route("/email-users", methods=["GET", "POST"])
@login_required
@admin_required
def email_users():
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        selected_ids = request.form.getlist("send_to", type=int)

        if not subject or not body:
            flash("Subject and message are required.", "error")
            return redirect(url_for("admin.email_users"))

        if not selected_ids:
            flash("No recipients selected.", "error")
            return redirect(url_for("admin.email_users"))

        import time
        users_to_email = User.query.filter(User.id.in_(selected_ids)).all()
        sent_count = 0
        fail_count = 0
        for user in users_to_email:
            if user.email:
                try:
                    send_email(user.email, subject,
                               f"Hi {user.username},\n\n{body}\n\n- Ditto Dinky Team")
                    sent_count += 1
                    time.sleep(1)
                except Exception:
                    fail_count += 1

        log_audit("BULK_EMAIL", f"Sent email to {sent_count} users ({fail_count} failed). Subject: {subject}")
        flash(f"Email sent to {sent_count} users. {fail_count} failed.", "success")
        return redirect(url_for("admin.email_users"))

    all_email_users = User.query.filter(
        User.email.isnot(None), User.email != ""
    ).order_by(User.username).all()
    return render_template("admin/email_users.html", email_users=all_email_users)



# ────────────────────────── NEW GAMES REPORTING ──────────────────────────
@admin_bp.route("/games-report")
@login_required
@admin_required
def games_report():
    """Revenue report for Aviator and Color Prediction."""
    from app.models_games import ColorRound
    from sqlalchemy import func

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    def gp_stats(game_type, since=None):
        q = GamePlay.query.filter(GamePlay.game_type == game_type)
        if since:
            q = q.filter(GamePlay.created_at >= datetime.combine(since, datetime.min.time()))
        rows = q.all()
        wagers   = sum(r.bet_amount for r in rows)
        payouts  = sum(r.payout    for r in rows)
        plays    = len(rows)
        players  = len(set(r.user_id for r in rows))
        rtp      = (payouts / wagers * 100) if wagers else 0
        profit   = wagers - payouts
        return dict(wagers=wagers, payouts=payouts, plays=plays, players=players,
                    rtp=rtp, profit=profit)

    periods = {
        "today":   today,
        "weekly":  week_ago,
        "monthly": month_ago,
        "alltime": None,
    }

    avi_stats   = {p: gp_stats("aviator", since) for p, since in periods.items()}
    color_stats = {p: gp_stats("color",   since) for p, since in periods.items()}
    lotto590_stats = {p: gp_stats("lotto590", since) for p, since in periods.items()}
    football_stats = {p: gp_stats("football", since) for p, since in periods.items()}
    ludo_stats     = {p: gp_stats("ludo",     since) for p, since in periods.items()}

    # Color round counts
    color_rounds_today = ColorRound.query.filter(
        ColorRound.started_at >= datetime.combine(today, datetime.min.time())
    ).count()

    color_rounds_total = ColorRound.query.count()

    return render_template("admin/games_report.html",
                           avi_stats=avi_stats,
                           color_stats=color_stats,
                           lotto590_stats=lotto590_stats,
                           football_stats=football_stats,
                           ludo_stats=ludo_stats,
                           color_rounds_today=color_rounds_today,
                           color_rounds_total=color_rounds_total)

# ────────────────────────── EXPORT CSV ──────────────────────────
@admin_bp.route("/export/<string:data_type>")
@login_required
@admin_required
def export_csv(data_type):
    output = io.StringIO()
    writer = csv.writer(output)

    if data_type == "users":
        writer.writerow(["ID", "Username", "Email", "Phone", "Balance", "Role", "Status", "Joined"])
        for u in User.query.order_by(User.created_at.desc()).all():
            status = "banned" if u.is_banned else ("suspended" if u.is_suspended else "active")
            writer.writerow([u.id, u.username, u.email or "", u.phone or "",
                             f"{u.balance:.2f}", u.role, status,
                             u.created_at.strftime("%Y-%m-%d %H:%M")])

    elif data_type == "transactions":
        writer.writerow(["ID", "User", "Action", "Amount", "Before", "After", "Reference", "Date"])
        for t in Transaction.query.order_by(Transaction.created_at.desc()).limit(5000).all():
            writer.writerow([t.id, t.user.username, t.action, f"{t.amount:.2f}",
                             f"{t.balance_before:.2f}", f"{t.balance_after:.2f}",
                             t.reference or "", t.created_at.strftime("%Y-%m-%d %H:%M")])

    elif data_type == "bets":
        writer.writerow(["ID", "User", "Num1", "Num2", "Num3", "Total", "Lucky", "Bet", "Payout", "Result", "Date"])
        for b in Bet.query.order_by(Bet.created_at.desc()).limit(5000).all():
            writer.writerow([b.id, b.user.username, b.num1, b.num2, b.num3,
                             b.picked_total, b.lucky_number, f"{b.bet_amount:.2f}",
                             f"{b.payout:.2f}", b.result,
                             b.created_at.strftime("%Y-%m-%d %H:%M")])
    elif data_type == "games":
        writer.writerow(["ID", "User", "Game", "Bet", "Payout", "Result", "Date"])
        for g in GamePlay.query.order_by(GamePlay.created_at.desc()).limit(5000).all():
            writer.writerow([g.id, g.user.username, g.game_type,
                             f"{g.bet_amount:.2f}", f"{g.payout:.2f}", g.result,
                             g.created_at.strftime("%Y-%m-%d %H:%M")])        
    else:
        flash("Invalid export type.", "error")
        return redirect(url_for("admin.dashboard"))

    log_audit("DATA_EXPORT", f"Exported {data_type} as CSV")

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ditto_dinky_{data_type}_{date.today()}.csv"}
    )


# ────────────────────────── GAME HISTORY ──────────────────────────
@admin_bp.route("/game-history")
@login_required
@admin_required
def game_history():
    page = request.args.get("page", 1, type=int)
    game_filter = request.args.get("game", "all", type=str)
    user_filter = request.args.get("user", "", type=str).strip()

    query = GamePlay.query.join(User)
    if game_filter != "all":
        query = query.filter(GamePlay.game_type == game_filter)
    if user_filter:
        query = query.filter(User.username.ilike(f"%{user_filter}%"))

    plays = query.order_by(GamePlay.created_at.desc()).paginate(
        page=page, per_page=30, error_out=False)

    game_users = db.session.query(User.username).join(GamePlay).distinct().order_by(User.username).all()
    user_list = [u[0] for u in game_users]

    return render_template("admin/game_history.html", plays=plays,
                           game_filter=game_filter, user_filter=user_filter,
                           user_list=user_list)


# ────────────────────────── RESET PLATFORM DATA ──────────────────────────
def serialize_records(query_all):
    serialized = []
    from datetime import datetime, date
    for item in query_all:
        data = {}
        for col in item.__table__.columns:
            val = getattr(item, col.name)
            if isinstance(val, (datetime, date)):
                val = val.isoformat()
            data[col.name] = val
        serialized.append(data)
    return serialized


def deserialize_records(model_class, data_list):
    from datetime import datetime, date
    for item_data in data_list:
        pk_name = model_class.__table__.primary_key.columns.keys()[0]
        pk_val = item_data.get(pk_name)
        if pk_val:
            existing = db.session.get(model_class, pk_val)
            if existing:
                continue

        parsed_data = {}
        for col in model_class.__table__.columns:
            val = item_data.get(col.name)
            if val is not None and col.type.python_type in (datetime, date):
                try:
                    val = datetime.fromisoformat(val)
                except Exception:
                    pass
            parsed_data[col.name] = val

        instance = model_class(**parsed_data)
        db.session.add(instance)


@admin_bp.route("/reset-demo-data", methods=["POST"])
@login_required
@superadmin_required
def reset_demo_data():
    if not verify_admin_password():
        flash("Invalid admin password. Action aborted.", "error")
        return redirect(url_for("admin.dashboard"))

    reset_action = request.form.get("reset_action", "").strip()
    if reset_action not in ("balances", "games", "financial", "all"):
        flash("Invalid reset action selected.", "error")
        return redirect(url_for("admin.dashboard"))

    import json
    import os
    from datetime import datetime

    backups_dir = os.path.join(current_app.root_path, "..", "backups")
    os.makedirs(backups_dir, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{reset_action}_{timestamp}.json"
    backup_filepath = os.path.join(backups_dir, backup_filename)

    backup_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "reset_type": reset_action,
        "balances": [],
        "transactions": [],
        "gameplays": [],
        "bets": [],
        "aviator_entries": [],
        "withdrawal_requests": [],
        "payment_records": [],
        "notifications": [],
        "audit_logs": []
    }

    try:
        # 1. Back up data based on selection
        if reset_action in ("balances", "all"):
            backup_data["balances"] = [{"id": u.id, "balance": u.balance} for u in User.query.all()]

        if reset_action in ("games", "all"):
            backup_data["bets"] = serialize_records(Bet.query.all())
            backup_data["gameplays"] = serialize_records(GamePlay.query.all())
            backup_data["aviator_entries"] = serialize_records(AviatorEntry.query.all())

        if reset_action in ("financial", "all"):
            backup_data["transactions"] = serialize_records(Transaction.query.all())
            backup_data["withdrawal_requests"] = serialize_records(WithdrawalRequest.query.all())
            backup_data["payment_records"] = serialize_records(PaymentRecord.query.all())

        if reset_action == "all":
            backup_data["notifications"] = serialize_records(Notification.query.all())
            backup_data["audit_logs"] = serialize_records(AuditLog.query.all())

        # Save backup file
        with open(backup_filepath, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)

        # 2. Execute deletion/reset based on selection
        if reset_action in ("balances", "all"):
            db.session.query(User).update({User.balance: 0.0})

        if reset_action in ("games", "all"):
            Bet.query.delete()
            GamePlay.query.delete()
            AviatorEntry.query.delete()

        if reset_action in ("financial", "all"):
            Transaction.query.delete()
            WithdrawalRequest.query.delete()
            PaymentRecord.query.delete()

        if reset_action == "all":
            Notification.query.delete()
            AuditLog.query.delete()

        db.session.commit()

        # Log the reset action
        log_audit("DEMO_RESET", f"Superadmin triggered platform reset action: {reset_action} (Backup: {backup_filename})", current_user.id)
        flash(f"Platform reset action '{reset_action}' executed successfully. A safety backup has been cached.", "success")
    except Exception as e:
        db.session.rollback()
        if os.path.exists(backup_filepath):
            try:
                os.remove(backup_filepath)
            except Exception:
                pass
        current_app.logger.error(f"Platform reset failed: {e}")
        flash(f"Error resetting platform data: {e}", "error")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/restore-backup/<string:filename>", methods=["POST"])
@login_required
@superadmin_required
def restore_backup(filename):
    if not verify_admin_password():
        flash("Invalid admin password. Action aborted.", "error")
        return redirect(url_for("admin.dashboard"))

    import json
    import os

    backups_dir = os.path.join(current_app.root_path, "..", "backups")
    backup_filepath = os.path.join(backups_dir, filename)

    if not os.path.exists(backup_filepath) or ".." in filename or "/" in filename or "\\" in filename:
        flash("Backup file not found or invalid filename.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        with open(backup_filepath, "r", encoding="utf-8") as f:
            backup_data = json.load(f)

        # 1. Restore balances
        if "balances" in backup_data and backup_data["balances"]:
            for u_data in backup_data["balances"]:
                user = db.session.get(User, u_data["id"])
                if user:
                    user.balance = u_data["balance"]

        # 2. Restore game records
        if "bets" in backup_data:
            deserialize_records(Bet, backup_data["bets"])
        if "gameplays" in backup_data:
            deserialize_records(GamePlay, backup_data["gameplays"])
        if "aviator_entries" in backup_data:
            deserialize_records(AviatorEntry, backup_data["aviator_entries"])

        # 3. Restore financial records
        if "transactions" in backup_data:
            deserialize_records(Transaction, backup_data["transactions"])
        if "withdrawal_requests" in backup_data:
            deserialize_records(WithdrawalRequest, backup_data["withdrawal_requests"])
        if "payment_records" in backup_data:
            deserialize_records(PaymentRecord, backup_data["payment_records"])

        # 4. Restore notification and audit logs
        if "notifications" in backup_data:
            deserialize_records(Notification, backup_data["notifications"])
        if "audit_logs" in backup_data:
            deserialize_records(AuditLog, backup_data["audit_logs"])

        db.session.commit()
        log_audit("DEMO_RESTORE", f"Superadmin restored platform data from backup: {filename}", current_user.id)
        flash(f"Platform data successfully restored from backup '{filename}'!", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Backup restore failed: {e}")
        flash(f"Failed to restore platform data: {e}", "error")

    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/delete-backup/<string:filename>", methods=["POST"])
@login_required
@superadmin_required
def delete_backup(filename):
    if not verify_admin_password():
        flash("Invalid admin password. Action aborted.", "error")
        return redirect(url_for("admin.dashboard"))

    import os
    backups_dir = os.path.join(current_app.root_path, "..", "backups")
    backup_filepath = os.path.join(backups_dir, filename)

    if not os.path.exists(backup_filepath) or ".." in filename or "/" in filename or "\\" in filename:
        flash("Backup file not found or invalid filename.", "error")
        return redirect(url_for("admin.dashboard"))

    try:
        os.remove(backup_filepath)
        log_audit("DEMO_BACKUP_DELETE", f"Superadmin permanently deleted backup cache: {filename}", current_user.id)
        flash(f"Backup '{filename}' has been permanently deleted from storage.", "success")
    except Exception as e:
        flash(f"Failed to delete backup: {e}", "error")

    return redirect(url_for("admin.dashboard"))
