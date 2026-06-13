from flask import Blueprint

aviator_bp = Blueprint("aviator", __name__, url_prefix="/games/aviator")

from app.aviator import routes   # noqa: F401, E402
from app.aviator import sockets  # noqa: F401, E402   <-- NEW