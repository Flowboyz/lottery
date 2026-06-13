"""
Aviator game blueprint — SHARED ROUND with PRE-COMPUTED MULTIPLIERS
Route prefix: /games/aviator

Round lifecycle:
  betting   →  BETTING_SECS seconds for players to place bets
  flying    →  multiplier climbs; players can cash out any time
  crashed   →  crash point reached; unsettled entries lose; brief pause then new round

NEW: Server pre-computes multiplier values for every 0.1s of flight
     so frontend ONLY renders, never calculates.
"""
import json
import secrets
import hashlib
import math
from datetime import datetime, date, timedelta

from flask import render_template, request, jsonify
from flask_login import login_required, current_user
from app.extensions import socketio
from app.aviator import aviator_bp
from app.extensions import db, csrf
from app.models import GamePlay
from app.models_games import AviatorRound, AviatorEntry
from app.utils import (
    credit_wallet, debit_wallet, notify_user, log_audit,
    check_daily_bet_limit, format_money, get_setting,
)

# Configuration
BETTING_SECS = 10      # How long the betting window lasts
CRASH_PAUSE  = 5       # Pause after crash before new round
MULTIPLIER_PRECISION = 0.1  # Send multiplier updates every 0.1 seconds


# ── helpers ──────────────────────────────────────────────────────────────────

def _enabled():
    return str(get_setting("AVIATOR_ENABLED", "1")) == "1"


def _generate_crash_point(house_edge_pct: float = 4.0):
    """Provably-fair crash point via SHA-256 geometric distribution."""
    seed_bytes = secrets.token_bytes(32)
    seed_hex = seed_bytes.hex()
    h = hashlib.sha256(seed_bytes).hexdigest()
    e = int(h[:8], 16)
    threshold = int((house_edge_pct / 100) * (2 ** 32))
    if e < threshold:
        return 1.00, seed_hex
    crash = (0.99 * (2 ** 32)) / (e - threshold + 1)
    crash = min(max(round(crash, 2), 1.01), 200.00)
    return crash, seed_hex


def _calculate_multiplier(elapsed_seconds: float) -> float:
    """
    Multiplier formula: 1 + 0.25t + 0.04t²
    Matches exactly what frontend expects.
    """
    if elapsed_seconds <= 0:
        return 1.00
    return round(1 + (elapsed_seconds * 0.25) + (elapsed_seconds * elapsed_seconds * 0.04), 2)


def _precompute_multipliers(crash_point: float, max_duration: float = 15.0) -> dict:
    """
    Pre-compute multiplier values at every 0.1s interval.
    Returns a dict with:
    - 'points': list of [elapsed_time, multiplier] pairs
    - 'crash_index': index where multiplier reaches crash_point
    - 'duration': total duration until crash
    """
    points = []
    crash_index = 0
    duration = 0.0
    
    # Find when multiplier reaches crash_point
    # Solve quadratic: 1 + 0.25t + 0.04t² = crash_point
    # => 0.04t² + 0.25t + (1 - crash_point) = 0
    a = 0.04
    b = 0.25
    c = 1 - crash_point
    
    discriminant = b*b - 4*a*c
    if discriminant >= 0:
        # Positive root
        duration = (-b + math.sqrt(discriminant)) / (2*a)
        duration = min(duration, max_duration)
    
    # If crash_point is 1.00 (instant crash), duration is very small
    if crash_point <= 1.01:
        duration = 0.5
    
    # Pre-compute points at every 0.1s interval
    steps = int(duration / MULTIPLIER_PRECISION) + 1
    for i in range(steps + 1):
        t = i * MULTIPLIER_PRECISION
        if t > duration:
            break
        mult = _calculate_multiplier(t)
        points.append([round(t, 2), mult])
        if mult >= crash_point and crash_index == 0:
            crash_index = i
    
    return {
        'points': points,
        'crash_index': crash_index,
        'duration': round(duration, 2),
        'crash_point': crash_point
    }


