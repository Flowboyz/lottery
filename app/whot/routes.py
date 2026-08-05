"""
HTTP Routes for the Whot Card Game.
Lobby list, room routing, and past histories.
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user

from app.extensions import db, csrf
from app.models import WhotGame, User
from app.utils import get_setting, credit_wallet, notify_user, debit_wallet, log_audit
from app.whot import whot_engine
import random
import time

whot_bp = Blueprint("whot", __name__, url_prefix="/whot")

STALE_TIMEOUT_SECONDS = 600  # 10 minutes


def cleanup_stale_whot_games(user_id):
    """
    Expire any negotiating or active Whot games for this user
    that have been idle for more than STALE_TIMEOUT_SECONDS.
    Refunds stakes for active games.
    """
    stale_games = WhotGame.query.filter(
        ((WhotGame.player1_id == user_id) | (WhotGame.player2_id == user_id)),
        WhotGame.status.in_(["negotiating", "active"])
    ).all()

    cleaned = 0
    for game in stale_games:
        elapsed = (datetime.utcnow() - (game.updated_at or game.created_at)).total_seconds()
        if elapsed < STALE_TIMEOUT_SECONDS:
            continue  # Still within the active window

        # Refund stakes for active games (money was already debited)
        if game.status == "active" and game.stake > 0:
            p1 = User.query.get(game.player1_id)
            if p1:
                credit_wallet(p1, game.stake, "REFUND",
                              description=f"Refund for expired Whot match (Room {game.room_id})")
                notify_user(p1.id, "Whot Game Expired",
                            f"Your Whot match (₦{game.stake:,.0f} stake) expired due to inactivity. Your stake has been refunded.",
                            "info")

            if game.player2_id and not game.is_bot_game:
                p2 = User.query.get(game.player2_id)
                if p2:
                    credit_wallet(p2, game.stake, "REFUND",
                                  description=f"Refund for expired Whot match (Room {game.room_id})")
                    notify_user(p2.id, "Whot Game Expired",
                                f"Your Whot match (₦{game.stake:,.0f} stake) expired due to inactivity. Your stake has been refunded.",
                                "info")

        game.status = "expired"
        cleaned += 1

    if cleaned:
        db.session.commit()


@whot_bp.route("/")
@login_required
def index():
    if get_setting("WHOT_ENABLED", "1") == "0":
        flash("Whot is temporarily disabled by admin.", "warning")
        return redirect(url_for("game.home"))

    if get_setting("MAINTENANCE_MODE", "off") == "on":
        flash("Whot is temporarily paused for maintenance.", "warning")
        return redirect(url_for("game.home"))

    # Auto-expire stale games and refund trapped stakes
    cleanup_stale_whot_games(current_user.id)

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


def verify_deck_size_http(state):
    """Recycle discard pile into deck if running low."""
    if len(state["deck"]) < 5:
        top_card = state["discard_pile"].pop()
        recycled = state["discard_pile"]
        random.shuffle(recycled)
        state["deck"].extend(recycled)
        state["discard_pile"] = [top_card]


def handle_bot_turn_http(game):
    """Execute the bot's turn directly in the HTTP request. Returns True if bot won."""
    state = game.game_state
    bot_hand = state["p2_hand"]
    top_card = state["discard_pile"][-1]
    penalty_picks = state.get("active_penalty_picks", 0)
    penalty_type = state.get("penalty_type")
    called_suit = state.get("called_suit")

    play_idx, bot_call = whot_engine.get_bot_play(
        bot_hand, top_card, called_suit, penalty_picks, penalty_type
    )

    action_text = ""
    next_turn_id = game.player1_id

    if play_idx is not None:
        card = bot_hand.pop(play_idx)
        state["discard_pile"].append(card)
        action_text = f"Computer Bot played {card['suit']} {card['value']}."

        val = card["value"]
        if val == 1 or val == 8:
            next_turn_id = 0
            action_text += " (Plays again!)"
        elif val == 2:
            state["active_penalty_picks"] = state.get("active_penalty_picks", 0) + 2
            state["penalty_type"] = 2
            action_text += f" (Pick Two stacked! Penalty: {state['active_penalty_picks']})"
        elif val == 5:
            state["active_penalty_picks"] = state.get("active_penalty_picks", 0) + 3
            state["penalty_type"] = 3
            action_text += f" (Pick Three stacked! Penalty: {state['active_penalty_picks']})"
        elif val == 14:
            if state["deck"]:
                state["p1_hand"].append(state["deck"].pop())
            action_text += " (General Market - you draw 1 card!)"
            state["called_suit"] = None
        elif val == 20:
            state["called_suit"] = bot_call
            state["active_penalty_picks"] = 0
            state["penalty_type"] = None
            action_text += f" (Whot wildcard! Called {bot_call.capitalize()}.)"
        else:
            state["called_suit"] = None

        if len(bot_hand) == 0:
            # Bot wins
            game.status = "completed"
            game.winner_id = None
            state["last_action"] = "Game Over! Computer Bot won the match."
            game.game_state = state
            db.session.commit()
            return True
    else:
        verify_deck_size_http(state)
        if penalty_picks > 0:
            drawn = []
            for _ in range(penalty_picks):
                if state["deck"]:
                    drawn.append(state["deck"].pop())
            bot_hand.extend(drawn)
            action_text = f"Computer Bot drew {penalty_picks} penalty cards."
            state["active_penalty_picks"] = 0
            state["penalty_type"] = None
        else:
            if state["deck"]:
                bot_hand.append(state["deck"].pop())
                action_text = "Computer Bot went to market (drew 1 card)."

    game.active_turn_id = next_turn_id
    state["last_action"] = action_text
    # Set turn deadline for the next player
    state["turn_deadline"] = time.time() + 15.0
    game.game_state = state
    db.session.commit()
    return False


