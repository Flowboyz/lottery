"""
Additional models for Aviator and Color Prediction games.
Import this in app/__init__.py (alongside models.py).
"""

from datetime import datetime
from app.extensions import db


# ---------------------------------------------------------------------------
# Color Prediction
# ---------------------------------------------------------------------------
class ColorRound(db.Model):
    __tablename__ = "color_rounds"

    id           = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, unique=True, nullable=False)
    result       = db.Column(db.String(10), nullable=True)      # red | green | violet
    status       = db.Column(db.String(10), default="open", index=True)
    started_at   = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at    = db.Column(db.DateTime, nullable=True)
    seed         = db.Column(db.String(64), nullable=True)

    entries = db.relationship("ColorEntry", backref="round", lazy="dynamic")


class ColorEntry(db.Model):
    __tablename__ = "color_entries"

    id          = db.Column(db.Integer, primary_key=True)
    round_id    = db.Column(db.Integer, db.ForeignKey("color_rounds.id"), nullable=False, index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    choice      = db.Column(db.String(10), nullable=False)      # red | green | violet
    bet_amount  = db.Column(db.Float, nullable=False)
    payout      = db.Column(db.Float, default=0.0)
    result      = db.Column(db.String(10), nullable=True)       # WIN | LOSS
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("color_entries", lazy="dynamic"))


# ---------------------------------------------------------------------------
# Aviator (Shared Round System)
# ---------------------------------------------------------------------------
class AviatorRound(db.Model):
    __tablename__ = "aviator_rounds"

    id              = db.Column(db.Integer, primary_key=True)
    round_number    = db.Column(db.Integer, unique=True, nullable=False)
    # waiting | betting | flying | crashed
    status          = db.Column(db.String(10), default="betting", index=True)
    crash_point     = db.Column(db.Float, nullable=True)
    seed            = db.Column(db.String(64), nullable=True)
    betting_ends_at = db.Column(db.DateTime, nullable=True)
    started_at      = db.Column(db.DateTime, default=datetime.utcnow)
    crashed_at      = db.Column(db.DateTime, nullable=True)

    # JSON: { "points": [[t, mult], ...], "duration": float, "crash_point": float }
    precomputed_data = db.Column(db.Text, nullable=True)

    entries = db.relationship("AviatorEntry", backref="round", lazy="dynamic")


class AviatorEntry(db.Model):
    __tablename__ = "aviator_entries"

    id           = db.Column(db.Integer, primary_key=True)
    round_id     = db.Column(db.Integer, db.ForeignKey("aviator_rounds.id"), nullable=False, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    bet_amount   = db.Column(db.Float, nullable=False)
    cashout_at   = db.Column(db.Float, nullable=True)
    payout       = db.Column(db.Float, default=0.0)
    result       = db.Column(db.String(10), nullable=True)      # WIN | LOSS | None
    auto_cashout = db.Column(db.Float, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("aviator_entries", lazy="dynamic"))