def _open_new_round() -> AviatorRound:
    """Promote a pre-generated waiting round, or create one if none exists."""
    
    # Check if a pre-generated round is already waiting
    waiting = AviatorRound.query.filter_by(status="waiting").order_by(
        AviatorRound.round_number.asc()
    ).first()
    
    if waiting:
        now = datetime.utcnow()
        waiting.status = "betting"
        waiting.started_at = now
        waiting.betting_ends_at = now + timedelta(seconds=BETTING_SECS)
        db.session.commit()
        print(f"[AVIATOR] Promoted pre-generated Round #{waiting.round_number} → betting")
        return waiting

    # No pre-generated round — create fresh
    last = AviatorRound.query.order_by(AviatorRound.round_number.desc()).first()
    next_num = (last.round_number + 1) if last else 1
    house_edge = float(get_setting("AVIATOR_HOUSE_EDGE", 4))
    crash_point, seed = _generate_crash_point(house_edge)
    precomputed = _precompute_multipliers(crash_point)

    now = datetime.utcnow()
    r = AviatorRound(
        round_number=next_num,
        status="betting",
        crash_point=crash_point,
        seed=seed,
        started_at=now,
        betting_ends_at=now + timedelta(seconds=BETTING_SECS),
        precomputed_data=json.dumps(precomputed)
    )
    db.session.add(r)
    db.session.commit()
    print(f"[AVIATOR] Round #{next_num} created fresh - Crash: {crash_point}x")
    return r

def _settle_crashed_round(round_obj: AviatorRound):
    """Mark round as crashed, settle all un-cashed entries as losses."""
    if round_obj.status != "flying":
        return

    round_obj.status = "crashed"
    round_obj.crashed_at = datetime.utcnow()
    db.session.flush()

    pending = AviatorEntry.query.filter_by(round_id=round_obj.id, result=None).all()
    for entry in pending:
        entry.result = "LOSS"
        entry.payout = 0.0
        from app.models import User
        user = db.session.get(User, entry.user_id)
        if user:
            gp = GamePlay(
                user_id=user.id, game_type="aviator",
                bet_amount=entry.bet_amount, payout=0.0, result="LOSS",
                result_data=json.dumps({
                    "round": round_obj.round_number,
                    "crash_point": round_obj.crash_point,
                }),
            )
            db.session.add(gp)

    db.session.commit()

    # === NEW: Instant crash broadcast via WebSocket ===
    try:
        recent = [
            {"crash_point": r.crash_point, "round_number": r.round_number}
            for r in AviatorRound.query.filter_by(status="crashed")
                .order_by(AviatorRound.crashed_at.desc()).limit(20).all()
            if r.crash_point
        ]
        socketio.emit(
            "aviator_crash",
            {
                "round_id": round_obj.id,
                "round_number": round_obj.round_number,
                "crash_point": round_obj.crash_point,
                "seed": round_obj.seed,
                "recent_crashes": recent,   # ← add this
            },
            room=f"aviator_{round_obj.id}",
        )
    except Exception as e:
        print(f"[Aviator WS] Failed to emit crash: {e}")


import threading
_round_lock = threading.Lock()

def _ensure_round() -> AviatorRound:
    with _round_lock:
        return _ensure_round_inner()

