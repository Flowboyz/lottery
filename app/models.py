"""
Database models for Ditto Dinky platform.
"""
import secrets
import string
from datetime import datetime, timedelta

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default="user")  # user | admin | superadmin

    # Profile
    phone = db.Column(db.String(20), nullable=True)
    is_email_verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)

    # Suspend / Ban
    is_suspended = db.Column(db.Boolean, default=False)
    suspended_until = db.Column(db.DateTime, nullable=True)
    is_banned = db.Column(db.Boolean, default=False)
    ban_reason = db.Column(db.String(200), nullable=True)

    # Wallet
    balance = db.Column(db.Float, default=0.0)

    # Responsible gambling
    daily_bet_total = db.Column(db.Float, default=0.0)
    daily_bet_date = db.Column(db.Date, nullable=True)
    is_self_excluded = db.Column(db.Boolean, default=False)
    self_exclusion_until = db.Column(db.DateTime, nullable=True)

    # Registration
    registration_ip = db.Column(db.String(45), nullable=True)
    
    # Security
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(45), nullable=True)
    last_play_time = db.Column(db.Integer, nullable=True)
    last_claim_time = db.Column(db.Integer, nullable=True)

    # Referral
    referral_code = db.Column(db.String(10), unique=True, nullable=True)
    referred_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    referral_tier_claimed = db.Column(db.Integer, default=0)  # highest tier milestone claimed

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    transactions = db.relationship("Transaction", backref="user", lazy="dynamic")
    notifications = db.relationship("Notification", backref="user", lazy="dynamic")
    bets = db.relationship("Bet", backref="user", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def generate_referral_code(self):
        chars = string.ascii_uppercase + string.digits
        self.referral_code = "".join(secrets.choice(chars) for _ in range(8))

    @property
    def is_locked(self):
        if self.locked_until and self.locked_until > datetime.utcnow():
            return True
        return False

    @property
    def account_status(self):
        if self.is_banned:
            return "banned"
        if self.is_suspended:
            if self.suspended_until and self.suspended_until > datetime.utcnow():
                return "suspended"
            else:
                self.is_suspended = False
                self.suspended_until = None
                db.session.commit()
                return "active"
        return "active"

    def __repr__(self):
        return f"<User {self.username}>"


# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------
class OTP(db.Model):
    __tablename__ = "otps"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    purpose = db.Column(db.String(20), nullable=False)  # verify_email | reset_password | admin_2fa
    is_used = db.Column(db.Boolean, default=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def generate(user_id, purpose, expiry_minutes=10):
        code = "".join(secrets.choice(string.digits) for _ in range(6))
        otp = OTP(
            user_id=user_id,
            code=code,
            purpose=purpose,
            expires_at=datetime.utcnow() + timedelta(minutes=expiry_minutes),
        )
        db.session.add(otp)
        db.session.commit()
        return code

    @property
    def is_valid(self):
        return not self.is_used and self.expires_at > datetime.utcnow()


# ---------------------------------------------------------------------------
# Transaction Ledger
# ---------------------------------------------------------------------------
class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    action = db.Column(db.String(30), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    balance_before = db.Column(db.Float, nullable=False)
    balance_after = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(100), nullable=True, index=True)
    description = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default="completed")
    method = db.Column(db.String(30), nullable=True)
    meta = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<Transaction {self.action} {self.amount}>"


# ---------------------------------------------------------------------------
# Lottery Round
# ---------------------------------------------------------------------------
class LotteryRound(db.Model):
    __tablename__ = "lottery_rounds"

    id = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, unique=True, nullable=False)
    lucky_number = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default="open")
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    settled_at = db.Column(db.DateTime, nullable=True)

    bets = db.relationship("Bet", backref="round", lazy="dynamic")


# ---------------------------------------------------------------------------
# Bet
# ---------------------------------------------------------------------------
class Bet(db.Model):
    __tablename__ = "bets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    round_id = db.Column(db.Integer, db.ForeignKey("lottery_rounds.id"), nullable=True)
    num1 = db.Column(db.Integer, nullable=False)
    num2 = db.Column(db.Integer, nullable=False)
    num3 = db.Column(db.Integer, nullable=False)
    picked_total = db.Column(db.Integer, nullable=False)
    bet_amount = db.Column(db.Float, nullable=False)
    lucky_number = db.Column(db.Integer, nullable=True)
    payout = db.Column(db.Float, default=0.0)
    result = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Payment Record
# ---------------------------------------------------------------------------
class PaymentRecord(db.Model):
    __tablename__ = "payment_records"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    provider = db.Column(db.String(20), nullable=False)
    payment_type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(100), unique=True, nullable=False)
    provider_reference = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default="pending")
    meta = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# Withdrawal Request
# ---------------------------------------------------------------------------
class WithdrawalRequest(db.Model):
    __tablename__ = "withdrawal_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(30), nullable=False)
    bank_name = db.Column(db.String(100), nullable=True)
    account_number = db.Column(db.String(20), nullable=True)
    account_name = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default="pending")
    admin_note = db.Column(db.String(255), nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id], backref="withdrawal_requests")
    reviewer = db.relationship("User", foreign_keys=[reviewed_by])


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------
class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(100), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(20), default="info")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------
class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="audit_logs")


# ---------------------------------------------------------------------------
# Bank Account
# ---------------------------------------------------------------------------
class BankAccount(db.Model):
    __tablename__ = "bank_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    bank_name = db.Column(db.String(100), nullable=False)
    account_number = db.Column(db.String(20), nullable=False)
    account_name = db.Column(db.String(100), nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("bank_accounts", lazy="dynamic"))


# ---------------------------------------------------------------------------
# Game Settings (dynamic config from admin panel)
# ---------------------------------------------------------------------------
class GameSettings(db.Model):
    __tablename__ = "game_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(200), nullable=False)
    label = db.Column(db.String(100), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)


# ---------------------------------------------------------------------------
# Announcement
# ---------------------------------------------------------------------------
class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)

    author = db.relationship("User", backref="announcements")
