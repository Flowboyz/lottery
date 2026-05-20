"""
Game blueprint - Lottery engine (pick 3 numbers).
"""
import secrets
import time
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Bet, LotteryRound
from app.utils import (
    credit_wallet, debit_wallet, notify_user, log_audit,
    check_daily_bet_limit, format_money,
)

game_bp = Blueprint("game", __name__)


@game_bp.route("/")
@login_required
def home():
    user = current_user
    current_time = int(time.time())

    # Cooldown
    cooldown = current_app.config["COOLDOWN_SECONDS"]
    remaining_cooldown = 0
    if user.last_play_time:
        elapsed = current_time - user.last_play_time
        if elapsed < cooldown:
            remaining_cooldown = cooldown - elapsed

    # Daily claim countdown
    claim_remaining = 0
    claim_cooldown = current_app.config["DAILY_CLAIM_COOLDOWN"]
    if user.last_claim_time:
        elapsed = current_time - user.last_claim_time
        if elapsed < claim_cooldown:
            claim_remaining = claim_cooldown - elapsed

    # Recent bets (for lucky number display)
    recent_bets = Bet.query.filter_by(user_id=user.id).order_by(
        Bet.created_at.desc()
    ).limit(5).all()
    recent_lucky = [b.lucky_number for b in recent_bets if b.lucky_number is not None]

    # Streak detection
    streak = None
    if recent_bets:
        results = [b.result for b in recent_bets if b.result]
        if len(results) >= 3:
            first = results[0]
            count = 0
            for r in results:
                if r == first:
                    count += 1
                else:
                    break
            if count >= 3:
                if first == "WIN":
                    streak = f"HOT STREAK ({count} wins)"
                else:
                    streak = f"COLD STREAK ({count} losses)"

    # Unread notifications count
    from app.models import Notification
    unread_count = Notification.query.filter_by(
        user_id=user.id, is_read=False
    ).count()

    return render_template(
        "game/index.html",
        balance=user.balance,
        win_probability=int(current_app.config["WIN_PROBABILITY"] * 100),
        remaining_cooldown=remaining_cooldown,
        recent_lucky=recent_lucky,
        claim_remaining=claim_remaining,
        streak=streak,
        unread_count=unread_count,
    )


@game_bp.route("/play", methods=["POST"])
@login_required
def play():
    user = current_user
    config = current_app.config

    # Self-exclusion check
    if user.is_self_excluded:
        flash("You are currently self-excluded from playing.", "error")
        return redirect(url_for("game.home"))

    # Cooldown check
    current_time = int(time.time())
    cooldown = config["COOLDOWN_SECONDS"]
    if user.last_play_time and current_time - user.last_play_time < cooldown:
        flash(f"Wait {cooldown} seconds before playing again.", "error")
        return redirect(url_for("game.home"))

    # Parse inputs
    try:
        num1 = int(request.form.get("num1", 0))
        num2 = int(request.form.get("num2", 0))
        num3 = int(request.form.get("num3", 0))
        bet_amount = float(request.form.get("bet", 0))
    except (ValueError, TypeError):
        flash("Invalid input.", "error")
        return redirect(url_for("game.home"))

    if not (1 <= num1 <= 5 and 1 <= num2 <= 5 and 1 <= num3 <= 5):
        flash("Select all 3 numbers (1-5).", "error")
        return redirect(url_for("game.home"))

    if bet_amount <= 0:
        flash("Bet must be greater than 0.", "error")
        return redirect(url_for("game.home"))

    if bet_amount > user.balance:
        flash("Insufficient balance.", "error")
        return redirect(url_for("game.home"))

    # Daily limit check
    if not check_daily_bet_limit(user, bet_amount):
        flash(f"Daily betting limit of {format_money(config['MAX_DAILY_BET'])} reached.", "error")
        return redirect(url_for("game.home"))

    picked_total = num1 + num2 + num3

    # Determine outcome using cryptographic randomness
    win_prob = config["WIN_PROBABILITY"]
    payout_mult = config["PAYOUT_MULTIPLIER"]

    # Use secrets for fair randomness
    win = secrets.randbelow(1000) < int(win_prob * 1000)

    if win:
        lucky_number = picked_total
        payout = bet_amount * payout_mult
        result = "WIN"
        # Debit bet then credit winnings
        debit_wallet(user, bet_amount, "BET", f"Bet on round (picked {picked_total})")
        credit_wallet(user, payout, "WIN", f"Won {format_money(payout)} (lucky: {lucky_number})")
        notify_user(user.id, "You Won!", f"You won {format_money(payout)}!", "win")
        result_msg = f"YOU WON {format_money(payout)}"
    else:
        # Generate a non-matching lucky number
        lucky_number = secrets.randbelow(13) + 3  # 3-15
        while lucky_number == picked_total:
            lucky_number = secrets.randbelow(13) + 3

        payout = 0
        result = "LOSS"
        debit_wallet(user, bet_amount, "LOSS", f"Lost bet (picked {picked_total}, lucky {lucky_number})")
        result_msg = f"You lost {format_money(bet_amount)}"

    # Record bet
    bet = Bet(
        user_id=user.id,
        num1=num1, num2=num2, num3=num3,
        picked_total=picked_total,
        bet_amount=bet_amount,
        lucky_number=lucky_number,
        payout=payout,
        result=result,
    )
    db.session.add(bet)

    # Update user
    user.last_play_time = current_time
    user.daily_bet_total = (user.daily_bet_total or 0) + bet_amount
    user.daily_bet_date = date.today()
    db.session.commit()

    flash({
        "result": result_msg,
        "balance": user.balance,
        "lucky": lucky_number,
        "total": picked_total,
        "is_win": result == "WIN",
    }, "game_result")

    return redirect(url_for("game.home"))


@game_bp.route("/claim")
@login_required
def claim():
    user = current_user
    current_time = int(time.time())
    claim_cooldown = current_app.config["DAILY_CLAIM_COOLDOWN"]
    reward = current_app.config["DAILY_CLAIM_AMOUNT"]

    if user.last_claim_time and current_time - user.last_claim_time < claim_cooldown:
        remaining = claim_cooldown - (current_time - user.last_claim_time)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        flash(f"Come back in {hours}h {minutes}m.", "error")
        return redirect(url_for("game.home"))

    credit_wallet(user, reward, "CLAIM", "Daily free reward")
    user.last_claim_time = current_time
    db.session.commit()

    notify_user(user.id, "Daily Reward!", f"You claimed {format_money(reward)}!", "info")
    flash(f"You claimed {format_money(reward)}!", "success")
    return redirect(url_for("game.home"))


@game_bp.route("/how-to-play")
def how_to_play():
    return render_template("game/how_to_play.html")
