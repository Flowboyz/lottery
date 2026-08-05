"""
Ditto Dinky - Nigerian Lottery Platform
Run with: python run.py
"""
from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(app, debug=True, port=5000)
