"""
WebSocket endpoint definitions.

Clients connect here to receive real-time market data, signals, and order
updates pushed by the backend.

Endpoint layout:
  ws://host/ws/market/{symbol}   — market tick feed for a symbol
  ws://host/ws/signals           — strategy signal broadcast
  ws://host/ws/orders            — order status updates

Authentication (to be added): validate a JWT token passed as a query
parameter ?token=<jwt> before calling ws_manager.connect().
"""

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket.manager import ws_manager
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/market/{symbol}")
async def market_feed(websocket: WebSocket, symbol: str) -> None:
    """
    Stream live market ticks for `symbol` to the connected client.

    The backend's market data service broadcasts to room "market:{symbol}".
    Each new subscriber automatically starts receiving ticks.
    """
    client_id = str(uuid.uuid4())
    room = f"market:{symbol.upper()}"

    await ws_manager.connect(websocket, client_id=client_id, room=room)
    logger.info("Client %s subscribed to %s", client_id, room)

    try:
        # Keep connection alive; the backend pushes data via broadcast_to_room.
        while True:
            # Receive (and discard) any ping/pong or client messages.
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id, room=room)
        logger.info("Client %s disconnected from %s", client_id, room)


@router.websocket("/ws/signals")
async def signals_feed(websocket: WebSocket) -> None:
    """
    Broadcast channel for strategy-generated signals.
    All connected clients receive every signal in real time.
    """
    client_id = str(uuid.uuid4())
    await ws_manager.connect(websocket, client_id=client_id, room="signals")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id, room="signals")


@router.websocket("/ws/orders")
async def orders_feed(websocket: WebSocket) -> None:
    """
    Real-time order status updates broadcast channel.
    """
    client_id = str(uuid.uuid4())
    await ws_manager.connect(websocket, client_id=client_id, room="orders")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id, room="orders")


@router.websocket("/ws/live/market-state")
async def live_market_state_feed(websocket: WebSocket) -> None:
    """
    Live engine lifecycle events broadcast channel.

    Publishes live.engine.started / live.engine.stopped events and any other
    high-level live-engine state changes. Per-symbol candle/breakout updates
    are still published on the room "market:{symbol}".
    """
    client_id = str(uuid.uuid4())
    await ws_manager.connect(websocket, client_id=client_id, room="live:market-state")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id, room="live:market-state")


# ── Paper trading ─────────────────────────────────────────────────────────────

async def _paper_feed(websocket: WebSocket, room: str) -> None:
    client_id = str(uuid.uuid4())
    await ws_manager.connect(websocket, client_id=client_id, room=room)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id, room=room)


@router.websocket("/ws/paper/trades")
async def paper_trades_feed(websocket: WebSocket) -> None:
    """Paper trade open/close events broadcast channel."""
    await _paper_feed(websocket, "paper:trades")


@router.websocket("/ws/paper/positions")
async def paper_positions_feed(websocket: WebSocket) -> None:
    """Paper position MTM tick + open/close events broadcast channel."""
    await _paper_feed(websocket, "paper:positions")


@router.websocket("/ws/paper/pnl")
async def paper_pnl_feed(websocket: WebSocket) -> None:
    """Paper trading PnL snapshot broadcast channel."""
    await _paper_feed(websocket, "paper:pnl")


@router.websocket("/ws/paper/account")
async def paper_account_feed(websocket: WebSocket) -> None:
    """Paper trading account state broadcast channel."""
    await _paper_feed(websocket, "paper:account")


# ── Notifications ─────────────────────────────────────────────────────────────

@router.websocket("/ws/notifications")
async def notifications_feed(websocket: WebSocket) -> None:
    """
    Real-time notification event stream.

    Receives all alert events: trade entries/exits, SL hits, signals,
    system errors, daily summaries. Connect to this room in the dashboard
    to display a live notification panel.
    """
    client_id = str(uuid.uuid4())
    await ws_manager.connect(websocket, client_id=client_id, room="notifications")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id, room="notifications")
