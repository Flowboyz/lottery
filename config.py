"""
Ditto Dinky - Configuration
"""
import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Database ---
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(basedir, 'instance', 'ditto_dinky.db')}"
    )

    # --- Session ---
    PERMANENT_SESSION_LIFETIME = timedelta(hours=6)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # --- CSRF ---
    WTF_CSRF_ENABLED = True

    # --- Currency ---
    CURRENCY_SYMBOL = "₦"
    CURRENCY_CODE = "NGN"
    MIN_DEPOSIT = 500        # ₦500
    MIN_WITHDRAWAL = 1000    # ₦1,000
    MAX_DAILY_BET = 50000    # ₦50,000

    # --- Lottery Engine ---
    WIN_PROBABILITY = 0.10
    PAYOUT_MULTIPLIER = 5
    COOLDOWN_SECONDS = 5
    ROUND_DURATION_SECONDS = 60
    DAILY_CLAIM_AMOUNT = 500  # ₦500 daily free claim
    DAILY_CLAIM_COOLDOWN = 86400

    # --- Paystack ---
    PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
    PAYSTACK_BASE_URL = "https://api.paystack.co"

    # --- Email / SMTP ---
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "noreply@dittodinky.com")

    # --- Referral ---
    REFERRAL_BONUS = 200     # ₦200 per referral
    SIGNUP_BONUS = 100       # ₦100 for new users

    # --- Telegram Bot ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")

    # --- Security ---
    MAX_LOGIN_ATTEMPTS = 5
    LOGIN_LOCKOUT_MINUTES = 15
    OTP_EXPIRY_MINUTES = 10
    BCRYPT_LOG_ROUNDS = 12


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
