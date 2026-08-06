"""
WebSocket Sockets for Whot Multiplayer & AI Game.
Handles Match-First, Stake-Later Negotiation Phase and Computer Bot fallbacks.
"""
import time
import json
import random
from flask import current_app, request
from flask_login import current_user
from flask_socketio import emit, join_room, leave_room

from app.extensions import socketio, db
from app.models import WhotGame, User
from app.utils import debit_wallet, credit_wallet, get_setting, format_money, notify_user, log_audit
from app.whot import whot_engine

# Single queue for matchmaking (no stake decided yet)
MATCHMAKING_QUEUE = None


ACTIVE_USER_SIDS = {}

@socketio.on("connect")
def handle_whot_connect():
    if current_user.is_authenticated:
        ACTIVE_USER_SIDS.setdefault(current_user.id, set()).add(request.sid)


@socketio.on("disconnect")
def handle_whot_disconnect():
    if current_user.is_authenticated:
        sids = ACTIVE_USER_SIDS.get(current_user.id, set())
        sids.discard(request.sid)
        if not sids:
            ACTIVE_USER_SIDS.pop(current_user.id, None)
            handle_user_offline(current_user.id)


def handle_user_offline(user_id):
    with current_app.app_context():
        active_games = WhotGame.query.filter(
            (WhotGame.status == "active") & 
            ((WhotGame.player1_id == user_id) | (WhotGame.player2_id == user_id))
        ).all()
        for game in active_games:
            socketio.emit("game_paused", {
                "message": "Game paused. Opponent has gone offline.",
                "offline_user_id": user_id
            }, room=game.room_id)


@socketio.on("register_lobby")
def handle_register_lobby(data=None):
    if current_user.is_authenticated:
        join_room(f"whot_user_{current_user.id}")
        ACTIVE_USER_SIDS.setdefault(current_user.id, set()).add(request.sid)


