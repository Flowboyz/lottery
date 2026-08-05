"""
One-Time Cleanup Script: Expire stale Whot games and refund trapped stakes.
Run locally first to verify, then on the live server.

Usage: python cleanup_stale_whot.py
"""
import sys
import os

# Ensure the app module is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from app import create_app
from app.extensions import db
from app.models import WhotGame, User
from app.utils import credit_wallet, notify_user

STALE_TIMEOUT_SECONDS = 600  # 10 minutes

app = create_app()

with app.app_context():
    # Find ALL stale games across all users
    stale_games = WhotGame.query.filter(
        WhotGame.status.in_(["negotiating", "active"])
    ).all()

    print(f"\n{'='*60}")
    print(f"  WHOT STALE GAME CLEANUP REPORT")
    print(f"{'='*60}")
    print(f"  Total negotiating/active games found: {len(stale_games)}")
    print(f"  Timeout threshold: {STALE_TIMEOUT_SECONDS}s ({STALE_TIMEOUT_SECONDS // 60} minutes)")
    print(f"{'='*60}\n")

    expired_negotiating = 0
    expired_active = 0
    total_refunded = 0.0
    skipped = 0

    for game in stale_games:
        reference_time = game.updated_at or game.created_at
        elapsed = (datetime.utcnow() - reference_time).total_seconds()

        if elapsed < STALE_TIMEOUT_SECONDS:
            skipped += 1
            continue

        elapsed_mins = elapsed / 60

        if game.status == "negotiating":
            # No money was taken - just mark as expired
            game.status = "expired"
            expired_negotiating += 1
            print(f"  [NEGOTIATING -> EXPIRED] Room: {game.room_id} | Idle: {elapsed_mins:.1f} min | Stake: N0 (no refund needed)")

        elif game.status == "active":
            # Money WAS taken - refund both players
            refunded_players = []

            p1 = User.query.get(game.player1_id)
            if p1 and game.stake > 0:
                credit_wallet(p1, game.stake, "REFUND",
                              description=f"Refund for expired Whot match (Room {game.room_id})")
                notify_user(p1.id, "Whot Game Expired",
                            f"Your Whot match (N{game.stake:,.0f} stake) expired due to inactivity. Your stake has been refunded.",
                            "info")
                refunded_players.append(f"{p1.username} (+N{game.stake:,.0f})")
                total_refunded += game.stake

            if game.player2_id and not game.is_bot_game:
                p2 = User.query.get(game.player2_id)
                if p2 and game.stake > 0:
                    credit_wallet(p2, game.stake, "REFUND",
                                  description=f"Refund for expired Whot match (Room {game.room_id})")
                    notify_user(p2.id, "Whot Game Expired",
                                f"Your Whot match (N{game.stake:,.0f} stake) expired due to inactivity. Your stake has been refunded.",
                                "info")
                    refunded_players.append(f"{p2.username} (+N{game.stake:,.0f})")
                    total_refunded += game.stake

            game.status = "expired"
            expired_active += 1
            refund_str = ", ".join(refunded_players) if refunded_players else "no players found"
            print(f"  [ACTIVE -> EXPIRED]      Room: {game.room_id} | Idle: {elapsed_mins:.1f} min | Stake: N{game.stake:,.0f} | Refunded: {refund_str}")

    db.session.commit()

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Negotiating games expired:  {expired_negotiating}")
    print(f"  Active games expired:       {expired_active}")
    print(f"  Total games expired:        {expired_negotiating + expired_active}")
    print(f"  Games still valid (skipped): {skipped}")
    print(f"  Total amount refunded:      N{total_refunded:,.0f}")
    print(f"{'='*60}\n")
