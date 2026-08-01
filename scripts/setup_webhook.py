"""
Telegram Bot Webhook Setup Helper.
Usage: venv/Scripts/python scripts/setup_webhook.py <URL>
E.g.: venv/Scripts/python scripts/setup_webhook.py https://flowboy.pythonanywhere.com
"""
import os, sys, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app
from app.utils import get_setting

if len(sys.argv) < 2:
    print("Error: Missing base URL argument.")
    print("Usage: venv/Scripts/python scripts/setup_webhook.py <YOUR_WEBSITE_URL>")
    sys.exit(1)

base_url = sys.argv[1].rstrip("/")
if not base_url.startswith("https://"):
    if base_url.startswith("http://"):
        base_url = base_url.replace("http://", "https://")
    else:
        base_url = f"https://{base_url}"

app = create_app()
with app.app_context():
    bot_token = get_setting("TELEGRAM_BOT_TOKEN") or app.config.get("TELEGRAM_BOT_TOKEN")

if not bot_token:
    print("Error: TELEGRAM_BOT_TOKEN not configured in settings or .env file.")
    sys.exit(1)

webhook_url = f"{base_url}/telegram/webhook"
api_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"

print(f"Setting webhook to: {webhook_url}")
try:
    resp = requests.post(api_url, json={"url": webhook_url}, timeout=15)
    result = resp.json()
    if result.get("ok"):
        print("Success! Webhook set successfully.")
        print(f"Details: {result.get('description')}")
    else:
        print(f"Failed! Telegram API Error: {result.get('description')}")
except Exception as e:
    print(f"Error making request to Telegram API: {e}")
