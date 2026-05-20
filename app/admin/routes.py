"""
Admin blueprint - dashboard, user management, withdrawal approval, analytics.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User, Transaction, Bet, WithdrawalRequest, PaymentRecord, AuditLog, Notification
from app.utils import admin_required, superadmin_required, credit_wallet, notify_user, log_audit, format_money

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ────────────────────────── DASHBOARD ──────────────────────────
@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    total_wagered = db.session.query(db.func.sum(Bet.bet_amount)).scalar() or 0
    total_payouts = db.session.query(db.func.sum(Bet.payout)).filter(
        Bet.result == "WIN"
    ).scalar() or 0

    stats = dict(
        total_users=User.query.count(),
        total_deposits=db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.action == "DEPOSIT", Transaction.status == "completed"
        ).scalar() or 0,
        total_bets=Bet.query.count(),
        total_wagered=total_wagered,
        total_payouts=total_payouts,
        pending_withdrawals=WithdrawalRequest.query.filter_by(status="pending").count(),
    )

    return render_template("admin/dashboard.html", stats=stats)


# ────────────────────────── USERS ──────────────────────────
@admin_bp.route("/users")
@login_required
@admin_required
def users():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "")
    query = User.query
    if search:
        query = query.filter(User.username.ilike(f"%{search}%"))
    users_page = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=25, error_out=False
    )
    return render_template("admin/users.html", users=users_page, q=search)


# ────────────────────────── UPDATE BALANCE (superadmin) ──────────────────────────
@admin_bp.route("/update-balance/<int:user_id>", methods=["POST"])
@login_required
@superadmin_required
def update_balance(user_id):
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
        return redirect(url_for("admin.users"))

    if action == "credit":
        credit_wallet(user, amount, "ADMIN_ADJUST",
                      description=f"Admin credited {format_money(amount)}",
                      method="admin")
    else:
        from app.utils import debit_wallet
        txn = debit_wallet(user, amount, "ADMIN_ADJUST",
                           description=f"Admin debited {format_money(amount)}",
                           method="admin")
        if not txn:
            flash("Insufficient balance for debit.", "error")
            return redirect(url_for("admin.users"))

    log_audit("ADMIN_BALANCE_UPDATE",
              f"{action.title()} {format_money(amount)} for {user.username}")
    flash(f"Balance updated for {user.username}.", "success")
    return redirect(url_for("admin.users"))


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
        log_audit("WITHDRAWAL_APPROVED", f"Withdrawal #{wr.id} approved for {format_money(wr.amount)}")
        flash(f"Withdrawal #{wr.id} approved.", "success")

    elif action == "reject":
        user = db.session.get(User, wr.user_id)
        credit_wallet(user, wr.amount, "WITHDRAWAL_REFUND",
                      description=f"Withdrawal #{wr.id} rejected - refund",
                      method="admin")

        wr.status = "rejected"
        wr.admin_note = request.form.get("reason", "No reason provided")
        wr.reviewed_by = current_user.id
        db.session.commit()

        notify_user(wr.user_id, "Withdrawal Rejected",
                    f"Your withdrawal of {format_money(wr.amount)} was rejected. Funds have been refunded.",
                    "withdrawal")
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
    bets_page = Bet.query.order_by(
        Bet.created_at.desc()
    ).paginate(page=page, per_page=30, error_out=False)
    return render_template("admin/bets.html", bets=bets_page)


# ────────────────────────── AUDIT LOGS ──────────────────────────
@admin_bp.route("/audit-logs")
@login_required
@superadmin_required
def audit_logs():
    page = request.args.get("page", 1, type=int)
    logs = AuditLog.query.order_by(
        AuditLog.created_at.desc()
    ).paginate(page=page, per_page=30, error_out=False)
    return render_template("admin/audit_logs.html", logs=logs)