def _ensure_round_inner() -> AviatorRound:
    now = datetime.utcnow()

    # 1. Settle overdue flying rounds
    flying_rounds = AviatorRound.query.filter_by(status="flying").all()
    for r in flying_rounds:
        if r.precomputed_data:
            pre = json.loads(r.precomputed_data)
            duration = pre.get('duration', 10.0)
            elapsed = (now - r.betting_ends_at).total_seconds()
            if elapsed >= duration:
                _settle_crashed_round(r)

    # 2. Crashed → open next round after pause
    crashed = AviatorRound.query.filter_by(status="crashed").order_by(
        AviatorRound.crashed_at.desc()
    ).first()
    if crashed and crashed.crashed_at:
        if (now - crashed.crashed_at).total_seconds() >= CRASH_PAUSE:
            # Only if no betting/flying round already exists
            active = AviatorRound.query.filter(
                AviatorRound.status.in_(["betting", "flying"])
            ).first()
            if not active:
                return _open_new_round()  # ← will promote waiting round

    # 3. Advance betting → flying
    betting = AviatorRound.query.filter_by(status="betting").first()
    if betting and betting.betting_ends_at and now >= betting.betting_ends_at:
        betting.status = "flying"
        db.session.commit()

        # Pre-generate the NEXT next round now (runs in background thread)
        threading.Thread(target=_pregenerate_next, args=(betting.round_number,), daemon=True).start()
        return betting

    # 4. Return current active round (never return "waiting")
    active = AviatorRound.query.filter(
        AviatorRound.status.in_(["betting", "flying"])
    ).order_by(AviatorRound.started_at.desc()).first()

    if not active:
        return _open_new_round()

    return active


def _pregenerate_next(current_round_number: int):
    """
    Pre-generate the round AFTER next in a background thread.
    Runs while current round is flying so next round is ready instantly.
    """
    from app import create_app  # adjust to your app factory import
    app = create_app()
    with app.app_context():
        with _round_lock:
            # Only create if none waiting already
            waiting = AviatorRound.query.filter_by(status="waiting").first()
            if waiting:
                return

            last = AviatorRound.query.order_by(
                AviatorRound.round_number.desc()
            ).first()
            if not last:
                return

            next_num = last.round_number + 1
            house_edge = float(get_setting("AVIATOR_HOUSE_EDGE", 4))
            crash_point, seed = _generate_crash_point(house_edge)
            precomputed = _precompute_multipliers(crash_point)

            r = AviatorRound(
                round_number=next_num,
                status="waiting",
                crash_point=crash_point,
                seed=seed,
                started_at=None,
                betting_ends_at=None,
                precomputed_data=json.dumps(precomputed)
            )
            db.session.add(r)
            db.session.commit()
            print(f"[AVIATOR] Pre-generated Round #{next_num} (crash: {crash_point}x)")


# ── routes ────────────────────────────────────────────────────────────────────

@aviator_bp.route("/")
@login_required
def index():
    if not _enabled():
        # from flask import flash, redirect, url_for
        # flash("Aviator is currently unavailable.", "info")
        # return redirect(url_for("games.hub"))
        return True

    current_round = _ensure_round()

    history = AviatorEntry.query.filter_by(
        user_id=current_user.id
    ).order_by(AviatorEntry.created_at.desc()).limit(6).all()

    recent_crashes = AviatorRound.query.filter_by(status="crashed").order_by(
        AviatorRound.crashed_at.desc()
    ).limit(6).all()

    min_bet = int(get_setting("AVIATOR_MIN_BET", 50))
    max_bet = int(get_setting("AVIATOR_MAX_BET", 50000))

    my_entry = AviatorEntry.query.filter_by(
        round_id=current_round.id, user_id=current_user.id
    ).first()
    
    # Get next round precomputed data (for preloading)
    next_round = AviatorRound.query.filter_by(status="waiting").first()
    next_round_data = None
    if next_round and next_round.precomputed_data:
        next_round_data = json.loads(next_round.precomputed_data)

    return render_template(
        "games/aviator.html",
        current_round=current_round,
        history=history,
        recent_crashes=recent_crashes,
        min_bet=min_bet,
        max_bet=max_bet,
        my_entry=my_entry,
        betting_secs=BETTING_SECS,
        next_round_data=next_round_data,
    )


