import json
import secrets
from datetime import datetime, timedelta
import requests

from flask import request, jsonify, render_template, redirect, url_for, flash, current_app
from flask_login import login_required, current_user, login_user

from app.extensions import db, csrf
from app.models import User
from app.telegram import telegram_bp
from app.telegram.helpers import verify_telegram_init_data, send_telegram_message
from app.utils import get_setting

def _attempt_link(chat_id, token):
    now = datetime.utcnow()
    user = User.query.filter_by(telegram_link_token=token).first()
    if user and user.telegram_link_expires and user.telegram_link_expires > now:
        # Clear token and link ID
        user.telegram_user_id = str(chat_id)
        user.telegram_link_token = None
        user.telegram_link_expires = None
        db.session.commit()
        return user
    return None

@csrf.exempt
@telegram_bp.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json() or {}
    if "message" not in update:
        return jsonify({"status": "ignored"}), 200
        
    message = update["message"]
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user_info = message.get("from", {})
    text = message.get("text", "").strip()
    
    if not text or not chat_id:
        return jsonify({"status": "ignored"}), 200
        
    # Get Bot configuration
    web_app_url = get_setting("TELEGRAM_MINI_APP_URL") or request.host_url
    if web_app_url.startswith("http://"):
        web_app_url = web_app_url.replace("http://", "https://")
        
    play_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "Play Now 🎮",
                    "web_app": {
                        "url": web_app_url
                    }
                }
            ]
        ]
    }
    
    if text.startswith("/start"):
        parts = text.split()
        if len(parts) > 1:
            token = parts[1].upper()
            linked = _attempt_link(chat_id, token)
            if linked:
                welcome_text = (
                    f"🎉 <b>Success!</b> Your Telegram account has been linked to Ditto Dinky.\n\n"
                    f"👤 <b>Username:</b> {linked.username}\n"
                    f"💰 <b>Wallet Balance:</b> ₦{linked.balance:,.2f}\n\n"
                    f"You can now play games directly inside Telegram. Click the button below to start!"
                )
                send_telegram_message(chat_id, welcome_text, reply_markup=play_markup)
            else:
                error_text = (
                    "❌ <b>Link Failed!</b>\n\n"
                    "The link code is invalid, expired, or already used.\n"
                    "Please log in on the website, go to your <b>Profile</b>, and generate a new code."
                )
                send_telegram_message(chat_id, error_text)
        else:
            user = User.query.filter_by(telegram_user_id=str(chat_id)).first()
            if user:
                welcome_text = (
                    f"👋 Welcome back, <b>{user.username}</b>!\n\n"
                    f"💰 <b>Wallet Balance:</b> ₦{user.balance:,.2f}\n\n"
                    f"Click the button below to play your favorite lottery and casino games!"
                )
                send_telegram_message(chat_id, welcome_text, reply_markup=play_markup)
            else:
                instruction_text = (
                    "👋 Welcome to the <b>Ditto Dinky Bot</b>!\n\n"
                    "Ditto Dinky is Nigeria's #1 Instant Lottery & Casino platform.\n\n"
                    "To play using this bot, you must connect your account:\n"
                    "1️⃣ Log in to the website: <code>flowboy.pythonanywhere.com</code>\n"
                    "2️⃣ Go to your <b>Profile</b> page\n"
                    "3️⃣ Generate a <b>Telegram Link Code</b> under Settings\n"
                    "4️⃣ Send <code>/link CODE</code> to this bot\n\n"
                    "💡 <i>Or, click 'Play Now' below, log in/register inside the app, and we will link your Telegram account automatically!</i>"
                )
                send_telegram_message(chat_id, instruction_text, reply_markup=play_markup)
                
    elif text.startswith("/link"):
        parts = text.split()
        if len(parts) > 1:
            token = parts[1].upper()
            linked = _attempt_link(chat_id, token)
            if linked:
                success_text = (
                    f"🎉 <b>Success!</b> Account linked successfully.\n\n"
                    f"👤 <b>Username:</b> {linked.username}\n"
                    f"💰 <b>Wallet:</b> ₦{linked.balance:,.2f}"
                )
                send_telegram_message(chat_id, success_text, reply_markup=play_markup)
            else:
                send_telegram_message(chat_id, "❌ Invalid or expired link code. Try generating a new one.")
        else:
            send_telegram_message(chat_id, "ℹ️ Please specify a link code: <code>/link CODE</code>")
            
    elif text == "/balance":
        user = User.query.filter_by(telegram_user_id=str(chat_id)).first()
        if user:
            send_telegram_message(chat_id, f"💰 Your current balance is: <b>₦{user.balance:,.2f}</b>", reply_markup=play_markup)
        else:
            send_telegram_message(chat_id, "❌ Your Telegram account is not linked. Send <code>/link CODE</code> first.")
            
    elif text == "/help":
        help_text = (
            "<b>Available Bot Commands:</b>\n\n"
            "🎮 /start - Greet and launch the game\n"
            "🔗 /link <code>CODE</code> - Connect your website account\n"
            "💰 /balance - Check your wallet balance\n"
            "ℹ️ /help - Show this command list"
        )
        send_telegram_message(chat_id, help_text, reply_markup=play_markup)
        
    else:
        user = User.query.filter_by(telegram_user_id=str(chat_id)).first()
        reply = "Click the button below to launch the game hub and start playing!"
        if user:
            reply = f"Hello <b>{user.username}</b>! {reply}"
        send_telegram_message(chat_id, reply, reply_markup=play_markup)
        
    return jsonify({"status": "processed"}), 200

