"""
Wallet blueprint - deposits, withdrawals, transaction history, Paystack.
"""
import hashlib
import hmac
import json
import requests

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, current_app, jsonify,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Transaction, PaymentRecord, WithdrawalRequest
from app.utils import credit_wallet, debit_wallet, notify_user, generate_reference, format_money, send_email

wallet_bp = Blueprint("wallet", __name__, url_prefix="/wallet")


# ────────────────────────── DEPOSIT PAGE ──────────────────────────
@wallet_bp.route("/deposit", methods=["GET"])
@login_required
def deposit_page():
    return render_template("wallet/deposit.html",
                           paystack_key=current_app.config["PAYSTACK_PUBLIC_KEY"])


# ────────────────────────── INITIALIZE PAYSTACK PAYMENT ──────────────────────────
@wallet_bp.route("/deposit/initialize", methods=["POST"])
@login_required
def initialize_deposit():
    try:
        amount = float(request.form.get("amount", 0))
    except (ValueError, TypeError):
        flash("Invalid amount.", "error")
        return redirect(url_for("wallet.deposit_page"))

    min_deposit = current_app.config["MIN_DEPOSIT"]
    if amount < min_deposit:
        flash(f"Minimum deposit is {format_money(min_deposit)}.", "error")
        return redirect(url_for("wallet.deposit_page"))

    reference = generate_reference("DEP")

    # Create payment record
    record = PaymentRecord(
        user_id=current_user.id,
        provider="paystack",
        payment_type="deposit",
        amount=amount,
        reference=reference,
        status="pending",
    )
    db.session.add(record)
    db.session.commit()

    # Initialize Paystack transaction
    secret_key = current_app.config["PAYSTACK_SECRET_KEY"]
    if not secret_key:
        # Fallback: direct credit for testing without Paystack
        credit_wallet(current_user, amount, "DEPOSIT",
                      description="Direct deposit (test mode)", method="direct",
                      reference=reference)
        record.status = "success"
        db.session.commit()
        notify_user(current_user.id, "Deposit Successful",
                    f"Your deposit of {format_money(amount)} was successful!", "deposit")
        send_email(current_user.email, "Deposit Successful - Ditto Dinky",
                   f"Hi {current_user.username},\n\n"
                   f"Your deposit of {format_money(amount)} has been credited to your wallet.\n\n"
                   f"New Balance: {format_money(current_user.balance)}\n"
                   f"Reference: {reference}\n\n"
                   f"Thank you for playing!\n- Ditto Dinky Team")
        flash(f"Deposited {format_money(amount)} successfully! (Test mode)", "success")
        return redirect(url_for("game.home"))

    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "email": current_user.email or f"{current_user.username}@dittodinky.local",
        "amount": int(amount * 100),  # Paystack uses kobo
        "reference": reference,
        "callback_url": url_for("wallet.verify_deposit", _external=True),
    }

    try:
        resp = requests.post(
            f"{current_app.config['PAYSTACK_BASE_URL']}/transaction/initialize",
            json=payload, headers=headers, timeout=15
        )
        data = resp.json()
        if data.get("status"):
            return redirect(data["data"]["authorization_url"])
        else:
            flash("Payment initialization failed. Try again.", "error")
            record.status = "failed"
            db.session.commit()
    except Exception as e:
        flash("Payment service unavailable. Try again later.", "error")
        record.status = "failed"
        db.session.commit()

    return redirect(url_for("wallet.deposit_page"))


# ────────────────────────── VERIFY DEPOSIT (callback) ──────────────────────────
@wallet_bp.route("/deposit/verify")
@login_required
def verify_deposit():
    reference = request.args.get("reference", "")
    if not reference:
        flash("Invalid payment reference.", "error")
        return redirect(url_for("wallet.deposit_page"))

    record = PaymentRecord.query.filter_by(reference=reference).first()
    if not record:
        flash("Payment not found.", "error")
        return redirect(url_for("wallet.deposit_page"))

    if record.status == "success":
        flash("This payment has already been verified.", "info")
        return redirect(url_for("game.home"))

    # Verify with Paystack
    secret_key = current_app.config["PAYSTACK_SECRET_KEY"]
    headers = {"Authorization": f"Bearer {secret_key}"}
    try:
        resp = requests.get(
            f"{current_app.config['PAYSTACK_BASE_URL']}/transaction/verify/{reference}",
            headers=headers, timeout=15
        )
        data = resp.json()
        if data.get("status") and data["data"]["status"] == "success":
            amount = data["data"]["amount"] / 100  # kobo to naira
            record.status = "success"
            record.provider_reference = data["data"].get("id")
            credit_wallet(current_user, amount, "DEPOSIT",
                          description="Paystack deposit", method="paystack",
                          reference=reference)
            db.session.commit()
            notify_user(current_user.id, "Deposit Successful",
                        f"Your deposit of {format_money(amount)} was successful!", "deposit")
            send_email(current_user.email, "Deposit Successful - Ditto Dinky",
                       f"Hi {current_user.username},\n\n"
                       f"Your deposit of {format_money(amount)} has been credited to your wallet.\n\n"
                       f"New Balance: {format_money(current_user.balance)}\n"
                       f"Reference: {reference}\n\n"
                       f"Thank you for playing!\n- Ditto Dinky Team")
            flash(f"Deposited {format_money(amount)} successfully!", "success")
        else:
            record.status = "failed"
            db.session.commit()
            flash("Payment verification failed.", "error")
    except Exception:
        flash("Could not verify payment. Contact support.", "error")

    return redirect(url_for("game.home"))


