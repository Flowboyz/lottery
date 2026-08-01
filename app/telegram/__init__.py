from flask import Blueprint

telegram_bp = Blueprint("telegram", __name__, url_prefix="/telegram")

from app.telegram import routes  # noqa: F401, E402
