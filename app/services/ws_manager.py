import json
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """Manages WebSocket connections and broadcasts messages to clients"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection"""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: Any, websocket: WebSocket):
        """Send a message to a specific client"""
        if isinstance(message, dict):
            await websocket.send_json(message)
        elif isinstance(message, str):
            await websocket.send_text(message)
        else:
            await websocket.send_json(json.dumps(message))

    async def broadcast(self, message: Any):
        """Send a message to all connected clients"""
        disconnected = []

        # Convert datetime objects to ISO format for JSON serialization
        if isinstance(message, dict):
            message = self._prepare_for_json(message)

        for connection in self.active_connections:
            try:
                if isinstance(message, dict):
                    await connection.send_json(message)
                elif isinstance(message, str):
                    await connection.send_text(message)
                else:
                    await connection.send_json(json.dumps(message))
            except (WebSocketDisconnect, RuntimeError):
                # Mark connection for removal if it's closed or in error state
                disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)

    def _prepare_for_json(self, obj: Any) -> Any:
        """Prepare an object for JSON serialization"""
        if isinstance(obj, dict):
            return {k: self._prepare_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._prepare_for_json(item) for item in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return obj


# Singleton instance
manager = ConnectionManager()
