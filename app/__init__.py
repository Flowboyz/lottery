"""
Application factory for Ditto Dinky.
"""
import os

from flask import Flask

from config import config_map


def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
    )
    app.config.from_object(config_map.get(config_name, config_map["default"]))

    # Ensure instance folder exists (for SQLite)
    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance"), exist_ok=True)

    # Initialize extensions
    from app.extensions import db, login_manager, csrf, mail, migrate

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)

    # Exempt Paystack webhook from CSRF
    csrf.exempt("app.wallet.routes.paystack_webhook")

    # Register blueprints
    from app.auth import auth_bp
    from app.game import game_bp
    from app.wallet import wallet_bp
    from app.admin import admin_bp
    from app.notifications import notifications_bp
    from app.legal import legal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(game_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(legal_bp)

    # Template context processors
    @app.context_processor
    def inject_helpers():
        from app.utils import naira
        from flask_login import current_user as cu
        ctx = dict(naira=naira)
        if cu.is_authenticated:
            from app.models import Notification
            ctx["unread_count"] = Notification.query.filter_by(
                user_id=cu.id, is_read=False
            ).count()
        return ctx

    # Create tables on first request if they don't exist
    with app.app_context():
        from app.models import (
            User, OTP, Transaction, LotteryRound, Bet,
            PaymentRecord, WithdrawalRequest, Notification, AuditLog
        )
        db.create_all()

    return app
