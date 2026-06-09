"""
Games blueprint — Spin Wheel, Coin Flip, Scratch Card.
"""
import json
import secrets
from datetime import datetime, date

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
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
        return jsonify({"error": "Daily bet limit reached."}), 400

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
        return jsonify({"error": "Daily bet limit reached."}), 400
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
            return jsonify({"error": "Daily bet limit reached."}), 400
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

