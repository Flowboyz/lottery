import hmac
import hashlib
import json
import logging
from urllib.parse import parse_qsl
import requests
from flask import current_app

logger = logging.getLogger(__name__)

def verify_telegram_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Verify Telegram WebApp initData cryptographic signature.
    Official spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        parsed = dict(parse_qsl(init_data))
        if "hash" not in parsed:
            return None
        
        received_hash = parsed.pop("hash")
        
        # Sort and create check string
        sorted_items = sorted(parsed.items())
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)
        
        # HMAC-SHA256 of bot token with key "WebAppData"
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        
        # HMAC-SHA256 of check string using the secret key
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if hmac.compare_digest(computed_hash, received_hash):
            return parsed
    except Exception as e:
        logger.error(f"[Telegram Helpers] Error verifying initData: {e}")
        
    return None


def send_telegram_message(chat_id: int | str, text: str, reply_markup: dict | None = None) -> bool:
    """
    Send a message back to the Telegram user via the bot API.
    """
    token = current_app.config.get("TELEGRAM_BOT_TOKEN") or current_app.config.get("SECRET_KEY") # fallback or direct settings get
    from app.utils import get_setting
    bot_token = get_setting("TELEGRAM_BOT_TOKEN") or current_app.config.get("TELEGRAM_BOT_TOKEN")
    
    if not bot_token:
        logger.warning("[Telegram Bot] Bot token not configured. Cannot send message.")
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
        
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"[Telegram Bot] API returned error: {data.get('description')}")
            return False
        return True
    except Exception as e:
        logger.error(f"[Telegram Bot] Failed to send message: {e}")
        return False