@csrf.exempt
@aviator_bp.route("/bet", methods=["POST"])
@login_required
def place_bet():
    """Place a bet during the betting phase of the current round."""
    if not _enabled():
        return jsonify({"error": "Aviator is currently unavailable."}), 503

    user = current_user
    if user.is_self_excluded:
        return jsonify({"error": "You are self-excluded from playing."}), 403

    data = request.get_json() or {}
    try:
        bet_amount = float(data.get("bet", 0))
        auto_cashout = data.get("auto_cashout")
        auto_cashout = float(auto_cashout) if auto_cashout else None
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid bet amount."}), 400

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

    current_round = _ensure_round()

    if current_round.status != "betting":
        return jsonify({"error": "Betting is closed. Wait for the next round."}), 400

    now = datetime.utcnow()
    if current_round.betting_ends_at and now >= current_round.betting_ends_at - timedelta(seconds=1):
        return jsonify({"error": "Betting window closing — wait for next round."}), 400

    existing = AviatorEntry.query.filter_by(
        round_id=current_round.id, user_id=user.id
    ).first()
    if existing:
        return jsonify({"error": "You already have a bet this round."}), 400

    debit_wallet(user, bet_amount, "BET",
                 description=f"Aviator round #{current_round.round_number} bet {format_money(bet_amount)}")

    today = date.today()
    if user.daily_bet_date != today:
        user.daily_bet_total = 0.0
        user.daily_bet_date = today
    user.daily_bet_total += bet_amount

    entry = AviatorEntry(
        round_id=current_round.id,
        user_id=user.id,
        bet_amount=bet_amount,
        auto_cashout=auto_cashout,
    )
    db.session.add(entry)
    db.session.commit()

    log_audit("AVIATOR_BET",
              f"Round #{current_round.round_number} bet {format_money(bet_amount)}")

    betting_ends = current_round.betting_ends_at
    seconds_left = max(0, (betting_ends - datetime.utcnow()).total_seconds()) if betting_ends else 0

    return jsonify({
        "success": True,
        "entry_id": entry.id,
        "round_number": current_round.round_number,
        "bet_amount": bet_amount,
        "auto_cashout": auto_cashout,
        "seconds_left": int(seconds_left),
        "new_balance": user.balance,
    })


@csrf.exempt
@aviator_bp.route("/cashout", methods=["POST"])
@login_required
def cashout():
    """Cash out during the flying phase. Server validates multiplier < crash_point."""
    data = request.get_json() or {}
    round_id = data.get("round_id")
    try:
        client_mult = round(float(data.get("multiplier", 1.0)), 2)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid multiplier."}), 400

    current_round = AviatorRound.query.get(round_id)
    if not current_round:
        return jsonify({"error": "Round not found."}), 404
    if current_round.status != "flying":
        return jsonify({"error": "Round is not in flight."}), 400

    entry = AviatorEntry.query.filter_by(
        round_id=current_round.id, user_id=current_user.id
    ).first()
    if not entry:
        return jsonify({"error": "No bet found for this round."}), 404
    if entry.result is not None:
        return jsonify({"error": "Already settled.", "result": entry.result}), 400

    client_mult = max(1.01, client_mult)
    if client_mult >= current_round.crash_point:
        entry.result = "LOSS"
        entry.payout = 0.0
        db.session.commit()
        return jsonify({
            "result": "LOSS",
            "crash_point": current_round.crash_point,
            "payout": 0,
            "new_balance": current_user.balance,
        })

    payout = round(entry.bet_amount * client_mult, 2)
    entry.cashout_at = client_mult
    entry.payout = payout
    entry.result = "WIN"

    credit_wallet(current_user, payout, "WIN",
                  description=f"Aviator #{current_round.round_number} cashout {client_mult}x — {format_money(payout)}")

    gp = GamePlay(
        user_id=current_user.id, game_type="aviator",
        bet_amount=entry.bet_amount, payout=payout, result="WIN",
        result_data=json.dumps({
            "round": current_round.round_number,
            "crash_point": current_round.crash_point,
            "cashout_at": client_mult,
        }),
    )
    db.session.add(gp)
    db.session.commit()

    # === NEW: Big win notification via WebSocket (optional) ===
    if payout >= entry.bet_amount * 5:
        try:
            socketio.emit(
                "aviator_big_win",
                {
                    "round_id": current_round.id,
                    "multiplier": client_mult,
                    "payout": payout,
                    "username": current_user.username,
                },
                room=f"aviator_{current_round.id}",
            )
        except Exception as e:
            print(f"[Aviator WS] Failed to emit big_win: {e}")

        notify_user(current_user.id, "Aviator Big Win! ✈️",
                    f"Cashed out at {client_mult}x — won {format_money(payout)}!", "info")

    log_audit("AVIATOR_CASHOUT",
              f"Round #{current_round.round_number} cashout @{client_mult}x → {format_money(payout)}")

    return jsonify({
        "result": "WIN",
        "cashout_at": client_mult,
        "payout": payout,
        "new_balance": current_user.balance,
    })