def handle_turn_timeout_http(game):
    """Check and execute turn timeout. Returns True if timeout was applied."""
    state = game.game_state
    deadline = state.get("turn_deadline", 0)

    if deadline == 0 or time.time() < deadline:
        return False  # Not timed out yet

    # Turn has timed out — auto-draw
    is_p1_turn = (game.active_turn_id == game.player1_id)
    active_hand = state["p1_hand"] if is_p1_turn else state["p2_hand"]
    verify_deck_size_http(state)

    p1_name = state.get("p1_username") or "Player"
    p2_name = state.get("p2_username") or "Computer Bot"
    active_name = p1_name if is_p1_turn else p2_name

    action_text = ""
    penalty_picks = state.get("active_penalty_picks", 0)

    if penalty_picks > 0:
        drawn = []
        for _ in range(penalty_picks):
            if state["deck"]:
                drawn.append(state["deck"].pop())
        active_hand.extend(drawn)
        action_text = f"{active_name} drew {penalty_picks} penalty cards on timeout."
        state["active_penalty_picks"] = 0
        state["penalty_type"] = None
    else:
        if state["deck"]:
            active_hand.append(state["deck"].pop())
            action_text = f"{active_name} went to market on timeout."

    # Switch turn
    next_turn = game.player2_id if is_p1_turn else game.player1_id
    if game.is_bot_game and is_p1_turn:
        next_turn = 0
    game.active_turn_id = next_turn

    state["last_action"] = action_text
    state["turn_deadline"] = time.time() + 15.0
    game.game_state = state
    db.session.commit()
    return True


@whot_bp.route("/game/<string:room_id>/status")
@login_required
def game_status(room_id):
    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game:
        return {"error": "Game not found"}, 404

    if game.player1_id != current_user.id and game.player2_id != current_user.id:
        return {"error": "Unauthorized"}, 403

    # ── Drive game forward on poll ──
    if game.status == "active":
        # 1. Check turn timeout first
        handle_turn_timeout_http(game)

        # 2. If it's the bot's turn, play it now
        if game.is_bot_game and game.active_turn_id == 0:
            # Add artificial delay: don't let bot play instantly if its turn just started.
            # Give the UI at least 1.5 seconds to show the player's last move.
            turn_time_elapsed = 15.0 - (game.game_state.get("turn_deadline", 0) - time.time())
            if turn_time_elapsed >= 1.5:
                # Bot may chain multiple turns (hold-on / suspend cards)
                max_chain = 10  # Safety limit to prevent infinite loops
                for _ in range(max_chain):
                    bot_won = handle_bot_turn_http(game)
                    if bot_won or game.active_turn_id != 0:
                        break

    # Re-read state after any modifications
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
        "turn_deadline": state.get("turn_deadline", 0),
        "server_time": time.time(),
        "updated_at": game.updated_at.isoformat() if game.updated_at else ""
    }
    return payload


