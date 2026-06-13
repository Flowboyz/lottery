"""
Color Prediction game blueprint.
Route prefix: /games/color

Round lifecycle:
  • A background "tick" (triggered by any GET or bet) ensures an open round exists.
  • After COLOR_ROUND_DURATION seconds the round is closed and settled by the
    first request that notices it has expired.
  • Results are server-generated, never client-supplied.
"""
import json
import secrets
import hashlib
from datetime import datetime, date, timedelta

from flask import render_template, request, jsonify
from flask_login import login_required, current_user

from app.color import color_bp
from app.extensions import db, csrf
from app.models import GamePlay
from app.models_games import ColorRound, ColorEntry
from app.utils import (
    credit_wallet, debit_wallet, notify_user, log_audit,
    check_daily_bet_limit, format_money, get_setting,
)


# ─── helpers ────────────────────────────────────────────────────────────────

CHOICES = ("red", "green", "violet")


def _color_enabled():
    return str(get_setting("COLOR_ENABLED", "1")) == "1"


def _payout_for(choice: str) -> float:
    if choice == "red":
        return float(get_setting("COLOR_RED_PAYOUT", 2.0))
    if choice == "green":
        return float(get_setting("COLOR_GREEN_PAYOUT", 2.0))
    return float(get_setting("COLOR_VIOLET_PAYOUT", 4.5))


def _generate_result(seed_hex: str) -> str:
    """Deterministic result from seed.  violet is rarer (~22 %)."""
    h = int(hashlib.sha256(bytes.fromhex(seed_hex)).hexdigest()[:8], 16)
    # Weights: red=39%, green=39%, violet=22%
    r = h % 100
    if r < 39:
        return "red"
    if r < 78:
        return "green"
    return "violet"


def _ensure_open_round() -> ColorRound:
    """Return the current open round, settling expired ones first."""
    duration = int(get_setting("COLOR_ROUND_DURATION", 30))

    # Settle any expired open rounds
    cutoff = datetime.utcnow() - timedelta(seconds=duration)
    expired = ColorRound.query.filter(
        ColorRound.status == "open",
        ColorRound.started_at <= cutoff,
    ).all()
    for r in expired:
        _settle_round(r)

    # Return existing open round, or create a new one
    open_round = ColorRound.query.filter_by(status="open").order_by(
        ColorRound.started_at.desc()
    ).first()
    if open_round:
        return open_round

    # Create next round
    last = ColorRound.query.order_by(ColorRound.round_number.desc()).first()
    next_num = (last.round_number + 1) if last else 1
    seed = secrets.token_bytes(32).hex()
    new_round = ColorRound(round_number=next_num, status="open", seed=seed)
    db.session.add(new_round)
    db.session.commit()
    return new_round


def _settle_round(round_obj: ColorRound):
    """Close round, generate result, pay out winners."""
    if round_obj.status != "open":
        return
    round_obj.status = "closed"
    round_obj.closed_at = datetime.utcnow()
    result = _generate_result(round_obj.seed)
    round_obj.result = result
    db.session.flush()

    entries = ColorEntry.query.filter_by(round_id=round_obj.id, result=None).all()
    for entry in entries:
        payout_mult = _payout_for(entry.choice)
        if entry.choice == result:
            payout = round(entry.bet_amount * payout_mult, 2)
            entry.payout = payout
            entry.result = "WIN"
            from app.models import User
            user = db.session.get(User, entry.user_id)
            if user:
                credit_wallet(user, payout, "WIN",
                              description=f"Color #{round_obj.round_number} {result} win — {format_money(payout)}")
                # Mirror to GamePlay
                gp = GamePlay(
                    user_id=user.id, game_type="color",
                    bet_amount=entry.bet_amount, payout=payout, result="WIN",
                    result_data=json.dumps({
                        "round": round_obj.round_number,
                        "choice": entry.choice,
                        "result": result,
                        "multiplier": payout_mult,
                    }),
                )
                db.session.add(gp)
                if payout >= entry.bet_amount * 3:
                    notify_user(user.id, "Color Prediction Win! 🎨",
                                f"Round #{round_obj.round_number}: {result.upper()} — you won {format_money(payout)}!", "info")
        else:
            entry.payout = 0.0
            entry.result = "LOSS"
            from app.models import User
            user = db.session.get(User, entry.user_id)
            if user:
                gp = GamePlay(
                    user_id=user.id, game_type="color",
                    bet_amount=entry.bet_amount, payout=0.0, result="LOSS",
                    result_data=json.dumps({
                        "round": round_obj.round_number,
                        "choice": entry.choice,
                        "result": result,
                    }),
                )
                db.session.add(gp)

    db.session.commit()


# ─── routes ─────────────────────────────────────────────────────────────────