@csrf.exempt
@telegram_bp.route("/auth/telegram-login", methods=["POST"])
def telegram_login():
    data = request.get_json() or {}
    init_data = data.get("initData")
    
    bot_token = get_setting("TELEGRAM_BOT_TOKEN") or current_app.config.get("TELEGRAM_BOT_TOKEN")
    
    if not init_data or not bot_token:
        return jsonify({"success": False, "error": "Missing parameters"}), 400
        
    verified = verify_telegram_init_data(init_data, bot_token)
    if not verified:
        return jsonify({"success": False, "error": "Invalid signature"}), 401
        
    user_str = verified.get("user")
    if not user_str:
        return jsonify({"success": False, "error": "No user data found"}), 400
        
    try:
        tg_user = json.loads(user_str)
        tg_user_id = tg_user.get("id")
    except Exception:
        return jsonify({"success": False, "error": "Invalid user JSON"}), 400
        
    if not tg_user_id:
        return jsonify({"success": False, "error": "No Telegram User ID"}), 400
        
    user = User.query.filter_by(telegram_user_id=str(tg_user_id)).first()
    if user:
        if not user.is_active or user.is_banned:
            return jsonify({"success": False, "error": "Account is inactive or banned"}), 403
            
        login_user(user, remember=True)
        return jsonify({
            "success": True,
            "username": user.username,
            "redirect": url_for("game.home")
        })
        
    return jsonify({
        "success": False,
        "reason": "not_linked",
        "tg_user_id": tg_user_id
    }), 404

@telegram_bp.route("/link-token", methods=["POST"])
@login_required
def generate_link_token():
    import string
    chars = string.ascii_uppercase + string.digits
    chars = "".join(c for c in chars if c not in ("I", "O", "0", "1"))
    
    token = "".join(secrets.choice(chars) for _ in range(6))
    
    current_user.telegram_link_token = token
    current_user.telegram_link_expires = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    
    flash("Link code generated! Send it to the Telegram Bot.", "success")
    return redirect(url_for("profile.index"))

@telegram_bp.route("/unlink", methods=["POST"])
@login_required
def unlink_telegram():
    current_user.telegram_user_id = None
    current_user.telegram_link_token = None
    current_user.telegram_link_expires = None
    db.session.commit()
    
    flash("Telegram account unlinked.", "success")
    return redirect(url_for("profile.index"))

@telegram_bp.route("/setup-webhook", methods=["GET", "POST"])
def setup_webhook():
    bot_token = get_setting("TELEGRAM_BOT_TOKEN") or current_app.config.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return "Telegram Bot Token is not configured. Configure it in settings or .env file.", 400
        
    host = request.host_url
    if host.startswith("http://"):
        host = host.replace("http://", "https://")
    webhook_url = f"{host.rstrip('/')}/telegram/webhook"
    
    api_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    try:
        resp = requests.post(api_url, json={"url": webhook_url}, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return f"Webhook successfully set to: {webhook_url}", 200
        else:
            return f"Telegram API error: {data.get('description')}", 400
    except Exception as e:
        return f"Request failed: {e}", 500
