"""
Admin blueprint - dashboard, user management, game settings, announcements, exports.
"""
import csv
import io
import time
from datetime import datetime, timedelta, date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response, current_app)
from flask_login import login_required, current_user
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import (User, Transaction, Bet, WithdrawalRequest, PaymentRecord,
                        AuditLog, Notification, GameSettings, Announcement, BankAccount)
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
        total_wagered=db.session.query(db.func.sum(Bet.bet_amount)).scalar() or 0,
        wagered_today=db.session.query(db.func.sum(Bet.bet_amount)).filter(
            Bet.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0,

        total_payouts=db.session.query(db.func.sum(Bet.payout)).filter(
            Bet.result == "WIN").scalar() or 0,
        payouts_today=db.session.query(db.func.sum(Bet.payout)).filter(
            Bet.result == "WIN",
            Bet.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0,

        pending_withdrawals=WithdrawalRequest.query.filter_by(status="pending").count(),
        active_users_today=db.session.query(db.func.count(db.distinct(Bet.user_id))).filter(
            Bet.created_at >= datetime.combine(today, datetime.min.time())).scalar() or 0,
    )

    # Net profit = deposits - withdrawals - payouts + bets lost
    stats["net_profit_today"] = stats["deposits_today"] - stats["withdrawals_today"] - stats["payouts_today"]
    stats["net_profit_total"] = stats["total_deposits"] - stats["total_withdrawals"] - stats["total_payouts"]

    return render_template("admin/dashboard.html", stats=stats)


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

    recent_bets = Bet.query.filter_by(user_id=user_id).order_by(
        Bet.created_at.desc()).limit(20).all()
    recent_txns = Transaction.query.filter_by(user_id=user_id).order_by(
        Transaction.created_at.desc()).limit(20).all()
    withdrawals = WithdrawalRequest.query.filter_by(user_id=user_id).order_by(
        WithdrawalRequest.created_at.desc()).limit(10).all()
    banks = BankAccount.query.filter_by(user_id=user_id).all()
    referrals = User.query.filter_by(referred_by=user_id).all()

    user_stats = dict(
        total_bets=Bet.query.filter_by(user_id=user_id).count(),
        total_wins=Bet.query.filter_by(user_id=user_id, result="WIN").count(),
        total_wagered=db.session.query(db.func.sum(Bet.bet_amount)).filter_by(
            user_id=user_id).scalar() or 0,
        total_won=db.session.query(db.func.sum(Bet.payout)).filter(
            Bet.user_id == user_id, Bet.result == "WIN").scalar() or 0,
        total_deposited=db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.user_id == user_id, Transaction.action == "DEPOSIT",
            Transaction.status == "completed").scalar() or 0,
    )

    return render_template("admin/user_detail.html", user=user,
                           recent_bets=recent_bets, recent_txns=recent_txns,
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
        target = request.form.get("target", "all")

        if not subject or not body:
            flash("Subject and message are required.", "error")
            return redirect(url_for("admin.email_users"))

        query = User.query.filter(User.email.isnot(None), User.email != "")
        if target == "active":
            query = query.filter_by(is_banned=False, is_suspended=False)

        users_with_email = query.all()
        sent_count = 0
        fail_count = 0
        for user in users_with_email:
            try:
                send_email(user.email, subject,
                           f"Hi {user.username},\n\n{body}\n\n- Ditto Dinky Team")
                sent_count += 1
                time.sleep(1)  # 1 second delay between emails to avoid Gmail throttling
            except Exception:
                fail_count += 1

        log_audit("BULK_EMAIL", f"Sent email to {sent_count} users. Subject: {subject}")
        flash(f"Email sent to {sent_count} users. {fail_count} failed.", "success")
        return redirect(url_for("admin.email_users"))

    total_with_email = User.query.filter(User.email.isnot(None), User.email != "").count()
    return render_template("admin/email_users.html", total_with_email=total_with_email)


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