# ────────────────────────── HTTP MATCHMAKING ──────────────────────────

MATCHMAKING_TIMEOUT = 120  # 2 minutes before matchmaking expires


@whot_bp.route("/matchmaking/join", methods=["POST"])
@csrf.exempt
@login_required
def matchmaking_join():
    """Join the matchmaking queue via database."""
    if get_setting("WHOT_ENABLED", "1") == "0":
        return jsonify({"error": "Whot is temporarily disabled by admin."}), 400

    if current_user.balance < 500:
        return jsonify({"error": "Minimum balance of ₦500 required to find matches."}), 400

    # Check if user already has a waiting matchmaking game
    my_waiting = WhotGame.query.filter(
        WhotGame.player1_id == current_user.id,
        WhotGame.status == "negotiating",
        WhotGame.player2_id.is_(None),
        WhotGame.room_id.like("whot_match_%")
    ).first()

    if my_waiting:
        # Already in queue — check if someone joined
        if my_waiting.player2_id:
            return jsonify({"status": "matched", "room_id": my_waiting.room_id})
        # Still waiting
        return jsonify({"status": "waiting", "room_id": my_waiting.room_id})

    # Look for another user's waiting matchmaking game to join
    available = WhotGame.query.filter(
        WhotGame.player1_id != current_user.id,
        WhotGame.status == "negotiating",
        WhotGame.player2_id.is_(None),
        WhotGame.room_id.like("whot_match_%"),
        WhotGame.is_bot_game == False
    ).order_by(WhotGame.created_at.asc()).first()

    if available:
        # Check expiry
        elapsed = (datetime.utcnow() - (available.updated_at or available.created_at)).total_seconds()
        if elapsed > MATCHMAKING_TIMEOUT:
            available.status = "expired"
            db.session.commit()
            # Fall through to create new waiting game
        else:
            # Match found! Join as player 2
            opponent = User.query.get(available.player1_id)
            if opponent and opponent.balance >= 500:
                available.player2_id = current_user.id
                state = available.game_state
                state["p2_id"] = current_user.id
                state["p2_username"] = current_user.username
                state["p2_balance"] = current_user.balance
                state["last_action"] = "Match found! Propose a stake to play."
                available.game_state = state
                db.session.commit()

                return jsonify({"status": "matched", "room_id": available.room_id})
            else:
                # Opponent invalid, expire their game
                available.status = "expired"
                db.session.commit()

    # No match found — create a new waiting game
    room_id = f"whot_match_{int(time.time())}_{random.randint(1000, 9999)}"

    state = {
        "p1_id": current_user.id,
        "p2_id": None,
        "p1_username": current_user.username,
        "p2_username": None,
        "p1_balance": current_user.balance,
        "p2_balance": 0.0,
        "p1_proposal": None,
        "p2_proposal": None,
        "last_action": f"{current_user.username} is waiting for an opponent..."
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

    return jsonify({"status": "waiting", "room_id": room_id})


@whot_bp.route("/matchmaking/status")
@login_required
def matchmaking_status():
    """Poll to check if a match has been found."""
    my_waiting = WhotGame.query.filter(
        WhotGame.player1_id == current_user.id,
        WhotGame.room_id.like("whot_match_%"),
        WhotGame.status == "negotiating"
    ).order_by(WhotGame.id.desc()).first()

    if not my_waiting:
        return jsonify({"status": "none"})

    # Check if opponent joined
    if my_waiting.player2_id:
        return jsonify({"status": "matched", "room_id": my_waiting.room_id})

    # Check timeout
    elapsed = (datetime.utcnow() - (my_waiting.updated_at or my_waiting.created_at)).total_seconds()
    if elapsed > MATCHMAKING_TIMEOUT:
        my_waiting.status = "expired"
        db.session.commit()
        return jsonify({"status": "expired"})

    return jsonify({"status": "waiting"})


@whot_bp.route("/matchmaking/cancel", methods=["POST"])
@csrf.exempt
@login_required
def matchmaking_cancel():
    """Cancel matchmaking — expire any waiting matchmaking games."""
    waiting_games = WhotGame.query.filter(
        WhotGame.player1_id == current_user.id,
        WhotGame.status == "negotiating",
        WhotGame.player2_id.is_(None),
        WhotGame.room_id.like("whot_match_%")
    ).all()

    for game in waiting_games:
        game.status = "cancelled"
    db.session.commit()

    return jsonify({"status": "cancelled"})


# ────────────────────────── HTTP FRIEND CHALLENGE ──────────────────────────

@whot_bp.route("/challenge/friend", methods=["POST"])
@csrf.exempt
@login_required
def challenge_friend():
    """Challenge a friend by email via HTTP. Uses DB notification instead of socket."""
    if get_setting("WHOT_ENABLED", "1") == "0":
        return jsonify({"error": "Whot is temporarily disabled by admin."}), 400

    data = request.get_json() or {}
    friend_email = (data.get("friend_email") or "").strip()

    if not friend_email:
        return jsonify({"error": "Please enter an email address to challenge."}), 400

    if friend_email.lower() == (current_user.email or "").lower():
        return jsonify({"error": "You cannot challenge yourself."}), 400

    friend = User.query.filter(db.func.lower(User.email) == db.func.lower(friend_email)).first()
    if not friend:
        return jsonify({"error": f"User with email '{friend_email}' not found."}), 404

    if current_user.balance < 500:
        return jsonify({"error": "You need at least ₦500 balance to challenge a friend."}), 400

    if friend.balance < 500:
        return jsonify({"error": f"Your friend '{friend.username}' does not have enough balance to play Whot (min ₦500)."}), 400

    # Create a negotiation room
    room_id = f"whot_challenge_{int(time.time())}_{random.randint(1000, 9999)}"

    state = {
        "p1_id": current_user.id,
        "p2_id": friend.id,
        "p1_username": current_user.username,
        "p2_username": friend.username,
        "p1_balance": current_user.balance,
        "p2_balance": friend.balance,
        "p1_proposal": None,
        "p2_proposal": None,
        "last_action": f"{current_user.username} challenged {friend.username}! Propose stake."
    }

    game = WhotGame(
        room_id=room_id,
        player1_id=current_user.id,
        player2_id=friend.id,
        is_bot_game=False,
        stake=0.0,
        pool=0.0,
        commission=0.0,
        status="negotiating",
        active_turn_id=None
    )
    game.game_state = state
    db.session.add(game)
    db.session.commit()

    # Notify the friend via DB notification (works across all processes)
    notify_user(friend.id, "⚔️ Whot Challenge!",
                f"{current_user.username} challenged you to a game of Whot! <a href='/whot/game/{room_id}'>Join Game</a>",
                "challenge")

    # Also try socket notification as a fast-path (best effort)
    try:
        from app.extensions import socketio
        socketio.emit("whot_challenge_received", {
            "room_id": room_id,
            "challenger": current_user.username
        }, room=f"whot_user_{friend.id}")
    except Exception:
        pass  # Socket delivery is best-effort; DB notification is the reliable fallback

    return jsonify({"status": "sent", "room_id": room_id, "friend_username": friend.username})


@whot_bp.route("/challenge/pending")
@login_required
def pending_challenges():
    """Check if there are any pending challenges for this user (for lobby polling)."""
    pending = WhotGame.query.filter(
        WhotGame.player2_id == current_user.id,
        WhotGame.status == "negotiating",
        WhotGame.room_id.like("whot_challenge_%")
    ).order_by(WhotGame.id.desc()).first()

    if pending:
        state = pending.game_state
        challenger_name = state.get("p1_username", "Someone")
        return jsonify({
            "has_challenge": True,
            "room_id": pending.room_id,
            "challenger": challenger_name
        })

    return jsonify({"has_challenge": False})


# ────────────────────────── HTTP BOT GAME ──────────────────────────

@whot_bp.route("/bot/start", methods=["POST"])
@csrf.exempt
@login_required
def start_bot_game():
    """Start a game against the Computer Bot via HTTP."""
    if get_setting("WHOT_ENABLED", "1") == "0":
        return jsonify({"error": "Whot is temporarily disabled by admin."}), 400

    data = request.get_json() or {}
    try:
        stake = int(data.get("stake", 500))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid stake amount."}), 400

    if stake not in [500, 1000, 2000, 5000]:
        return jsonify({"error": "Unsupported stake tier."}), 400

    if current_user.balance < stake:
        return jsonify({"error": "Insufficient wallet balance."}), 400

    room_id = f"whot_bot_{int(time.time())}_{random.randint(1000, 9999)}"

    # Debit stake
    debit_wallet(current_user, stake, "GAME", description=f"Whot Bot stake (Room {room_id})")

    # Set up deck & deal cards
    deck = whot_engine.create_deck()
    p1_hand = [deck.pop() for _ in range(5)]
    p2_hand = [deck.pop() for _ in range(5)]  # Bot hand

    discard_pile = []
    while deck:
        first_card = deck.pop()
        if first_card["value"] not in (1, 2, 5, 8, 14, 20):
            discard_pile = [first_card]
            break
        else:
            deck.insert(0, first_card)

    state = {
        "deck": deck,
        "p1_hand": p1_hand,
        "p2_hand": p2_hand,
        "discard_pile": discard_pile,
        "active_penalty_picks": 0,
        "penalty_type": None,
        "called_suit": None,
        "p1_id": current_user.id,
        "p2_id": 0,
        "last_action": f"Match started against Computer Bot! Your turn.",
        "turn_deadline": time.time() + 15.0
    }

    total_wagered = stake * 2
    commission = total_wagered * 0.05
    pool = total_wagered - commission

    game = WhotGame(
        room_id=room_id,
        player1_id=current_user.id,
        player2_id=None,
        is_bot_game=True,
        stake=float(stake),
        pool=float(pool),
        commission=float(commission),
        status="active",
        active_turn_id=current_user.id
    )
    game.game_state = state
    db.session.add(game)
    db.session.commit()

    return jsonify({"status": "started", "room_id": room_id})


# ────────────────────────── HTTP GAME ACTIONS ──────────────────────────

@whot_bp.route("/game/<string:room_id>/propose_stake", methods=["POST"])
@csrf.exempt
@login_required
def http_propose_stake(room_id):
    """Propose a stake during negotiation phase."""
    data = request.get_json() or {}
    try:
        stake = int(data.get("stake"))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid stake."}), 400

    if stake not in [500, 1000, 2000, 5000]:
        return jsonify({"error": "Unsupported stake tier."}), 400

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "negotiating":
        return jsonify({"error": "Game not found or not in negotiation."}), 400

    if current_user.balance < stake:
        return jsonify({"error": "You cannot afford this stake."}), 400

    state = game.game_state
    is_p1 = (game.player1_id == current_user.id)

    if is_p1:
        state["p1_proposal"] = stake
        state["last_action"] = f"{current_user.username} proposed a stake of ₦{stake}."
    else:
        state["p2_proposal"] = stake
        state["last_action"] = f"{current_user.username} proposed a stake of ₦{stake}."

    # Check if proposals match
    if state["p1_proposal"] == state["p2_proposal"] and state["p1_proposal"] is not None:
        final_stake = state["p1_proposal"]
        p1 = User.query.get(game.player1_id)
        p2 = User.query.get(game.player2_id)

        if not p1 or not p2 or p1.balance < final_stake or p2.balance < final_stake:
            state["last_action"] = "Stake agreement failed due to insufficient wallet balances."
            state["p1_proposal"] = None
            state["p2_proposal"] = None
            game.game_state = state
            db.session.commit()
            return jsonify({"status": "failed", "message": "Insufficient balance."})

        # Debit wallets
        debit_wallet(p1, final_stake, "GAME", description=f"Whot Stake (Room {room_id})")
        debit_wallet(p2, final_stake, "GAME", description=f"Whot Stake (Room {room_id})")

        # Deal cards
        deck = whot_engine.create_deck()
        p1_hand = [deck.pop() for _ in range(5)]
        p2_hand = [deck.pop() for _ in range(5)]

        discard_pile = []
        while deck:
            first_card = deck.pop()
            if first_card["value"] not in (1, 2, 5, 8, 14, 20):
                discard_pile = [first_card]
                break
            else:
                deck.insert(0, first_card)

        state["deck"] = deck
        state["p1_hand"] = p1_hand
        state["p2_hand"] = p2_hand
        state["discard_pile"] = discard_pile
        state["active_penalty_picks"] = 0
        state["penalty_type"] = None
        state["called_suit"] = None
        state["last_action"] = f"Agreed on ₦{final_stake} stake! Match started. {p1.username} plays first."
        state["turn_deadline"] = time.time() + 15.0

        total_wagered = final_stake * 2
        commission = total_wagered * 0.05
        pool = total_wagered - commission

        game.status = "active"
        game.stake = float(final_stake)
        game.pool = float(pool)
        game.commission = float(commission)
        game.active_turn_id = p1.id

    game.game_state = state
    db.session.commit()

    return jsonify({"status": "ok"})


@whot_bp.route("/game/<string:room_id>/leave", methods=["POST"])
@csrf.exempt
@login_required
def http_leave_negotiation(room_id):
    """Leave negotiation and cancel the game."""
    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "negotiating":
        return jsonify({"error": "Game not found or not in negotiation."}), 400

    game.status = "cancelled"
    state = game.game_state
    state["last_action"] = f"{current_user.username} left the room. Game cancelled."
    game.game_state = state
    db.session.commit()

    return jsonify({"status": "cancelled"})


@whot_bp.route("/game/<string:room_id>/play_card", methods=["POST"])
@csrf.exempt
@login_required
def http_play_card(room_id):
    """Play a card from hand."""
    data = request.get_json() or {}
    card_index = data.get("card_index")
    called_shape = data.get("called_shape")

    if card_index is None:
        return jsonify({"error": "No card selected."}), 400

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "active":
        return jsonify({"error": "Game not found or not active."}), 400

    if game.active_turn_id != current_user.id:
        return jsonify({"error": "It is not your turn!"}), 400

    state = game.game_state
    is_p1 = (game.player1_id == current_user.id)
    hand = state["p1_hand"] if is_p1 else state["p2_hand"]

    if card_index < 0 or card_index >= len(hand):
        return jsonify({"error": "Invalid card selected."}), 400

    card = hand[card_index]
    top_card = state["discard_pile"][-1]

    if not whot_engine.is_card_playable(
        card, top_card, state.get("called_suit"),
        state.get("active_penalty_picks", 0), state.get("penalty_type")
    ):
        return jsonify({"error": "That card cannot be played."}), 400

    hand.pop(card_index)
    state["discard_pile"].append(card)

    action_text = f"{current_user.username} played {card['suit']} {card['value']}."
    val = card["value"]
    next_turn_id = game.player2_id if is_p1 else game.player1_id
    if game.is_bot_game and is_p1:
        next_turn_id = 0

    if val == 1 or val == 8:
        next_turn_id = current_user.id
        action_text += " (Plays again!)"
    elif val == 2:
        state["active_penalty_picks"] = state.get("active_penalty_picks", 0) + 2
        state["penalty_type"] = 2
        action_text += f" (Pick Two stacked! Penalty: {state['active_penalty_picks']})"
    elif val == 5:
        state["active_penalty_picks"] = state.get("active_penalty_picks", 0) + 3
        state["penalty_type"] = 3
        action_text += f" (Pick Three stacked! Penalty: {state['active_penalty_picks']})"
    elif val == 14:
        opp_hand = state["p1_hand"] if not is_p1 else state["p2_hand"]
        if state["deck"]:
            opp_hand.append(state["deck"].pop())
        action_text += " (General Market - opponent draws 1 card!)"
        state["called_suit"] = None
    elif val == 20:
        if not called_shape or called_shape not in whot_engine.SUITS:
            # Put card back
            hand.insert(card_index, card)
            state["discard_pile"].pop()
            return jsonify({"error": "Select shape call!"}), 400
        state["called_suit"] = called_shape
        state["active_penalty_picks"] = 0
        state["penalty_type"] = None
        action_text += f" (Whot wildcard! Called {called_shape.capitalize()}.)"
    else:
        state["called_suit"] = None

    # Check win
    if len(hand) == 0:
        game.status = "completed"
        game.winner_id = current_user.id
        credit_wallet(current_user, game.pool, "WIN", description=f"Won Whot match (Room {room_id})")
        action_text = f"Game Over! {current_user.username} won ₦{game.pool:,.2f}!"
        notify_user(current_user.id, "Whot Victory!", f"You won ₦{game.pool:,.0f} in Whot room {room_id}!", "win")
        log_audit("WHOT_WIN", f"User {current_user.username} won Whot match {room_id} (Stake: {game.stake}, Pool: {game.pool})", current_user.id)
        state["last_action"] = action_text
        game.game_state = state
        db.session.commit()
        return jsonify({"status": "win"})

    game.active_turn_id = next_turn_id
    state["last_action"] = action_text
    state["turn_deadline"] = time.time() + 15.0
    game.game_state = state
    db.session.commit()

    return jsonify({"status": "ok"})


@whot_bp.route("/game/<string:room_id>/draw_card", methods=["POST"])
@csrf.exempt
@login_required
def http_draw_card(room_id):
    """Draw a card from the deck."""
    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "active":
        return jsonify({"error": "Game not found or not active."}), 400

    if game.active_turn_id != current_user.id:
        return jsonify({"error": "It is not your turn!"}), 400

    state = game.game_state
    is_p1 = (game.player1_id == current_user.id)
    hand = state["p1_hand"] if is_p1 else state["p2_hand"]

    verify_deck_size_http(state)

    action_text = ""
    penalty_picks = state.get("active_penalty_picks", 0)
    if penalty_picks > 0:
        drawn = []
        for _ in range(penalty_picks):
            if state["deck"]:
                drawn.append(state["deck"].pop())
        hand.extend(drawn)
        action_text = f"{current_user.username} drew {penalty_picks} penalty cards."
        state["active_penalty_picks"] = 0
        state["penalty_type"] = None
    else:
        if state["deck"]:
            hand.append(state["deck"].pop())
            action_text = f"{current_user.username} went to market (drew 1 card)."

    next_turn_id = game.player2_id if is_p1 else game.player1_id
    if game.is_bot_game and is_p1:
        next_turn_id = 0
    game.active_turn_id = next_turn_id

    state["last_action"] = action_text
    state["turn_deadline"] = time.time() + 15.0
    game.game_state = state
    db.session.commit()

    return jsonify({"status": "ok"})


@whot_bp.route("/game/<string:room_id>/forfeit", methods=["POST"])
@csrf.exempt
@login_required
def http_forfeit_game(room_id):
    """Forfeit the game — opponent wins the pool."""
    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "active":
        return jsonify({"error": "Game not found or not active."}), 400

    if current_user.id not in [game.player1_id, game.player2_id]:
        return jsonify({"error": "Unauthorized."}), 403

    game.status = "completed"
    state = game.game_state

    winner_id = game.player2_id if current_user.id == game.player1_id else game.player1_id
    if game.is_bot_game:
        winner_id = 0

    if winner_id == 0:
        game.winner_id = None
        state["last_action"] = f"Game Over! {current_user.username} forfeited the match. Computer Bot wins."
    else:
        game.winner_id = winner_id
        winner = User.query.get(winner_id)
        if winner:
            credit_wallet(winner, game.pool, "WIN", description=f"Won Whot match via forfeit (Room {room_id})")
            state["last_action"] = f"Game Over! {current_user.username} forfeited. {winner.username} wins ₦{game.pool:,.2f}!"
            notify_user(winner.id, "Whot Forfeit Victory!", f"Your opponent forfeited! You won ₦{game.pool:,.0f}!", "win")
            log_audit("WHOT_WIN_FORFEIT", f"User {winner.username} won Whot match {room_id} via forfeit (Pool: {game.pool})", winner.id)

    game.game_state = state
    db.session.commit()

    return jsonify({"status": "forfeited"})
