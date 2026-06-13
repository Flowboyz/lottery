from flask import Blueprint

color_bp = Blueprint("color", __name__, url_prefix="/games/color")

from app.color import routes  # noqa: F401, E402