@socketio.on("join_matchmaking")
def handle_join_matchmaking(data=None):
    global MATCHMAKING_QUEUE
    if not current_user.is_authenticated:
        emit("match_error", {"message": "Unauthorized user."})
        return

    # Check minimum balance to join matchmaking (at least ₦500, our lowest stake)
    if current_user.balance < 500:
        emit("match_error", {"message": "Minimum balance of ₦500 required to find matches."})
        return

    # If already waiting in queue, do nothing
    if MATCHMAKING_QUEUE == current_user.id:
        emit("waiting_match", {"message": "Already waiting in queue."})
        return

    if MATCHMAKING_QUEUE:
        # Match found!
        opponent_id = MATCHMAKING_QUEUE
        MATCHMAKING_QUEUE = None  # Clear queue
        
        opponent = db.session.get(User, opponent_id)
        if not opponent or opponent.balance < 500:
            # Opponent is invalid or ran out of money, put current user in queue instead
            MATCHMAKING_QUEUE = current_user.id
            emit("waiting_match", {"message": "Waiting for an opponent..."})
            return

        # Initialize game in 'negotiating' status
        room_id = f"whot_{int(time.time())}_{random.randint(1000, 9999)}"
        
        # Initial negotiation state
        state = {
            "p1_id": current_user.id,
            "p2_id": opponent.id,
            "p1_username": current_user.username,
            "p2_username": opponent.username,
            "p1_balance": current_user.balance,
            "p2_balance": opponent.balance,
            "p1_proposal": None,
            "p2_proposal": None,
            "last_action": "Match found! Propose a stake to play."
        }

        game = WhotGame(
            room_id=room_id,
            player1_id=current_user.id,
            player2_id=opponent.id,
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

        # Emit redirects
        socketio.emit("match_found", {"room_id": room_id}, room=f"whot_user_{current_user.id}")
        socketio.emit("match_found", {"room_id": room_id}, room=f"whot_user_{opponent.id}")
    else:
        # Set waiting state
        MATCHMAKING_QUEUE = current_user.id
        emit("waiting_match", {"message": "Waiting for an opponent..."})


@socketio.on("cancel_matchmaking")
def handle_cancel_matchmaking(data=None):
    global MATCHMAKING_QUEUE
    if not current_user.is_authenticated:
        return
    if MATCHMAKING_QUEUE == current_user.id:
        MATCHMAKING_QUEUE = None
    emit("matchmaking_cancelled")


@socketio.on("send_friend_challenge")
def handle_send_friend_challenge(data=None):
    data = data or {}
    if not current_user.is_authenticated:
        emit("match_error", {"message": "Unauthorized user."})
        return

    friend_email = data.get("friend_email", "").strip()
    if not friend_email:
        emit("match_error", {"message": "Please enter an email address to challenge."})
        return

    if friend_email.lower() == (current_user.email or "").lower():
        emit("match_error", {"message": "You cannot challenge yourself."})
        return

    friend = User.query.filter(db.func.lower(User.email) == db.func.lower(friend_email)).first()
    if not friend:
        emit("match_error", {"message": f"User with email '{friend_email}' not found."})
        return

    # Check minimum balance
    if current_user.balance < 500:
        emit("match_error", {"message": "You need at least ₦500 balance to challenge a friend."})
        return

    if friend.balance < 500:
        emit("match_error", {"message": f"Your friend '{friend.username}' does not have enough balance to play Whot (min ₦500)."})
        return

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

    # Redirect the challenger to the game board immediately
    emit("match_found", {"room_id": room_id})

    # Alert/notify the friend
    socketio.emit("whot_challenge_received", {
        "room_id": room_id,
        "challenger": current_user.username
    }, room=f"whot_user_{friend.id}")


@socketio.on("decline_friend_challenge")
def handle_decline_friend_challenge(data=None):
    data = data or {}
    room_id = data.get("room_id")
    if not room_id:
        return

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "negotiating":
        return

    game.status = "cancelled"
    state = game.game_state
    state["last_action"] = f"{current_user.username} declined the challenge."
    game.game_state = state
    db.session.commit()

    # Inform the challenger
    socketio.emit("opponent_left", {
        "message": f"{current_user.username} declined your challenge."
    }, room=game.room_id)


@socketio.on("join_bot_game")
def handle_join_bot_game(data=None):
    data = data or {}
    global MATCHMAKING_QUEUE
    if not current_user.is_authenticated:
        emit("match_error", {"message": "Unauthorized user."})
        return

    try:
        stake = int(data.get("stake", 500))
    except (ValueError, TypeError):
        emit("match_error", {"message": "Invalid stake amount."})
        return

    if stake not in [500, 1000, 2000, 5000]:
        emit("match_error", {"message": "Unsupported stake tier."})
        return

    if current_user.balance < stake:
        emit("match_error", {"message": "Insufficient wallet balance."})
        return

    # Clear queue if they were waiting
    if MATCHMAKING_QUEUE == current_user.id:
        MATCHMAKING_QUEUE = None

    room_id = f"whot_bot_{int(time.time())}_{random.randint(1000, 9999)}"
    
    # Debit stake
    debit_wallet(current_user, stake, "GAME", description=f"Whot Bot stake (Room {room_id})")

    # Set up deck & deal cards
    deck = whot_engine.create_deck()
    p1_hand = [deck.pop() for _ in range(5)]
    p2_hand = [deck.pop() for _ in range(5)] # Bot hand
    
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
        "last_action": f"Match started against Computer Bot! Your turn."
    }

    total_wagered = stake * 2
    commission = total_wagered * 0.10
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

    emit("match_found", {"room_id": room_id})
    trigger_turn_start(current_app._get_current_object(), game)


@socketio.on("join_whot_room")
def handle_join_whot_room(data=None):
    data = data or {}
    room_id = data.get("room_id")
    if not room_id:
        return

    join_room(room_id)
    if current_user.is_authenticated:
        join_room(f"whot_user_{current_user.id}")
        ACTIVE_USER_SIDS.setdefault(current_user.id, set()).add(request.sid)

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if game:
        if game.status == "active":
            is_p1_online = game.player1_id in ACTIVE_USER_SIDS
            is_p2_online = game.is_bot_game or (game.player2_id in ACTIVE_USER_SIDS)
            
            if is_p1_online and is_p2_online:
                # Both are online: resume game!
                socketio.emit("game_resumed", {
                    "message": "Both players are online. Game resumed!"
                }, room=game.room_id)
                # Restart/Start turn timer!
                trigger_turn_start(current_app._get_current_object(), game)
            else:
                # Pause notice
                socketio.emit("game_paused", {
                    "message": "Game paused. Waiting for opponent to connect...",
                    "offline_user_id": game.player2_id if is_p1_online else game.player1_id
                }, room=f"whot_user_{current_user.id}")

        emit_game_state(game)