@aviator_bp.route("/status")
@login_required
def round_status():
    """
    Polled every second by every connected client.
    Returns FULL round state with PRE-COMPUTED multiplier points.
    Frontend only renders - no calculations needed!
    """
    current_round = _ensure_round()
    now = datetime.utcnow()

    # Time calculations
    if current_round.status == "betting" and current_round.betting_ends_at:
        seconds_left = max(0, (current_round.betting_ends_at - now).total_seconds())
        elapsed_flying = 0.0
    elif current_round.status == "flying" and current_round.betting_ends_at:
        seconds_left = 0
        elapsed_flying = (now - current_round.betting_ends_at).total_seconds()
    else:
        seconds_left = 0
        elapsed_flying = 0.0

    # Get precomputed data
    precomputed = None
    if current_round.precomputed_data:
        precomputed = json.loads(current_round.precomputed_data)

    # Current multiplier (from precomputed points or calculated)
    mult = 1.00
    if current_round.status == "flying" and precomputed:
        points = precomputed.get('points', [])
        for i, (t, m) in enumerate(points):
            if t >= elapsed_flying:
                mult = m
                break
    elif current_round.status == "flying":
        mult = _calculate_multiplier(elapsed_flying)

    # This user's entry
    my_entry = AviatorEntry.query.filter_by(
        round_id=current_round.id, user_id=current_user.id
    ).first()

    # Auto-cashout checking
    auto_cashed = False
    auto_result = None
    if (current_round.status == "flying" and my_entry
            and my_entry.result is None and my_entry.auto_cashout
            and mult >= my_entry.auto_cashout):
        if my_entry.auto_cashout < current_round.crash_point:
            payout = round(my_entry.bet_amount * my_entry.auto_cashout, 2)
            my_entry.cashout_at = my_entry.auto_cashout
            my_entry.payout = payout
            my_entry.result = "WIN"
            credit_wallet(current_user, payout, "WIN",
                          description=f"Aviator #{current_round.round_number} auto-cashout {my_entry.auto_cashout}x")
            gp = GamePlay(
                user_id=current_user.id, game_type="aviator",
                bet_amount=my_entry.bet_amount, payout=payout, result="WIN",
                result_data=json.dumps({"round": current_round.round_number,
                                        "cashout_at": my_entry.auto_cashout, "auto": True}),
            )
            db.session.add(gp)
            db.session.commit()
            auto_cashed = True
            auto_result = {
                'cashout_at': my_entry.auto_cashout,
                'payout': payout
            }

    # Get next round data for preloading
    next_round = AviatorRound.query.filter_by(status="waiting").first()
    next_round_data = None
    if next_round and next_round.precomputed_data:
        next_round_data = json.loads(next_round.precomputed_data)

    # Last crashed round
    last_crashed = AviatorRound.query.filter_by(status="crashed").order_by(
        AviatorRound.crashed_at.desc()
    ).first()

    return jsonify({
        "round_id": current_round.id,
        "round_number": current_round.round_number,
        "status": current_round.status,
        "seconds_left": int(seconds_left),
        "elapsed_flying": round(elapsed_flying, 2),
        "multiplier": mult,
        "crash_point": current_round.crash_point if current_round.status == "crashed" else None,
        "seed": current_round.seed if current_round.status == "crashed" else None,
        "precomputed": precomputed,  # ← NEW: Send precomputed multiplier points
        "next_round": next_round_data,  # ← NEW: Preload next round data
        "my_bet": my_entry.bet_amount if my_entry else None,
        "my_result": my_entry.result if my_entry else None,
        "my_payout": my_entry.payout if my_entry else None,
        "my_cashout_at": my_entry.cashout_at if my_entry else None,
        "auto_cashed": auto_cashed,
        "auto_result": auto_result,
        "new_balance": current_user.balance,
        "last_crash": last_crashed.crash_point if last_crashed else None,
        "last_round": last_crashed.round_number if last_crashed else None,
        "recent_crashes": [
            {"crash_point": r.crash_point, "round_number": r.round_number}
            for r in AviatorRound.query.filter_by(status="crashed")
                .order_by(AviatorRound.crashed_at.desc()).limit(20).all()
            if r.crash_point
],
    })
    
