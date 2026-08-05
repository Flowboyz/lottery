"""
HTTP Routes for the Whot Card Game.
Lobby list, room routing, and past histories.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.extensions import db, csrf
from app.models import WhotGame, User
from app.utils import get_setting

whot_bp = Blueprint("whot", __name__, url_prefix="/whot")


@whot_bp.route("/")
@login_required
def index():
    if get_setting("WHOT_ENABLED", "1") == "0":
        flash("Whot is temporarily disabled by admin.", "warning")
        return redirect(url_for("game.home"))

    if get_setting("MAINTENANCE_MODE", "off") == "on":
        flash("Whot is temporarily paused for maintenance.", "warning")
        return redirect(url_for("game.home"))

    # Load active user history
    history = WhotGame.query.filter(
        (WhotGame.player1_id == current_user.id) | (WhotGame.player2_id == current_user.id)
    ).order_by(WhotGame.created_at.desc()).limit(10).all()

    # Find if there is any ongoing active or negotiating game to allow rejoin
    active_game = WhotGame.query.filter(
        ((WhotGame.player1_id == current_user.id) | (WhotGame.player2_id == current_user.id)) &
        (WhotGame.status.in_(["active", "negotiating"]))
    ).order_by(WhotGame.id.desc()).first()

    # Stakes configurations
    stakes = [500, 1000, 2000, 5000]

    return render_template(
        "whot/index.html",
        stakes=stakes,
        history=history,
        active_game=active_game,
        balance=current_user.balance
    )


@whot_bp.route("/game/<string:room_id>")
@login_required
def game_room(room_id):
    if get_setting("WHOT_ENABLED", "1") == "0":
        flash("Whot is temporarily disabled by admin.", "warning")
        return redirect(url_for("game.home"))

    if get_setting("MAINTENANCE_MODE", "off") == "on":
        flash("Whot is temporarily paused for maintenance.", "warning")
        return redirect(url_for("game.home"))

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game:
        flash("Game room not found.", "error")
        return redirect(url_for("whot.index"))

    # Expiration Check (10 minutes limit for unjoined rooms)
    if game.player2_id is None:
        from datetime import datetime
        duration = datetime.utcnow() - game.created_at
        if duration.total_seconds() > 600:
            db.session.delete(game)
            db.session.commit()
            flash("This challenge invitation link has expired (10 minutes limit). Please generate a new one.", "error")
            return redirect(url_for("whot.index"))

    # Validate that current_user is in this game (or allow joining if player2 is pending)
    if game.player1_id != current_user.id and game.player2_id != current_user.id and game.player2_id is not None:
        flash("You are not authorized to join this game room.", "error")
        return redirect(url_for("whot.index"))

    opponent = None
    if game.is_bot_game:
        opponent = {"username": "Computer Bot", "id": 0}
    else:
        opp_user = game.player2 if game.player1_id == current_user.id else game.player1
        if opp_user:
            opponent = {"username": opp_user.username, "id": opp_user.id}
        else:
            opponent = {"username": "Waiting for player...", "id": None}

    return render_template(
        "whot/game.html",
        game=game,
        opponent=opponent,
        is_p1=(game.player1_id == current_user.id)
    )


@whot_bp.route("/challenge/create", methods=["POST"])
@csrf.exempt
@login_required
def create_challenge_link():
    if get_setting("WHOT_ENABLED", "1") == "0":
        return {"error": "Whot is temporarily disabled by admin."}, 400

    if current_user.balance < 500:
        return {"error": "Minimum balance of ₦500 required to host a challenge."}, 400

    import random
    import time

    room_id = f"whot_link_{int(time.time())}_{random.randint(1000, 9999)}"
    
    state = {
        "p1_id": current_user.id,
        "p2_id": None,
        "p1_username": current_user.username,
        "p2_username": None,
        "p1_balance": current_user.balance,
        "p2_balance": 0.0,
        "p1_proposal": None,
        "p2_proposal": None,
        "last_action": f"{current_user.username} created an invite! Waiting for an opponent to join."
    }

    game = WhotGame(
        room_id=room_id,
        player1_id=current_user.id,
        player2_id=None,
        is_bot_game=False,
        stake=0.0,
        pool=0.0,
        commission=0.0,
        status="negotiating"
    )
    game.game_state = state
    
    db.session.add(game)
    db.session.commit()
    
    join_url = url_for("whot.join_challenge_link", room_id=room_id, _external=True)
    return {"join_url": join_url, "room_id": room_id}


@whot_bp.route("/challenge/join/<string:room_id>")
def join_challenge_link(room_id):
    if get_setting("WHOT_ENABLED", "1") == "0":
        flash("Whot is temporarily disabled by admin.", "warning")
        return redirect(url_for("game.home"))

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game:
        flash("Invitation link not found or expired.", "error")
        return redirect(url_for("whot.index"))

    # Expiration Check (10 minutes limit for unjoined rooms)
    if game.player2_id is None:
        from datetime import datetime
        duration = datetime.utcnow() - game.created_at
        if duration.total_seconds() > 600:
            db.session.delete(game)
            db.session.commit()
            flash("This challenge invitation link has expired (10 minutes limit). Please generate a new one.", "error")
            return redirect(url_for("whot.index"))

    # If user is not logged in, redirect to registration prefilling referral code
    if not current_user.is_authenticated:
        host = User.query.get(game.player1_id)
        ref_code = host.referral_code if host else ""
        flash("Please create an account or login to accept this challenge.", "info")
        return redirect(url_for("auth.register", ref=ref_code, next=url_for("whot.join_challenge_link", room_id=room_id)))

    if get_setting("MAINTENANCE_MODE", "off") == "on":
        flash("Whot is temporarily paused for maintenance.", "warning")
        return redirect(url_for("game.home"))

    # If player 1 is joining, send them to the game felt
    if game.player1_id == current_user.id:
        return redirect(url_for("whot.game_room", room_id=room_id))

    # Check status
    if game.status != "negotiating":
        flash("This invitation has already been accepted or is no longer available.", "warning")
        return redirect(url_for("whot.index"))

    # Check player 2 slot
    if game.player2_id is not None and game.player2_id != current_user.id:
        flash("This game is already full.", "error")
        return redirect(url_for("whot.index"))

    # Claim slot if empty
    if game.player2_id is None:
        if current_user.balance < 500:
            flash("You need at least ₦500 balance to join a challenge.", "error")
            return redirect(url_for("whot.index"))

        game.player2_id = current_user.id
        state = game.game_state
        state["p2_id"] = current_user.id
        state["p2_username"] = current_user.username
        state["p2_balance"] = current_user.balance
        state["last_action"] = f"{current_user.username} joined the challenge! Propose a stake to play."
        game.game_state = state
        
        db.session.commit()

        # Emit websocket state refresh to active host waiting in game room
        from app.extensions import socketio
        socketio.emit("whot_negotiate_state", {
            "status": game.status,
            "p1_id": state["p1_id"],
            "p2_id": state["p2_id"],
            "p1_username": state["p1_username"],
            "p2_username": state["p2_username"],
            "p1_balance": state["p1_balance"],
            "p2_balance": state["p2_balance"],
            "p1_proposal": state["p1_proposal"],
            "p2_proposal": state["p2_proposal"],
            "last_action": state["last_action"]
        }, room=game.room_id)

    return redirect(url_for("whot.game_room", room_id=room_id))


@whot_bp.route("/game/<string:room_id>/status")
@login_required
def game_status(room_id):
    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game:
        return {"error": "Game not found"}, 404

    if game.player1_id != current_user.id and game.player2_id != current_user.id:
        return {"error": "Unauthorized"}, 403

    state = game.game_state

    # Negotiation phase status
    if game.status == "negotiating":
        payload = {
            "status": game.status,
            "p1_id": state.get("p1_id"),
            "p2_id": state.get("p2_id"),
            "p1_username": state.get("p1_username"),
            "p2_username": state.get("p2_username"),
            "p1_balance": state.get("p1_balance", 0.0),
            "p2_balance": state.get("p2_balance", 0.0),
            "p1_proposal": state.get("p1_proposal"),
            "p2_proposal": state.get("p2_proposal"),
            "last_action": state.get("last_action"),
            "updated_at": game.updated_at.isoformat() if game.updated_at else ""
        }
        return payload

    # Active/Completed/Forfeited phase status
    is_p1 = (game.player1_id == current_user.id)
    my_hand = state.get("p1_hand", []) if is_p1 else state.get("p2_hand", [])
    opp_hand = state.get("p2_hand", []) if is_p1 else state.get("p1_hand", [])
    opp_card_count = len(opp_hand) if opp_hand else 0

    payload = {
        "status": game.status,
        "stake": game.stake,
        "pool": game.pool,
        "active_turn_id": game.active_turn_id,
        "my_hand": my_hand,
        "opp_card_count": opp_card_count,
        "top_card": state.get("discard_pile", [None])[-1] if state.get("discard_pile") else None,
        "called_suit": state.get("called_suit"),
        "active_penalty_picks": state.get("active_penalty_picks", 0),
        "penalty_type": state.get("penalty_type"),
        "last_action": state.get("last_action"),
        "draw_deck_count": len(state.get("deck", [])) if state.get("deck") is not None else 0,
        "winner_id": game.winner_id,
        "is_bot_game": game.is_bot_game,
        "updated_at": game.updated_at.isoformat() if game.updated_at else ""
    }
    return payload
