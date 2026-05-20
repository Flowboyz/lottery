# DITTO DINKY — Nigerian Lottery Platform

A production-grade, multi-game Nigerian betting platform built with Flask, featuring Paystack payment integration, ledger-safe wallet operations, admin dashboard, referral system, and responsible gambling tools.

---

## Features

- **Pick-3 Lottery Game** — Choose 3 numbers (1–5), match the Lucky Number to win 5× your bet
- **Cryptographic Fairness** — Outcomes determined via Python `secrets` module
- **Paystack Integration** — Card, bank transfer, USSD deposits (Nigerian Naira)
- **Ledger-Safe Wallet** — Every balance change creates a Transaction with `balance_before`/`balance_after`
- **Admin Dashboard** — Stats, user management, withdrawal approval, betting history, audit logs
- **Superadmin Controls** — Balance adjustments, audit log access
- **Referral System** — Unique codes, ₦200 referrer bonus + ₦100 signup bonus
- **Daily Rewards** — Free ₦500 claimable every 24 hours
- **Responsible Gambling** — Daily bet limits, self-exclusion periods (7/30/90/180 days)
- **Notification System** — In-app alerts for wins, deposits, withdrawals
- **Dark/Light Theme** — User preference saved locally
- **Mobile-First Design** — Footer navigation, responsive layout
- **CSRF Protection** — Flask-WTF on all forms
- **Legal Pages** — Terms of Service, Privacy Policy, Responsible Gambling

---

## Quick Start

### Prerequisites
- Python 3.10+
- pip

### 1. Clone & Install

```bash
git clone <your-repo-url>
cd ditto_dinky
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY
```

### 3. Seed Database

```bash
python scripts/seed.py
```

This creates:
| Account     | Username    | Password        | Balance |
|-------------|-------------|-----------------|---------|
| Superadmin  | superadmin  | SuperAdmin@123  | ₦0      |
| Admin       | admin       | Admin@123       | ₦0      |
| Test User   | testuser    | Test@123        | ₦5,000  |

### 4. Run

```bash
python run.py
```

Visit `http://localhost:5000`

---

## Project Structure

```
ditto_dinky/
├── app/
│   ├── __init__.py          # Application factory
│   ├── extensions.py        # SQLAlchemy, LoginManager, CSRF, Mail, Migrate
│   ├── models.py            # User, Transaction, Bet, Withdrawal, Notification, etc.
│   ├── utils.py             # credit_wallet, debit_wallet, decorators, helpers
│   ├── auth/routes.py       # Register, login, logout, forgot/reset password
│   ├── game/routes.py       # Home, play, claim, how-to-play
│   ├── wallet/routes.py     # Deposit (Paystack), withdraw, history
│   ├── admin/routes.py      # Dashboard, users, withdrawals, bets, audit
│   ├── notifications/routes.py  # Inbox, mark read
│   └── legal/routes.py      # Terms, privacy, responsible gambling, self-exclude
├── templates/               # Jinja2 templates (base, game, auth, wallet, admin, legal)
├── static/
│   ├── css/styles.css       # Full stylesheet (dark/light themes)
│   ├── css/auth.css         # Auth page styles
│   └── js/                  # button.js, cooldown.js, amount.js, luckyNumber.js, theme.js
├── scripts/seed.py          # Database seeding
├── config.py                # Dev/Prod configuration
├── run.py                   # Development entry point
├── wsgi.py                  # Production WSGI entry point
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Payment Integration (Paystack)

1. Get API keys from [Paystack Dashboard](https://dashboard.paystack.com/#/settings/developers)
2. Set in `.env`:
   ```
   PAYSTACK_SECRET_KEY=sk_test_xxx
   PAYSTACK_PUBLIC_KEY=pk_test_xxx
   ```
3. For webhook support, configure callback URL: `https://yourdomain.com/wallet/webhook/paystack`

**Test Mode**: If no Paystack keys are configured, deposits are credited directly (for development).

---

## Wallet & Ledger System

All balance changes go through `credit_wallet()` or `debit_wallet()` in `app/utils.py`. Each operation:

1. Records `balance_before` on the Transaction
2. Updates the User's balance
3. Records `balance_after` on the Transaction
4. Commits atomically

Transaction types: `DEPOSIT`, `WITHDRAWAL`, `BET`, `WIN`, `LOSS`, `BONUS`, `REFERRAL`, `CLAIM`, `ADMIN_ADJUST`, `WITHDRAWAL_REFUND`

---

## Admin Panel

Access at `/admin/` (requires `admin` or `superadmin` role).

- **Dashboard** — Total users, deposits, bets, wagered, payouts, pending withdrawals
- **Users** — Search, paginate, view balances/roles
- **Withdrawals** — Approve or reject pending requests (rejected = auto-refund)
- **Bet History** — All bets with numbers, results, payouts
- **Audit Logs** — Superadmin only, tracks admin actions

---

## Responsible Gambling

- Daily betting limit: ₦50,000 (configurable in `config.py`)
- Self-exclusion: 7, 30, 90, or 180 days via `/legal/responsible-gambling`
- Self-excluded users cannot place bets but can still deposit/withdraw

---

## Production Deployment

```bash
# Using gunicorn
gunicorn wsgi:app -b 0.0.0.0:8000 -w 4

# With PostgreSQL (set in .env)
DATABASE_URL=postgresql://user:pass@localhost:5432/ditto_dinky
```

Set `FLASK_ENV=production` and use a strong `SECRET_KEY`.

---

## Configuration

Key settings in `config.py`:

| Setting              | Default    | Description                    |
|----------------------|------------|--------------------------------|
| WIN_PROBABILITY      | 0.10       | 10% chance to win              |
| PAYOUT_MULTIPLIER    | 5          | Win = bet × 5                  |
| COOLDOWN_SECONDS     | 10         | Seconds between plays          |
| MIN_DEPOSIT          | 500        | Minimum deposit (₦)            |
| MIN_WITHDRAWAL       | 1000       | Minimum withdrawal (₦)         |
| MAX_DAILY_BET        | 50000      | Daily betting limit (₦)        |
| DAILY_CLAIM_AMOUNT   | 500        | Free daily reward (₦)          |
| DAILY_CLAIM_COOLDOWN | 86400      | Claim cooldown (24h)           |
| SIGNUP_BONUS         | 100        | New user bonus (₦)             |
| REFERRAL_BONUS       | 200        | Referrer bonus (₦)             |

---

## License

Proprietary. All rights reserved.
