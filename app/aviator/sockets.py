"""
Aviator WebSocket handlers (Flask-SocketIO)
Importing this module registers the socket event handlers.
"""
from app.extensions import socketio
from flask_socketio import join_room



@socketio.on("connect")
def handle_connect():
    print("[Aviator WS] Client connected")


@socketio.on("join_aviator_round")
def handle_join_aviator_round(data):
    round_id = data.get("round_id")
    if round_id:
        room = f"aviator_{round_id}"
        join_room(room)
        print(f"[Aviator WS] Client joined room: {room}")