"""
Games blueprint — Spin Wheel, Coin Flip, Scratch Card.
"""
import json
import secrets
from datetime import datetime, date

from flask import Blueprint, config, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app.extensions import db, csrf
from app.models import GamePlay
from app.utils import (
    credit_wallet, debit_wallet, notify_user, log_audit,
    check_daily_bet_limit, format_money, get_setting, naira,
)

games_bp = Blueprint("games", __name__, url_prefix="/games")

def check_game_cooldown(user_id, seconds=3):
    """Block if user played any game within the last N seconds."""
    from datetime import datetime, timedelta
    last_play = GamePlay.query.filter_by(user_id=user_id).order_by(
        GamePlay.created_at.desc()
    ).first()
    if last_play and (datetime.utcnow() - last_play.created_at).total_seconds() < seconds:
        return False
    return True

# ─────────────── Wheel Segments Config ───────────────
WHEEL_SEGMENTS = [
    {"label": "💔 Lose",  "multiplier": 0,    "color": "#ef4444"},
    {"label": "0.5x",     "multiplier": 0.5,  "color": "#8b5cf6"},
    {"label": "1x",       "multiplier": 1.0,  "color": "#3b82f6"},
    {"label": "💔 Lose",  "multiplier": 0,    "color": "#ef4444"},
    {"label": "1.5x",     "multiplier": 1.5,  "color": "#22c55e"},
    {"label": "2x",       "multiplier": 2.0,  "color": "#f59e0b"},
    {"label": "💔 Lose",  "multiplier": 0,    "color": "#ef4444"},
    {"label": "3x",       "multiplier": 3.0,  "color": "#ec4899"},
    {"label": "0.5x",     "multiplier": 0.5,  "color": "#6366f1"},
    {"label": "5x",       "multiplier": 5.0,  "color": "#14b8a6"},
    {"label": "💔 Lose",  "multiplier": 0,    "color": "#ef4444"},
    {"label": "10x 🎉",   "multiplier": 10.0, "color": "#fbbf24"},
]

# Probabilities (must match segment count, sum to 1.0)
WHEEL_PROBABILITIES = [0.22, 0.12, 0.14, 0.18, 0.10, 0.08, 0.06, 0.04, 0.03, 0.02, 0.005, 0.005]


# ─────────────── Scratch Card Symbols ───────────────
SCRATCH_SYMBOLS = [
    {"symbol": "💎", "multiplier": 5.0, "name": "Diamond"},
    {"symbol": "🌟", "multiplier": 3.0, "name": "Star"},
    {"symbol": "🎯", "multiplier": 2.0, "name": "Target"},
    {"symbol": "💰", "multiplier": 1.5, "name": "Money"},
    {"symbol": "🍀", "multiplier": 1.0, "name": "Clover"},
    {"symbol": "💔", "multiplier": 0,   "name": "Lose"},
]


# ────────────────────────── GAME HUB ──────────────────────────
@games_bp.route("/")
@login_required
def hub():
    return render_template("games/hub.html")


# ════════════════════════════════════════════════════════════════
#  SPIN THE WHEEL
# ════════════════════════════════════════════════════════════════
@games_bp.route("/wheel")
@login_required
def wheel():
    return render_template("games/wheel.html", segments=WHEEL_SEGMENTS)