@socketio.on("propose_stake")
def handle_propose_stake(data=None):
    data = data or {}
    room_id = data.get("room_id")
    try:
        stake = int(data.get("stake"))
    except (ValueError, TypeError):
        return

    if stake not in [500, 1000, 2000, 5000]:
        return

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "negotiating":
        return

    # Check user has balance
    if current_user.balance < stake:
        emit("game_error", {"message": "You cannot afford this stake."})
        return

    state = game.game_state
    is_p1 = (game.player1_id == current_user.id)

    # Save proposal
    if is_p1:
        state["p1_proposal"] = stake
        state["last_action"] = f"{current_user.username} proposed a stake of ₦{stake}."
    else:
        state["p2_proposal"] = stake
        state["last_action"] = f"{current_user.username} proposed a stake of ₦{stake}."

    # Check if proposals match
    if state["p1_proposal"] == state["p2_proposal"] and state["p1_proposal"] is not None:
        # Stake agreed! Let's lock it in
        final_stake = state["p1_proposal"]
        p1 = game.player1
        p2 = game.player2

        # Final balance verification
        if p1.balance < final_stake or p2.balance < final_stake:
            state["last_action"] = "Stake agreement failed due to insufficient wallet balances."
            state["p1_proposal"] = None
            state["p2_proposal"] = None
            game.game_state = state
            db.session.commit()
            emit_game_state(game)
            return

        # Debit wallets
        debit_wallet(p1, final_stake, "GAME", description=f"Whot Stake (Room {room_id})")
        debit_wallet(p2, final_stake, "GAME", description=f"Whot Stake (Room {room_id})")

        # Deal cards & start discard pile
        deck = whot_engine.create_deck()
        p1_hand = [deck.pop() for _ in range(5)]
        p2_hand = [deck.pop() for _ in range(5)]
        
        while deck:
            first_card = deck.pop()
            if first_card["value"] not in (1, 2, 5, 8, 14, 20):
                discard_pile = [first_card]
                break
            else:
                deck.insert(0, first_card)

        # Update game state details
        state["deck"] = deck
        state["p1_hand"] = p1_hand
        state["p2_hand"] = p2_hand
        state["discard_pile"] = discard_pile
        state["active_penalty_picks"] = 0
        state["penalty_type"] = None
        state["called_suit"] = None
        state["last_action"] = f"Agreed on ₦{final_stake} stake! Match started. {p1.username} plays first."

        # Setup WhotGame totals
        total_wagered = final_stake * 2
        commission = total_wagered * 0.10
        pool = total_wagered - commission

        game.status = "active"
        game.stake = float(final_stake)
        game.pool = float(pool)
        game.commission = float(commission)
        game.active_turn_id = p1.id
        
    game.game_state = state
    db.session.commit()

    emit_game_state(game)
    if game.status == "active":
        trigger_turn_start(current_app._get_current_object(), game)


@socketio.on("leave_negotiation")
def handle_leave_negotiation(data=None):
    data = data or {}
    room_id = data.get("room_id")
    if not room_id:
        return

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "negotiating":
        return

    # Cancel the game
    game.status = "cancelled"
    state = game.game_state
    state["last_action"] = f"{current_user.username} left the room. Game cancelled."
    game.game_state = state
    db.session.commit()

    # Broadcast update and trigger exit redirect
    socketio.emit("opponent_left", {"message": f"{current_user.username} cancelled the game."}, room=room_id)


@socketio.on("play_card")
def handle_play_card(data=None):
    data = data or {}
    room_id = data.get("room_id")
    card_index = data.get("card_index")
    called_shape = data.get("called_shape")

    if card_index is None or not room_id:
        return

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "active":
        return

    if game.active_turn_id != current_user.id:
        emit("game_error", {"message": "It is not your turn!"})
        return

    state = game.game_state
    is_p1 = (game.player1_id == current_user.id)
    hand = state["p1_hand"] if is_p1 else state["p2_hand"]

    if card_index < 0 or card_index >= len(hand):
        emit("game_error", {"message": "Invalid card selected."})
        return

    card = hand[card_index]
    top_card = state["discard_pile"][-1]

    if not whot_engine.is_card_playable(
        card, top_card, state.get("called_suit"),
        state.get("active_penalty_picks", 0), state.get("penalty_type")
    ):
        emit("game_error", {"message": "That card cannot be played."})
        return

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
        opp_is_p1 = not is_p1
        opp_hand = state["p1_hand"] if opp_is_p1 else state["p2_hand"]
        if state["deck"]:
            opp_hand.append(state["deck"].pop())
        action_text += " (General Market - opponent draws 1 card!)"
        state["called_suit"] = None
    elif val == 20:
        if not called_shape or called_shape not in whot_engine.SUITS:
            emit("game_error", {"message": "Select shape call!"})
            return
        state["called_suit"] = called_shape
        state["active_penalty_picks"] = 0
        state["penalty_type"] = None
        action_text += f" (Whot wildcard! Called {called_shape.capitalize()}.)"
    else:
        state["called_suit"] = None

    if len(hand) == 0:
        handle_game_win(game, current_user.id)
        return

    game.active_turn_id = next_turn_id
    state["last_action"] = action_text
    game.game_state = state
    db.session.commit()

    emit_game_state(game)

    if game.status == "active":
        trigger_turn_start(current_app._get_current_object(), game)