# ────────────────────────── PAYSTACK WEBHOOK ──────────────────────────
@wallet_bp.route("/webhook/paystack", methods=["POST"])
def paystack_webhook():
    """Handle Paystack webhook for failed transaction recovery."""
    secret_key = current_app.config["PAYSTACK_SECRET_KEY"]
    signature = request.headers.get("x-paystack-signature", "")
    body = request.get_data()

    # Verify signature
    expected = hmac.new(secret_key.encode(), body, hashlib.sha512).hexdigest()
    if signature != expected:
        return jsonify({"error": "Invalid signature"}), 400

    payload = json.loads(body)
    event = payload.get("event", "")
    data = payload.get("data", {})

    if event == "charge.success":
        reference = data.get("reference")
        record = PaymentRecord.query.filter_by(reference=reference).first()
        if record and record.status != "success":
            from app.models import User
            user = db.session.get(User, record.user_id)
            if user:
                amount = data["amount"] / 100
                record.status = "success"
                credit_wallet(user, amount, "DEPOSIT",
                              description="Paystack webhook recovery",
                              method="paystack", reference=reference)
                db.session.commit()
                notify_user(user.id, "Deposit Confirmed",
                            f"Your deposit of {format_money(amount)} has been confirmed.", "deposit")
                send_email(user.email, "Deposit Confirmed - Ditto Dinky",
                           f"Hi {user.username},\n\n"
                           f"Your deposit of {format_money(amount)} has been confirmed and credited.\n\n"
                           f"New Balance: {format_money(user.balance)}\n"
                           f"Reference: {reference}\n\n"
                           f"- Ditto Dinky Team")

    return jsonify({"status": "ok"}), 200


# ────────────────────────── WITHDRAWAL PAGE ──────────────────────────
@wallet_bp.route("/withdraw", methods=["GET"])
@login_required
def withdraw_page():
    pending = WithdrawalRequest.query.filter_by(
        user_id=current_user.id, status="pending"
    ).count()
    from app.models import BankAccount
    saved_banks = BankAccount.query.filter_by(user_id=current_user.id).order_by(
        BankAccount.is_default.desc(), BankAccount.created_at.desc()
    ).all()
    return render_template("wallet/withdraw.html",
                           pending_withdrawals=pending, saved_banks=saved_banks)


# ────────────────────────── SUBMIT WITHDRAWAL ──────────────────────────
@wallet_bp.route("/withdraw/submit", methods=["POST"])
@login_required
def submit_withdrawal():
    try:
        amount = float(request.form.get("amount", 0))
    except (ValueError, TypeError):
        flash("Invalid amount.", "error")
        return redirect(url_for("wallet.withdraw_page"))

    method = request.form.get("method", "bank")
    bank_name = request.form.get("bank_name", "")
    account_number = request.form.get("account_number", "")
    account_name = request.form.get("account_name", "")

    min_withdraw = current_app.config["MIN_WITHDRAWAL"]
    if amount < min_withdraw:
        flash(f"Minimum withdrawal is {format_money(min_withdraw)}.", "error")
        return redirect(url_for("wallet.withdraw_page"))

    if amount > current_user.balance:
        flash("Insufficient balance.", "error")
        return redirect(url_for("wallet.withdraw_page"))

    # Debit wallet immediately (hold funds)
    txn = debit_wallet(current_user, amount, "WITHDRAWAL",
                       description="Withdrawal request (pending approval)",
                       method=method)
    if not txn:
        flash("Insufficient balance.", "error")
        return redirect(url_for("wallet.withdraw_page"))

    # Create withdrawal request
    wr = WithdrawalRequest(
        user_id=current_user.id,
        amount=amount,
        method=method,
        bank_name=bank_name,
        account_number=account_number,
        account_name=account_name,
    )
    db.session.add(wr)
    db.session.commit()

    notify_user(current_user.id, "Withdrawal Submitted",
                f"Your withdrawal of {format_money(amount)} is pending approval.", "withdrawal")
    send_email(current_user.email, "Withdrawal Request Submitted - Ditto Dinky",
               f"Hi {current_user.username},\n\n"
               f"Your withdrawal request has been submitted.\n\n"
               f"Amount: {format_money(amount)}\n"
               f"Bank: {bank_name}\n"
               f"Account: {account_number}\n"
               f"Account Name: {account_name}\n\n"
               f"Your request will be reviewed within 24 hours. "
               f"Funds have been held from your balance until approval.\n\n"
               f"- Ditto Dinky Team")
    flash(f"Withdrawal of {format_money(amount)} submitted for approval.", "success")
    return redirect(url_for("game.home"))


# ────────────────────────── TRANSACTION HISTORY ──────────────────────────
@wallet_bp.route("/history")
@login_required
def history():
    page = request.args.get("page", 1, type=int)
    txns = Transaction.query.filter_by(user_id=current_user.id).order_by(
        Transaction.created_at.desc()
    ).paginate(page=page, per_page=20, error_out=False)

    return render_template("wallet/history.html", transactions=txns)