@aviator_bp.route("/crash-history")
@login_required
def crash_history():
    """Get recent crash points for the UI chips"""
    recent_crashes = AviatorRound.query.filter(
        AviatorRound.status == "crashed",
        AviatorRound.crash_point.isnot(None)
    ).order_by(AviatorRound.crashed_at.desc()).limit(10).all()
    
    crashes = []
    for r in recent_crashes:
        crashes.append({
            'crash_point': r.crash_point,
            'round_number': r.round_number,
            'timestamp': r.crashed_at.isoformat() if r.crashed_at else None
        })
    
    return jsonify({'crashes': crashes})    

@csrf.exempt
@aviator_bp.route("/crash-round", methods=["POST"])
@login_required
def crash_round():
    """
    Called by the client whose rAF loop detects elapsed >= precomputed duration.
    Server validates timing and settles all remaining entries.
    Idempotent — safe to call multiple times.
    """
    data = request.get_json() or {}
    round_id = data.get("round_id")
    r = AviatorRound.query.get(round_id)
    if not r:
        return jsonify({"error": "Round not found"}), 404
    if r.status == "crashed":
        return jsonify({"crashed": True, "crash_point": r.crash_point, "seed": r.seed})
    if r.status != "flying":
        return jsonify({"crashed": False})

    # Validate: has enough time elapsed for crash to be legit?
    now = datetime.utcnow()
    if r.betting_ends_at:
        actual_elapsed = (now - r.betting_ends_at).total_seconds()
        pre = json.loads(r.precomputed_data) if r.precomputed_data else {}
        duration = pre.get("duration", 0)
        # Allow 15% clock-drift tolerance
        if actual_elapsed < duration * 0.85:
            return jsonify({"crashed": False, "too_early": True})

    _settle_crashed_round(r)
    return jsonify({"crashed": True, "crash_point": r.crash_point, "seed": r.seed})


@aviator_bp.route("/players")
@login_required
def live_players():
    """Live bets panel — returns all entries for the current round."""
    current_round = _ensure_round()
    entries = AviatorEntry.query.filter_by(round_id=current_round.id).all()
    from app.models import User
    players = []
    for e in entries:
        user = db.session.get(User, e.user_id)
        name = user.username if user else "Player"
        masked = name[:2] + "*" * max(2, len(name) - 2) if len(name) > 2 else name
        players.append({
            "name":       masked,
            "bet":        e.bet_amount,
            "cashout_at": e.cashout_at,
            "payout":     e.payout if e.result == "WIN" else None,
            "result":     e.result,
        })
    return jsonify({"round_id": current_round.id, "status": current_round.status, "players": players})
