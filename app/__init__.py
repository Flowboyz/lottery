"""
Application factory for Ditto Dinky.
"""
import os
from flask import Flask, render_template

from config import config_map
from app.extensions import socketio, db, login_manager, csrf, mail, migrate


def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development")

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"),
    )
    app.config.from_object(config_map.get(config_name, config_map["default"]))

    # Ensure instance folder exists
    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance"), exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please login to continue."
    login_manager.login_message_category = "info"
    csrf.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)

    # Initialize SocketIO
    socketio.init_app(app, cors_allowed_origins="*", async_mode="gevent")

    # Exempt Paystack webhook from CSRF
    csrf.exempt("app.wallet.routes.paystack_webhook")

    # Register blueprints
    from app.auth import auth_bp
    from app.game import game_bp
    from app.wallet import wallet_bp
    from app.admin import admin_bp
    from app.notifications import notifications_bp
    from app.legal import legal_bp
    from app.profile import profile_bp
    from app.games import games_bp
    from app.aviator import aviator_bp
    from app.color import color_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(game_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(legal_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(games_bp)
    app.register_blueprint(aviator_bp)
    app.register_blueprint(color_bp)
    

    # Template context processors
    @app.context_processor
    def inject_helpers():
        from app.utils import naira
        from flask_login import current_user as cu
        from datetime import datetime

        ctx = {"naira": naira}

        if cu.is_authenticated:
            from app.models import Notification
            ctx["unread_count"] = Notification.query.filter_by(
                user_id=cu.id, is_read=False
            ).count()

        from app.models import Announcement
        ctx["announcements"] = Announcement.query.filter_by(is_active=True).filter(
            db.or_(Announcement.expires_at.is_(None), Announcement.expires_at > datetime.utcnow())
        ).order_by(Announcement.created_at.desc()).limit(3).all()

        return ctx

    # Create tables (for development). Remove in production if using migrations.
    with app.app_context():
        from app.models import (
            User, OTP, Transaction, LotteryRound, Bet,
            PaymentRecord, WithdrawalRequest, Notification, AuditLog,
            BankAccount, GameSettings, Announcement, GamePlay
        )
        from app.models_games import ColorRound, ColorEntry, AviatorRound, AviatorEntry
        db.create_all()

    # Error handlers
    @app.errorhandler(401)
    def unauthorized(e):
        return render_template("errors.html", error_code=401, message="Please login to access this page."), 401

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors.html", error_code=403, message="You don't have permission to access this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors.html", error_code=404, message="This page doesn't exist."), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors.html", error_code=500, message="Something went wrong. Please try again."), 500

    return app