@color_bp.route("/")
@login_required
def index():
    if not _color_enabled():
        # from flask import flash, redirect, url_for
        # flash("Color Prediction is currently unavailable.", "info")
        # return redirect(url_for("games.hub"))
       return str(get_setting("COLOR_ENABLED", "1")) == "1"

    current_round = _ensure_open_round()
    duration = int(get_setting("COLOR_ROUND_DURATION", 30))
    elapsed = (datetime.utcnow() - current_round.started_at).total_seconds()
    seconds_left = max(0, duration - int(elapsed))

    history = ColorRound.query.filter_by(status="closed").order_by(
        ColorRound.round_number.desc()
    ).limit(15).all()

    # User's entry for current round (if any)
    my_entry = ColorEntry.query.filter_by(
        round_id=current_round.id, user_id=current_user.id
    ).first()

    payouts = {
        "red":    float(get_setting("COLOR_RED_PAYOUT", 2.0)),
        "green":  float(get_setting("COLOR_GREEN_PAYOUT", 2.0)),
        "violet": float(get_setting("COLOR_VIOLET_PAYOUT", 4.5)),
    }

    min_bet = float(get_setting("AVIATOR_MIN_BET", 50))  # reuse platform min
    max_bet = float(get_setting("AVIATOR_MAX_BET", 50000))

    return render_template(
        "games/color.html",
        current_round=current_round,
        seconds_left=seconds_left,
        history=history,
        my_entry=my_entry,
        payouts=payouts,
        min_bet=min_bet,
        max_bet=max_bet,
    )


@csrf.exempt
@color_bp.route("/bet", methods=["POST"])
@login_required
def place_bet():
    if not _color_enabled():
        return jsonify({"error": "Color Prediction is currently unavailable."}), 503

    user = current_user

    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    choice = data.get("choice", "").lower()
    try:
        bet_amount = float(data.get("bet", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid bet amount."}), 400

    if choice not in CHOICES:
        return jsonify({"error": "Pick red, green, or violet."}), 400

    min_bet = float(get_setting("AVIATOR_MIN_BET", 50))
    max_bet = float(get_setting("AVIATOR_MAX_BET", 50000))
    if bet_amount < min_bet:
        return jsonify({"error": f"Minimum bet is {format_money(min_bet)}."}), 400
    if bet_amount > max_bet:
        return jsonify({"error": f"Maximum bet is {format_money(max_bet)}."}), 400
    if user.balance < bet_amount:
        return jsonify({"error": "Insufficient balance."}), 400
    if not check_daily_bet_limit(user, bet_amount):
        return jsonify({"error": "Daily betting limit reached."}), 400

    current_round = _ensure_open_round()

    # One entry per user per round
    existing = ColorEntry.query.filter_by(
        round_id=current_round.id, user_id=user.id
    ).first()
    if existing:
        return jsonify({"error": "You already placed a bet this round."}), 400

    duration = int(get_setting("COLOR_ROUND_DURATION", 30))
    elapsed = (datetime.utcnow() - current_round.started_at).total_seconds()
    if elapsed >= duration - 3:  # 3-second lock before round ends
        return jsonify({"error": "Round is closing, wait for next round."}), 400

    debit_wallet(user, bet_amount, "BET",
                 description=f"Color #{current_round.round_number} bet on {choice} — {format_money(bet_amount)}")

    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
    user.daily_bet_total += bet_amount

    entry = ColorEntry(
        round_id=current_round.id,
        user_id=user.id,
        choice=choice,
        bet_amount=bet_amount,
    )
    db.session.add(entry)
    db.session.commit()

    log_audit("COLOR_BET",
              f"Round #{current_round.round_number} {choice} {format_money(bet_amount)}")

    seconds_left = max(0, duration - int(elapsed))
    return jsonify({
        "success": True,
        "round_number": current_round.round_number,
        "choice": choice,
        "bet_amount": bet_amount,
        "seconds_left": seconds_left,
        "new_balance": user.balance,
        "multiplier": _payout_for(choice),
    })


@color_bp.route("/status")
@login_required
def round_status():
    """Polled by frontend every second to get timer + result."""
    current_round = _ensure_open_round()
    duration = int(get_setting("COLOR_ROUND_DURATION", 30))
    elapsed = (datetime.utcnow() - current_round.started_at).total_seconds()
    seconds_left = max(0, duration - int(elapsed))

    # Check if user has a settled entry
    my_entry = ColorEntry.query.filter_by(
        round_id=current_round.id, user_id=current_user.id
    ).first()

    # Last closed round result for reveal animation
    last_closed = ColorRound.query.filter_by(status="closed").order_by(
        ColorRound.round_number.desc()
    ).first()

    return jsonify({
        "round_number": current_round.round_number,
        "seconds_left": seconds_left,
        "status": current_round.status,
        "my_choice": my_entry.choice if my_entry else None,
        "my_bet": my_entry.bet_amount if my_entry else None,
        "my_result": my_entry.result if my_entry else None,
        "my_payout": my_entry.payout if my_entry else None,
        "last_result": last_closed.result if last_closed else None,
        "last_round": last_closed.round_number if last_closed else None,
        "new_balance": current_user.balance,
    })
