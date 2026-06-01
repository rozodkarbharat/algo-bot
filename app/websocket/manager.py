"""
WebSocket connection manager.

Manages connected clients and provides broadcast / targeted send utilities.
Used for:
  - Live market tick feeds to the React dashboard
  - Real-time order/signal updates
  - System alerts and notifications

Each client connection is identified by a unique client_id.
Room-based grouping (e.g. "market:NIFTY", "orders") is supported so the
frontend can subscribe to specific data streams.
"""

import json
from collections import defaultdict
from typing import Any, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.utils.logger import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """
    Thread-safe (single-process asyncio) WebSocket connection manager.

    Rooms allow targeted broadcast:
      - connect(ws, client_id, room="market:NIFTY50")
      - broadcast_to_room({"tick": ...}, room="market:NIFTY50")
    """

    def __init__(self) -> None:
        # client_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        # room_name → set of client_ids
        self._rooms: dict[str, set[str]] = defaultdict(set)

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(
        self,
        websocket: WebSocket,
        client_id: str,
        room: Optional[str] = None,
    ) -> None:
        """Accept a WebSocket handshake and register the client."""
        await websocket.accept()
        self._connections[client_id] = websocket
        if room:
            self._rooms[room].add(client_id)
        logger.info("WebSocket connected: client_id=%s, room=%s", client_id, room)

    def disconnect(self, client_id: str, room: Optional[str] = None) -> None:
        """Remove a client from the registry and optionally from a room."""
        self._connections.pop(client_id, None)
        if room:
            self._rooms[room].discard(client_id)
        # Clean up empty rooms
        if room and not self._rooms[room]:
            del self._rooms[room]
        logger.info("WebSocket disconnected: client_id=%s", client_id)

    # ── Sending ───────────────────────────────────────────────────────────────

    async def send_to(self, client_id: str, data: Any) -> bool:
        """
        Send JSON-serialisable data to a single client.
        Returns False if the client is not found or send fails.
        """
        websocket = self._connections.get(client_id)
        if websocket is None:
            return False
        return await self._safe_send(websocket, client_id, data)

    async def broadcast(self, data: Any) -> None:
        """Send data to all connected clients."""
        payload = _serialise(data)
        disconnected: list[str] = []
        for client_id, ws in self._connections.items():
            sent = await self._safe_send(ws, client_id, payload, pre_serialised=True)
            if not sent:
                disconnected.append(client_id)
        for cid in disconnected:
            self.disconnect(cid)

    async def broadcast_to_room(self, data: Any, room: str) -> None:
        """Send data only to clients subscribed to a specific room."""
        client_ids = list(self._rooms.get(room, set()))
        if not client_ids:
            return
        payload = _serialise(data)
        disconnected: list[str] = []
        for client_id in client_ids:
            ws = self._connections.get(client_id)
            if ws is None:
                disconnected.append(client_id)
                continue
            sent = await self._safe_send(ws, client_id, payload, pre_serialised=True)
            if not sent:
                disconnected.append(client_id)
        for cid in disconnected:
            self.disconnect(cid, room=room)

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    def get_room_members(self, room: str) -> set[str]:
        return set(self._rooms.get(room, set()))

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    async def _safe_send(
        websocket: WebSocket,
        client_id: str,
        data: Any,
        pre_serialised: bool = False,
    ) -> bool:
        """Send data, returning False on any error."""
        try:
            if websocket.client_state != WebSocketState.CONNECTED:
                return False
            text = data if pre_serialised else _serialise(data)
            await websocket.send_text(text)
            return True
        except Exception as exc:
            logger.warning("Failed to send to client_id=%s: %s", client_id, exc)
            return False


def _serialise(data: Any) -> str:
    """Convert data to a JSON string, handling non-serialisable types gracefully."""
    if isinstance(data, str):
        return data
    return json.dumps(data, default=str)


# Module-level singleton used throughout the application.
ws_manager = ConnectionManager()
