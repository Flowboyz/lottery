from flask import Blueprint, render_template, session

admin_bp = Blueprint("admin", __name__)

@admin_bp.route("/admin")
def admin():
    return render_template(
        "admin.html",
        balance=session.get("balance", 0),
        history=session.get("history", [])
    )
