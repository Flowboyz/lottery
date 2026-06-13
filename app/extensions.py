"""
Shared Flask extensions - initialized once, imported everywhere.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail
from flask_migrate import Migrate
from flask_socketio import SocketIO

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
mail = Mail()
migrate = Migrate()
socketio = SocketIO(async_mode='gevent')

login_manager.login_view = "auth.login"
login_manager.login_message_category = "error"