@socketio.on("draw_card")
def handle_draw_card(data=None):
    data = data or {}
    room_id = data.get("room_id")
    if not room_id:
        return

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "active":
        return

    if game.active_turn_id != current_user.id:
        emit("game_error", {"message": "It is not your turn!"})
        return

    state = game.game_state
    is_p1 = (game.player1_id == current_user.id)
    hand = state["p1_hand"] if is_p1 else state["p2_hand"]

    verify_deck_size(state)

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
        
        next_turn_id = game.player2_id if is_p1 else game.player1_id
        if game.is_bot_game and is_p1:
            next_turn_id = 0
        game.active_turn_id = next_turn_id
    else:
        if state["deck"]:
            card = state["deck"].pop()
            hand.append(card)
            action_text = f"{current_user.username} went to market (drew 1 card)."
            
            next_turn_id = game.player2_id if is_p1 else game.player1_id
            if game.is_bot_game and is_p1:
                next_turn_id = 0
            game.active_turn_id = next_turn_id

    state["last_action"] = action_text
    game.game_state = state
    db.session.commit()

    emit_game_state(game)

    if game.status == "active":
        trigger_turn_start(current_app._get_current_object(), game)


def trigger_turn_start(app, game):
    if game.status != "active":
        return
    state = game.game_state
    deadline = time.time() + 15.0
    state["turn_deadline"] = deadline
    game.game_state = state
    db.session.commit()
    
    # Start the timeout task
    socketio.start_background_task(run_turn_timeout, app, game.room_id, game.active_turn_id, deadline)

    # If next player is bot, trigger bot play immediately!
    if game.is_bot_game and game.active_turn_id == 0:
        socketio.start_background_task(run_bot_turn, app, game.room_id)


def run_turn_timeout(app, room_id, turn_user_id, deadline):
    # Wait for the turn time (15 seconds)
    time.sleep(15.5)
    with app.app_context():
        game = WhotGame.query.filter_by(room_id=room_id).first()
        if not game or game.status != "active" or game.active_turn_id != turn_user_id:
            # Game has moved on or ended
            return

        state = game.game_state
        # Validate that this is still the active turn timer
        if abs(state.get("turn_deadline", 0) - deadline) > 0.5:
            return

        # Check if anyone went offline. If so, do NOT timeout, game is paused
        is_p1_online = game.player1_id in ACTIVE_USER_SIDS
        is_p2_online = game.is_bot_game or (game.player2_id in ACTIVE_USER_SIDS)
        if not is_p1_online or not is_p2_online:
            # Game is paused. Exit this timeout.
            # A new timer will start when they reconnect
            return

        # Turn timeout! Perform automatic draw
        is_p1_turn = (game.active_turn_id == game.player1_id)
        active_hand = state["p1_hand"] if is_p1_turn else state["p2_hand"]
        verify_deck_size(state)
        
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
        game.game_state = state
        db.session.commit()
        
        emit_game_state(game)
        
        # Start turn timer timeout countdown loop / bot play for the next player!
        trigger_turn_start(app, game)


def run_bot_turn(app, room_id):
    time.sleep(1.5)
    with app.app_context():
        game = WhotGame.query.filter_by(room_id=room_id).first()
        if not game or game.status != "active" or game.active_turn_id != 0:
            return

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
                handle_game_win(game, 0)
                return
        else:
            verify_deck_size(state)
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
        game.game_state = state
        db.session.commit()

        emit_game_state(game)
        
        # Start turn timer timeout countdown loop for the player!
        trigger_turn_start(app, game)


