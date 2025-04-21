import json
from typing import Dict, List, Any

from fastapi import WebSocket


class WebSocketManager:
    """Manager for WebSocket connections"""
    
    def __init__(self):
        """Initialize the manager with an empty list of active connections"""
        self.active_connections: List[WebSocket] = []
        
    async def connect(self, websocket: WebSocket):
        """Connect a new client"""
        await websocket.accept()
        self.active_connections.append(websocket)
        
    def disconnect(self, websocket: WebSocket):
        """Disconnect a client"""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            
    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket):
        """Send a message to a specific client"""
        await websocket.send_text(json.dumps(message))
        
    async def broadcast(self, message: Dict[str, Any]):
        """Send a message to all connected clients"""
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Error broadcasting message: {e}")
                # Remove failed connection
                if connection in self.active_connections:
                    self.active_connections.remove(connection)
    
    async def send_notification(self, title: str, message: str, notification_type: str = "info", download_id: str = None):
        """Send a notification to all connected clients"""
        notification = {
            "type": "notification",
            "notification_type": notification_type,
            "title": title,
            "message": message,
            "timestamp": None  # Will be filled in by the client
        }
        
        if download_id:
            notification["download_id"] = download_id
            
        await self.broadcast(notification)


# Singleton instance for use throughout the app
manager = WebSocketManager()
