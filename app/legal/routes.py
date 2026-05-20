"""
Legal pages blueprint.
"""
from flask import Blueprint, render_template
from flask_login import login_required, current_user

from app.extensions import db
from app.models import User
from datetime import datetime, timedelta

legal_bp = Blueprint("legal", __name__, url_prefix="/legal")


@legal_bp.route("/terms")
def terms():
    return render_template("legal/terms.html")


@legal_bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html")


@legal_bp.route("/responsible-gambling")
def responsible_gambling():
    return render_template("legal/responsible_gambling.html")


@legal_bp.route("/self-exclude", methods=["POST"])
@login_required
def self_exclude():
    from flask import request, flash, redirect, url_for
    days = int(request.form.get("days", 7))
    current_user.is_self_excluded = True
    current_user.self_exclusion_until = datetime.utcnow() + timedelta(days=days)
    db.session.commit()
    flash(f"You have been self-excluded for {days} days.", "info")
    return redirect(url_for("game.home"))