def handle_game_win(game, winner_id):
    game.status = "completed"
    room_id = game.room_id
    state = game.game_state

    if winner_id == 0:
        game.winner_id = None
        state["last_action"] = "Game Over! Computer Bot won the match."
    else:
        game.winner_id = winner_id
        winner = db.session.get(User, winner_id)
        if winner:
            credit_wallet(winner, game.pool, "WIN", description=f"Won Whot match (Room {room_id})")
            state["last_action"] = f"Game Over! {winner.username} won ₦{game.pool:,.2f}!"
            notify_user(winner.id, "Whot Victory!", f"You won ₦{game.pool:,.0f} in Whot room {room_id}!", "win")
            log_audit("WHOT_WIN", f"User {winner.username} won Whot match {room_id} (Stake: {game.stake}, Pool: {game.pool})", winner.id)

    game.game_state = state
    db.session.commit()
    emit_game_state(game)


def verify_deck_size(state):
    if len(state["deck"]) < 5:
        top_card = state["discard_pile"].pop()
        recycled = state["discard_pile"]
        random.shuffle(recycled)
        state["deck"].extend(recycled)
        state["discard_pile"] = [top_card]


def emit_game_state(game):
    state = game.game_state
    
    # Check if game is in negotiating phase
    if game.status == "negotiating":
        payload = {
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
        }
        socketio.emit("whot_negotiate_state", payload, room=game.room_id)
        return

    # Dealt states
    p1_card_count = len(state["p1_hand"])
    p2_card_count = len(state["p2_hand"])

    payload_p1 = {
        "status": game.status,
        "stake": game.stake,
        "pool": game.pool,
        "active_turn_id": game.active_turn_id,
        "my_hand": state["p1_hand"],
        "opp_card_count": p2_card_count,
        "top_card": state["discard_pile"][-1],
        "called_suit": state.get("called_suit"),
        "active_penalty_picks": state.get("active_penalty_picks", 0),
        "penalty_type": state.get("penalty_type"),
        "last_action": state.get("last_action"),
        "draw_deck_count": len(state["deck"]),
        "winner_id": game.winner_id,
        "is_bot_game": game.is_bot_game
    }

    payload_p2 = {
        "status": game.status,
        "stake": game.stake,
        "pool": game.pool,
        "active_turn_id": game.active_turn_id,
        "my_hand": state["p2_hand"],
        "opp_card_count": p1_card_count,
        "top_card": state["discard_pile"][-1],
        "called_suit": state.get("called_suit"),
        "active_penalty_picks": state.get("active_penalty_picks", 0),
        "penalty_type": state.get("penalty_type"),
        "last_action": state.get("last_action"),
        "draw_deck_count": len(state["deck"]),
        "winner_id": game.winner_id,
        "is_bot_game": game.is_bot_game
    }

    socketio.emit("whot_state", payload_p1, room=f"whot_user_{game.player1_id}")
    if game.is_bot_game:
        socketio.emit("whot_state", payload_p1, room=game.room_id)
    else:
        socketio.emit("whot_state", payload_p2, room=f"whot_user_{game.player2_id}")


@socketio.on("forfeit_game")
def handle_forfeit_game(data=None):
    data = data or {}
    room_id = data.get("room_id")
    if not room_id:
        return

    game = WhotGame.query.filter_by(room_id=room_id).first()
    if not game or game.status != "active":
        return

    if current_user.id not in [game.player1_id, game.player2_id]:
        return

    winner_id = game.player2_id if current_user.id == game.player1_id else game.player1_id
    if game.is_bot_game:
        winner_id = 0

    handle_game_forfeit(game, current_user.id, winner_id)


def handle_game_forfeit(game, forfeiter_id, winner_id):
    game.status = "completed"
    room_id = game.room_id
    state = game.game_state

    forfeiter = db.session.get(User, forfeiter_id)
    forfeiter_name = forfeiter.username if forfeiter else "Unknown Player"

    if winner_id == 0:
        game.winner_id = None
        state["last_action"] = f"Game Over! {forfeiter_name} forfeited the match. Computer Bot wins."
    else:
        game.winner_id = winner_id
        winner = db.session.get(User, winner_id)
        if winner:
            credit_wallet(winner, game.pool, "WIN", description=f"Won Whot match via forfeit (Room {room_id})")
            state["last_action"] = f"Game Over! {forfeiter_name} forfeited. {winner.username} wins ₦{game.pool:,.2f}!"
            notify_user(winner.id, "Whot Forfeit Victory!", f"Your opponent forfeited! You won ₦{game.pool:,.0f}!", "win")
            log_audit("WHOT_WIN_FORFEIT", f"User {winner.username} won Whot match {room_id} via forfeit (Pool: {game.pool})", winner.id)

    game.game_state = state
    db.session.commit()
    emit_game_state(game)
