"""
Seed script: creates default admin and superadmin accounts.
Usage: python scripts/seed.py
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User
from werkzeug.security import generate_password_hash


def seed():
    app = create_app()
    with app.app_context():
        db.create_all()

        # Create superadmin
        if not User.query.filter_by(username="superadmin").first():
            sa = User(
                username="superadmin",
                email="superadmin@dittodinky.com",
                password_hash=generate_password_hash("SuperAdmin@123"),
                role="superadmin",
                balance=0.0,
            )
            db.session.add(sa)
            print("[+] Created superadmin (superadmin / SuperAdmin@123)")
        else:
            print("[=] superadmin already exists")

        # Create admin
        admin_user = os.getenv("ADMIN_USERNAME", "admin")
        admin_pass = os.getenv("ADMIN_PASSWORD", "Admin@123")
        admin_email = os.getenv("ADMIN_EMAIL", "admin@dittodinky.com")

        if not User.query.filter_by(username=admin_user).first():
            admin = User(
                username=admin_user,
                email=admin_email,
                password_hash=generate_password_hash(admin_pass),
                role="admin",
                balance=0.0,
            )
            db.session.add(admin)
            print(f"[+] Created admin ({admin_user} / {admin_pass})")
        else:
            print(f"[=] {admin_user} already exists")

        # Create test user
        if not User.query.filter_by(username="testuser").first():
            test = User(
                username="testuser",
                email="test@dittodinky.com",
                password_hash=generate_password_hash("Test@123"),
                role="user",
                balance=5000.0,
            )
            db.session.add(test)
            print("[+] Created testuser (testuser / Test@123) with ₦5,000")
        else:
            print("[=] testuser already exists")

        db.session.commit()
        print("\nSeeding complete!")


if __name__ == "__main__":
    seed()