@csrf.exempt
@games_bp.route("/wheel/spin", methods=["POST"])
@login_required
def wheel_spin():
    user = current_user

    # Self-exclusion check
    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    try:
        bet_amount = float(data.get("bet", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid bet amount."}), 400

    if bet_amount < 50:
        return jsonify({"error": "Minimum bet is ₦50."}), 400
    if bet_amount > 50000:
        return jsonify({"error": "Maximum bet is ₦50,000."}), 400

    if not check_daily_bet_limit(user, bet_amount):
        return jsonify({"error": "You've reached your daily betting limit of ₦50,000. Come back tomorrow!"}), 400
    flash(f"You've hit your daily betting limit of {format_money(get_setting('MAX_DAILY_BET', 50000))}. Come back tomorrow!", "warning")

    if user.balance < bet_amount:
        return jsonify({"error": "Insufficient balance."}), 400
    
    if not check_game_cooldown(user.id):
        return jsonify({"error": "Please wait a few seconds between plays."}), 429

    # Debit the bet
    debit_wallet(user, bet_amount, "BET", description=f"Wheel spin bet {format_money(bet_amount)}")

    # Track daily bet
    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
    user.daily_bet_total += bet_amount

    # Determine result using weighted random
    rand = secrets.randbelow(100000) / 100000.0
    cumulative = 0.0
    segment_index = 0
    for i, prob in enumerate(WHEEL_PROBABILITIES):
        cumulative += prob
        if rand < cumulative:
            segment_index = i
            break

    segment = WHEEL_SEGMENTS[segment_index]
    payout = bet_amount * segment["multiplier"]
    result = "WIN" if payout > 0 else "LOSS"

    if payout > 0:
        credit_wallet(user, payout, "WIN", description=f"Wheel win {segment['label']} — {format_money(payout)}")

    # Record game play
    gp = GamePlay(
        user_id=user.id, game_type="wheel",
        bet_amount=bet_amount, payout=payout, result=result,
        result_data=json.dumps({"segment": segment_index, "label": segment["label"], "multiplier": segment["multiplier"]}),
    )
    db.session.add(gp)
    db.session.commit()

    if payout >= bet_amount * 5:
        notify_user(user.id, "Big Wheel Win!", f"You won {format_money(payout)} on the Lucky Wheel!", "info")

    return jsonify({
        "segment": segment_index,
        "label": segment["label"],
        "multiplier": segment["multiplier"],
        "payout": payout,
        "result": result,
        "new_balance": user.balance,
    })


# ════════════════════════════════════════════════════════════════
#  COIN FLIP
# ════════════════════════════════════════════════════════════════
@games_bp.route("/coinflip")
@login_required
def coinflip():
    return render_template("games/coinflip.html")


@csrf.exempt
@games_bp.route("/coinflip/flip", methods=["POST"])
@login_required
def coinflip_flip():
    user = current_user

    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    choice = data.get("choice", "").lower()
    try:
        bet_amount = float(data.get("bet", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid bet amount."}), 400

    if choice not in ("heads", "tails"):
        return jsonify({"error": "Pick heads or tails."}), 400
    if bet_amount < 50:
        return jsonify({"error": "Minimum bet is ₦50."}), 400
    if bet_amount > 50000:
        return jsonify({"error": "Maximum bet is ₦50,000."}), 400

    if not check_daily_bet_limit(user, bet_amount):
        return jsonify({"error": "You've reached your daily betting limit of ₦50,000. Come back tomorrow!"}), 400
    flash(f"You've hit your daily betting limit of {format_money(get_setting('MAX_DAILY_BET', 50000))}. Come back tomorrow!", "warning")
    
    if user.balance < bet_amount:
        return jsonify({"error": "Insufficient balance."}), 400
    
    if not check_game_cooldown(user.id):
        return jsonify({"error": "Please wait a few seconds between plays."}), 429

    debit_wallet(user, bet_amount, "BET", description=f"Coinflip bet {format_money(bet_amount)}")

    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
    user.daily_bet_total += bet_amount

    # Flip (fair coin, house edge via 1.9x payout instead of 2x)
    coin_result = "heads" if secrets.randbelow(2) == 0 else "tails"
    payout_multiplier = float(get_setting("COINFLIP_PAYOUT", 1.9))

    won = choice == coin_result
    payout = bet_amount * payout_multiplier if won else 0
    result = "WIN" if won else "LOSS"

    if payout > 0:
        credit_wallet(user, payout, "WIN", description=f"Coinflip win — {format_money(payout)}")

    gp = GamePlay(
        user_id=user.id, game_type="coinflip",
        bet_amount=bet_amount, payout=payout, result=result,
        result_data=json.dumps({"choice": choice, "coin": coin_result, "multiplier": payout_multiplier}),
    )
    db.session.add(gp)
    db.session.commit()

    return jsonify({
        "coin": coin_result,
        "choice": choice,
        "won": won,
        "payout": payout,
        "result": result,
        "new_balance": user.balance,
    })


# ════════════════════════════════════════════════════════════════
#  SCRATCH CARD
# ════════════════════════════════════════════════════════════════
@games_bp.route("/scratchcard")
@login_required
def scratchcard():
    # Check if user has a free daily card
    today_start = datetime.combine(date.today(), datetime.min.time())
    free_used = GamePlay.query.filter(
        GamePlay.user_id == current_user.id,
        GamePlay.game_type == "scratchcard",
        GamePlay.bet_amount == 0,
        GamePlay.created_at >= today_start,
    ).first()
    return render_template("games/scratchcard.html", free_available=not free_used)


@csrf.exempt
@games_bp.route("/scratchcard/buy", methods=["POST"])
@login_required
def scratchcard_buy():
    user = current_user

    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    tier = data.get("tier", "free")

    tiers = {"free": 0, "bronze": 100, "silver": 500, "gold": 1000}
    if tier not in tiers:
        return jsonify({"error": "Invalid card tier."}), 400

    cost = tiers[tier]

    # Free card: one per day
    if tier == "free":
        today_start = datetime.combine(date.today(), datetime.min.time())
        free_used = GamePlay.query.filter(
            GamePlay.user_id == user.id,
            GamePlay.game_type == "scratchcard",
            GamePlay.bet_amount == 0,
            GamePlay.created_at >= today_start,
        ).first()
        if free_used:
            return jsonify({"error": "Free daily card already used. Try again tomorrow!"}), 400
    else:
        if not check_daily_bet_limit(user, cost):
            return jsonify({"error": "You've reached your daily betting limit of ₦50,000. Come back tomorrow!"}), 400
        flash(f"You've hit your daily betting limit of {format_money(get_setting('MAX_DAILY_BET', 50000))}. Come back tomorrow!", "warning")
        if user.balance < cost:
            return jsonify({"error": "Insufficient balance."}), 400
        
        if not check_game_cooldown(user.id):
            return jsonify({"error": "Please wait a few seconds between plays."}), 429

        debit_wallet(user, cost, "BET", description=f"Scratch card ({tier}) — {format_money(cost)}")

        today = date.today()
        if user.daily_bet_date != today:
            user.daily_bet_total = 0.0
            user.daily_bet_date = today
        user.daily_bet_total += cost

    # Generate 9 cells (3x3 grid)
    # Backend decides: win or lose, then fills grid
    win_chance = float(get_setting("SCRATCH_WIN_CHANCE", 0.30))
    is_winner = secrets.randbelow(100) < int(win_chance * 100)

    cells = []
    if is_winner:
        # Pick a winning symbol (weighted toward lower multipliers)
        win_weights = [5, 10, 20, 30, 25, 0]  # Diamond rare, Clover common
        total_w = sum(win_weights)
        r = secrets.randbelow(total_w)
        cum = 0
        win_sym_idx = 4  # default Clover
        for i, w in enumerate(win_weights):
            cum += w
            if r < cum:
                win_sym_idx = i
                break

        win_symbol = SCRATCH_SYMBOLS[win_sym_idx]
        # Place 3 matching symbols randomly
        positions = list(range(9))
        secrets.SystemRandom().shuffle(positions)
        win_positions = positions[:3]

        for i in range(9):
            if i in win_positions:
                cells.append({"symbol": win_symbol["symbol"], "name": win_symbol["name"], "multiplier": win_symbol["multiplier"]})
            else:
                # Random non-winning symbol
                other_idx = secrets.randbelow(len(SCRATCH_SYMBOLS))
                while SCRATCH_SYMBOLS[other_idx]["symbol"] == win_symbol["symbol"]:
                    other_idx = secrets.randbelow(len(SCRATCH_SYMBOLS))
                s = SCRATCH_SYMBOLS[other_idx]
                cells.append({"symbol": s["symbol"], "name": s["name"], "multiplier": s["multiplier"]})

        payout = cost * win_symbol["multiplier"] if cost > 0 else win_symbol["multiplier"] * 100
        result = "WIN"
    else:
        # No three matching — fill randomly ensuring no 3-match
        for i in range(9):
            s = SCRATCH_SYMBOLS[secrets.randbelow(len(SCRATCH_SYMBOLS))]
            cells.append({"symbol": s["symbol"], "name": s["name"], "multiplier": s["multiplier"]})
        # Verify no 3 match
        from collections import Counter
        sym_counts = Counter(c["symbol"] for c in cells)
        while any(v >= 3 for v in sym_counts.values()):
            cells = []
            for i in range(9):
                s = SCRATCH_SYMBOLS[secrets.randbelow(len(SCRATCH_SYMBOLS))]
                cells.append({"symbol": s["symbol"], "name": s["name"], "multiplier": s["multiplier"]})
            sym_counts = Counter(c["symbol"] for c in cells)
        payout = 0
        result = "LOSS"

    if payout > 0:
        credit_wallet(user, payout, "WIN", description=f"Scratch card win — {format_money(payout)}")

    gp = GamePlay(
        user_id=user.id, game_type="scratchcard",
        bet_amount=cost, payout=payout, result=result,
        result_data=json.dumps({"tier": tier, "cells": [c["symbol"] for c in cells], "win_symbol": cells[0]["symbol"] if is_winner else None}),
    )
    db.session.add(gp)
    db.session.commit()

    if payout >= 1000:
        notify_user(user.id, "Scratch Card Win!", f"You won {format_money(payout)} on a scratch card!", "info")

    return jsonify({
        "cells": cells,
        "payout": payout,
        "result": result,
        "new_balance": user.balance,
        "tier": tier,
    })


# ════════════════════════════════════════════════════════════════
#  LOTTO 5/90
# ════════════════════════════════════════════════════════════════
@games_bp.route("/lotto590")
@login_required
def lotto590():
    enabled = str(get_setting("LOTTO590_ENABLED", "1")) == "1"
    if not enabled:
        flash("Lotto 5/90 is currently unavailable.", "info")
        return redirect(url_for("games.hub"))

    min_bet = float(get_setting("LOTTO590_MIN_BET", 50))
    max_bet = float(get_setting("LOTTO590_MAX_BET", 50000))
    payouts = {
        "nap2": float(get_setting("LOTTO590_NAP2_PAYOUT", 240)),
        "nap3": float(get_setting("LOTTO590_NAP3_PAYOUT", 2100)),
    }

    return render_template(
        "games/lotto590.html",
        min_bet=min_bet,
        max_bet=max_bet,
        payouts=payouts,
    )


@csrf.exempt
@games_bp.route("/lotto590/play", methods=["POST"])
@login_required
def lotto590_play():
    import math
    user = current_user

    enabled = str(get_setting("LOTTO590_ENABLED", "1")) == "1"
    if not enabled:
        return jsonify({"error": "Lotto 5/90 is currently unavailable."}), 503

    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    try:
        bet_amount = float(data.get("bet", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid bet amount."}), 400

    min_bet = float(get_setting("LOTTO590_MIN_BET", 50))
    max_bet = float(get_setting("LOTTO590_MAX_BET", 50000))

    if bet_amount < min_bet:
        return jsonify({"error": f"Minimum bet is {format_money(min_bet)}."}), 400
    if bet_amount > max_bet:
        return jsonify({"error": f"Maximum bet is {format_money(max_bet)}."}), 400

    if not check_daily_bet_limit(user, bet_amount):
        return jsonify({"error": "Daily betting limit reached."}), 400

    if user.balance < bet_amount:
        return jsonify({"error": "Insufficient balance."}), 400

    if not check_game_cooldown(user.id):
        return jsonify({"error": "Please wait a few seconds between plays."}), 429

    play_type = data.get("play_type", "").lower()
    if play_type not in ("nap2", "nap3", "perm2"):
        return jsonify({"error": "Invalid play type."}), 400

    picks = data.get("picks", [])
    if not isinstance(picks, list):
        return jsonify({"error": "Picks must be a list of numbers."}), 400

    try:
        picks = list(set(int(x) for x in picks))
    except (ValueError, TypeError):
        return jsonify({"error": "Picks must be valid integers."}), 400

    if any(x < 1 or x > 90 for x in picks):
        return jsonify({"error": "Numbers must be between 1 and 90."}), 400

    if play_type == "nap2" and len(picks) != 2:
        return jsonify({"error": "Nap 2 requires exactly 2 numbers."}), 400
    elif play_type == "nap3" and len(picks) != 3:
        return jsonify({"error": "Nap 3 requires exactly 3 numbers."}), 400
    elif play_type == "perm2" and not (3 <= len(picks) <= 10):
        return jsonify({"error": "Perm 2 requires between 3 and 10 numbers."}), 400

    # Draw 5 numbers securely
    drawn = sorted(secrets.SystemRandom().sample(range(1, 91), 5))

    # Debit wallet
    debit_wallet(user, bet_amount, "BET", description=f"Lotto 5/90 {play_type.upper()} bet — {format_money(bet_amount)}")

    # Track daily bet
    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
    user.daily_bet_total += bet_amount

    # Payout calculation
    nap2_mult = float(get_setting("LOTTO590_NAP2_PAYOUT", 240))
    nap3_mult = float(get_setting("LOTTO590_NAP3_PAYOUT", 2100))
    payout = 0.0

    matched = [x for x in picks if x in drawn]

    if play_type == "nap2":
        if len(matched) == 2:
            payout = bet_amount * nap2_mult
    elif play_type == "nap3":
        if len(matched) == 3:
            payout = bet_amount * nap3_mult
    elif play_type == "perm2":
        # Total pairs from picks
        total_combos = math.comb(len(picks), 2)
        stake_per_combo = bet_amount / total_combos
        # Winning pairs from matches
        winning_combos = math.comb(len(matched), 2) if len(matched) >= 2 else 0
        payout = winning_combos * stake_per_combo * nap2_mult

    payout = round(payout, 2)
    result = "WIN" if payout > 0 else "LOSS"

    if payout > 0:
        credit_wallet(user, payout, "WIN", description=f"Lotto 5/90 {play_type.upper()} win — {format_money(payout)}")

    # Record gameplay
    gp = GamePlay(
        user_id=user.id,
        game_type="lotto590",
        bet_amount=bet_amount,
        payout=payout,
        result=result,
        result_data=json.dumps({
            "play_type": play_type,
            "picks": picks,
            "drawn": drawn,
            "matched": matched,
            "payout_multiplier": nap2_mult if play_type in ("nap2", "perm2") else nap3_mult
        }),
    )
    db.session.add(gp)
    db.session.commit()

    if payout >= bet_amount * 5:
        notify_user(user.id, "Lotto 5/90 Win! 🎫", f"You matched {len(matched)} numbers and won {format_money(payout)}!", "info")

    return jsonify({
        "drawn": drawn,
        "matched": matched,
        "payout": payout,
        "result": result,
        "new_balance": user.balance
    })


# ════════════════════════════════════════════════════════════════
#  FAST FOOTBALL PREDICTOR
# ════════════════════════════════════════════════════════════════
@games_bp.route("/football")
@login_required
def football():
    enabled = str(get_setting("FOOTBALL_ENABLED", "1")) == "1"
    if not enabled:
        flash("Football Predictor is currently unavailable.", "info")
        return redirect(url_for("games.hub"))

    min_bet = float(get_setting("FOOTBALL_MIN_BET", 50))
    max_bet = float(get_setting("FOOTBALL_MAX_BET", 50000))
    odds = float(get_setting("FOOTBALL_ODDS", 1.8))

    return render_template(
        "games/football.html",
        min_bet=min_bet,
        max_bet=max_bet,
        odds=odds,
    )


@csrf.exempt
@games_bp.route("/football/play", methods=["POST"])
@login_required
def football_play():
    user = current_user

    enabled = str(get_setting("FOOTBALL_ENABLED", "1")) == "1"
    if not enabled:
        return jsonify({"error": "Football Predictor is currently unavailable."}), 503

    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    try:
        bet_amount = float(data.get("bet", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid bet amount."}), 400

    min_bet = float(get_setting("FOOTBALL_MIN_BET", 50))
    max_bet = float(get_setting("FOOTBALL_MAX_BET", 50000))

    if bet_amount < min_bet:
        return jsonify({"error": f"Minimum bet is {format_money(min_bet)}."}), 400
    if bet_amount > max_bet:
        return jsonify({"error": f"Maximum bet is {format_money(max_bet)}."}), 400

    if not check_daily_bet_limit(user, bet_amount):
        return jsonify({"error": "Daily betting limit reached."}), 400

    if user.balance < bet_amount:
        return jsonify({"error": "Insufficient balance."}), 400

    if not check_game_cooldown(user.id):
        return jsonify({"error": "Please wait a few seconds between plays."}), 429

    bet_type = data.get("type", "").lower()
    if bet_type not in ("single", "accumulator"):
        return jsonify({"error": "Invalid bet type."}), 400

    predictions = data.get("predictions", [])
    if len(predictions) != 3:
        return jsonify({"error": "Predictions are required for all 3 matches."}), 400

    for pred in predictions:
        if pred not in ("1", "X", "2"):
            return jsonify({"error": "Predictions must be '1', 'X', or '2'."}), 400

    match_index = int(data.get("match_index", 0)) if bet_type == "single" else None

    # Simulate scores
    # Match 1: Lagos City vs Enyimba
    # Match 2: Kano Pillars vs Shooting Stars
    # Match 3: Bendel Insurance vs Rangers Int'l
    scores = []
    outcomes = []
    for _ in range(3):
        h_goals = secrets.randbelow(4)
        a_goals = secrets.randbelow(4)
        scores.append(f"{h_goals}-{a_goals}")
        if h_goals > a_goals:
            outcomes.append("1")
        elif h_goals == a_goals:
            outcomes.append("X")
        else:
            outcomes.append("2")

    # Debit wallet
    debit_wallet(user, bet_amount, "BET", description=f"Football Predictor {bet_type.upper()} bet — {format_money(bet_amount)}")

    # Track daily bet
    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
    user.daily_bet_total += bet_amount

    # Compute payout
    odds = float(get_setting("FOOTBALL_ODDS", 1.8))
    payout = 0.0
    won = False

    if bet_type == "single":
        won = predictions[match_index] == outcomes[match_index]
        payout = bet_amount * odds if won else 0.0
    elif bet_type == "accumulator":
        won = all(predictions[i] == outcomes[i] for i in range(3))
        payout = bet_amount * (odds ** 3) if won else 0.0

    payout = round(payout, 2)
    result = "WIN" if won else "LOSS"

    if payout > 0:
        credit_wallet(user, payout, "WIN", description=f"Football Predictor {bet_type.upper()} win — {format_money(payout)}")

    # Record gameplay
    gp = GamePlay(
        user_id=user.id,
        game_type="football",
        bet_amount=bet_amount,
        payout=payout,
        result=result,
        result_data=json.dumps({
            "type": bet_type,
            "predictions": predictions,
            "scores": scores,
            "outcomes": outcomes,
            "match_index": match_index,
            "odds": odds
        }),
    )
    db.session.add(gp)
    db.session.commit()

    if payout >= bet_amount * 3:
        notify_user(user.id, "Football Win! ⚽", f"Your predictions matched and you won {format_money(payout)}!", "info")

    return jsonify({
        "scores": scores,
        "outcomes": outcomes,
        "payout": payout,
        "result": result,
        "new_balance": user.balance
    })


# ════════════════════════════════════════════════════════════════
#  LUDO QUICK-BET
# ════════════════════════════════════════════════════════════════
@games_bp.route("/ludo")
@login_required
def ludo():
    enabled = str(get_setting("LUDO_ENABLED", "1")) == "1"
    if not enabled:
        flash("Ludo Quick-Bet is currently unavailable.", "info")
        return redirect(url_for("games.hub"))

    min_bet = float(get_setting("LUDO_MIN_BET", 50))
    max_bet = float(get_setting("LUDO_MAX_BET", 50000))
    payout_under_over = float(get_setting("LUDO_PAYOUT_UNDER_OVER", 1.9))
    payout_seven = float(get_setting("LUDO_PAYOUT_SEVEN", 5.5))

    return render_template(
        "games/ludo.html",
        min_bet=min_bet,
        max_bet=max_bet,
        payout_under_over=payout_under_over,
        payout_seven=payout_seven,
    )


@csrf.exempt
@games_bp.route("/ludo/play", methods=["POST"])
@login_required
def ludo_play():
    user = current_user

    enabled = str(get_setting("LUDO_ENABLED", "1")) == "1"
    if not enabled:
        return jsonify({"error": "Ludo Quick-Bet is currently unavailable."}), 503

    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    try:
        bet_amount = float(data.get("bet", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid bet amount."}), 400

    min_bet = float(get_setting("LUDO_MIN_BET", 50))
    max_bet = float(get_setting("LUDO_MAX_BET", 50000))

    if bet_amount < min_bet:
        return jsonify({"error": f"Minimum bet is {format_money(min_bet)}."}), 400
    if bet_amount > max_bet:
        return jsonify({"error": f"Maximum bet is {format_money(max_bet)}."}), 400

    if not check_daily_bet_limit(user, bet_amount):
        return jsonify({"error": "Daily betting limit reached."}), 400

    if user.balance < bet_amount:
        return jsonify({"error": "Insufficient balance."}), 400

    if not check_game_cooldown(user.id):
        return jsonify({"error": "Please wait a few seconds between plays."}), 429

    choice = data.get("choice", "").lower()
    if choice not in ("under7", "over7", "seven"):
        return jsonify({"error": "Invalid betting choice."}), 400

    # Roll 2 dice
    d1 = secrets.randbelow(6) + 1
    d2 = secrets.randbelow(6) + 1
    total = d1 + d2

    # Debit wallet
    debit_wallet(user, bet_amount, "BET", description=f"Ludo Quick-Bet {choice.upper()} bet — {format_money(bet_amount)}")

    # Track daily bet
    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
    user.daily_bet_total += bet_amount

    # Compute payout
    payout_under_over = float(get_setting("LUDO_PAYOUT_UNDER_OVER", 1.9))
    payout_seven = float(get_setting("LUDO_PAYOUT_SEVEN", 5.5))
    payout = 0.0
    won = False

    if choice == "under7" and total < 7:
        won = True
        payout = bet_amount * payout_under_over
    elif choice == "over7" and total > 7:
        won = True
        payout = bet_amount * payout_under_over
    elif choice == "seven" and total == 7:
        won = True
        payout = bet_amount * payout_seven

    payout = round(payout, 2)
    result = "WIN" if won else "LOSS"

    if payout > 0:
        credit_wallet(user, payout, "WIN", description=f"Ludo Quick-Bet win — {format_money(payout)}")

    # Record gameplay
    gp = GamePlay(
        user_id=user.id,
        game_type="ludo",
        bet_amount=bet_amount,
        payout=payout,
        result=result,
        result_data=json.dumps({
            "choice": choice,
            "d1": d1,
            "d2": d2,
            "total": total,
            "multiplier": payout_seven if choice == "seven" else payout_under_over
        }),
    )
    db.session.add(gp)
    db.session.commit()

    if payout >= bet_amount * 3:
        notify_user(user.id, "Ludo Quick-Bet Win! 🎲", f"Dice total was {total}. You won {format_money(payout)}!", "info")

    return jsonify({
        "d1": d1,
        "d2": d2,
        "total": total,
        "payout": payout,
        "result": result,
        "new_balance": user.balance
    })


