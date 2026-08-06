"""
Database-agnostic migration script to create all missing tables (including whot_games).
Works on both SQLite (local development) and PostgreSQL/MySQL (production environments).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db

def upgrade():
    app = create_app()
    with app.app_context():
        print("Checking database tables and creating any missing ones...")
        db.create_all()
        print("Database tables upgraded successfully!")

if __name__ == "__main__":
    upgrade()
