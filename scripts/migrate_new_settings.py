"""
Migration Script: Seed Ludo, Football, and Lotto 5/90 settings into game_settings table.
Run this script to update your existing database.
Usage: python scripts/migrate_new_settings.py
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import GameSettings


def migrate_settings():
    app = create_app()
    with app.app_context():
        new_settings = {
            "LOTTO590_ENABLED": ("Lotto 5/90 Enabled", "1"),
            "LOTTO590_MIN_BET": ("Lotto 5/90 Min Bet", "50"),
            "LOTTO590_MAX_BET": ("Lotto 5/90 Max Bet", "50000"),
            "LOTTO590_NAP2_PAYOUT": ("Lotto 5/90 Nap 2 Multiplier", "240"),
            "LOTTO590_NAP3_PAYOUT": ("Lotto 5/90 Nap 3 Multiplier", "2100"),
            
            "FOOTBALL_ENABLED": ("Football Predictor Enabled", "1"),
            "FOOTBALL_MIN_BET": ("Football Predictor Min Bet", "50"),
            "FOOTBALL_MAX_BET": ("Football Predictor Max Bet", "50000"),
            "FOOTBALL_ODDS": ("Football Predictor Match Odds", "1.8"),
            
            "LUDO_ENABLED": ("Ludo Quick-Bet Enabled", "1"),
            "LUDO_MIN_BET": ("Ludo Quick-Bet Min Bet", "50"),
            "LUDO_MAX_BET": ("Ludo Quick-Bet Max Bet", "50000"),
            "LUDO_PAYOUT_UNDER_OVER": ("Ludo Quick-Bet Under/Over 7 Payout", "1.9"),
            "LUDO_PAYOUT_SEVEN": ("Ludo Quick-Bet Lucky 7 Payout", "5.5"),
        }

        print("Checking and seeding new game configurations...")
        added_count = 0
        
        for key, (label, val) in new_settings.items():
            setting = GameSettings.query.filter_by(key=key).first()
            if not setting:
                new_setting = GameSettings(
                    key=key,
                    value=val,
                    label=label
                )
                db.session.add(new_setting)
                print(f"  [+] Seeding key: {key} -> {val} ({label})")
                added_count += 1
            else:
                print(f"  [=] Key already exists: {key} (Current value: {setting.value})")

        if added_count > 0:
            db.session.commit()
            print(f"\nSuccessfully added {added_count} new database settings!")
        else:
            print("\nNo new settings to add. Everything is up to date!")


if __name__ == "__main__":
    migrate_settings()
