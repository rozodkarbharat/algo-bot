"""
Live signal service — orchestrates the live engine, persistence and WS broadcast.

Responsibilities:
  1. Pull the day's shortlist from ShortlistService.
  2. Start / stop the LiveMarketEngine for the trading session.
  3. Persist every emitted GeneratedSignal as a LiveSignal document, enforcing
     the unique (symbol, trading_date) constraint.
  4. Mirror per-symbol state into IntradayMarketState rows so it survives
     restarts and is visible via the API.
  5. Broadcast signals, market state and breakout alerts to WebSocket rooms.

The service is the ONLY place that knows about:
  - Repositories (persistence)
  - ws_manager (broadcast)
  - The application-level shortlist (cross-domain orchestration)

Both the live engine pipeline (candles, signals) and the service itself never
touch the broker. Broker integration will live in a future tick-bridge module
that calls `live_market_engine.feed_tick(...)`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.core.exceptions import ValidationException
from app.live.candle_builder import BuiltCandle
from app.live.health_monitor import LiveHealthMonitor, live_health_monitor
from app.live.market_engine import LiveMarketEngine, live_market_engine
from app.live.market_session import MarketSessionEngine
from app.live.signal_engine import GeneratedSignal, ShortlistedCandidate
from app.models.intraday_market_state import IntradayBreakoutSide, IntradayMarketState
from app.models.live_signal import (
    LiveBreakoutSide,
    LiveSignal,
    LiveSignalStatus,
    LiveSignalType,
)
from app.repositories.intraday_market_state_repository import (
    IntradayMarketStateRepository,
)
from app.repositories.live_signal_repository import (
    DuplicateSignalError,
    LiveSignalRepository,
)
from app.services.shortlist_service import ShortlistEntry, ShortlistService
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc
from app.websocket.manager import ws_manager

logger = get_logger(__name__)


# ── WebSocket room names ─────────────────────────────────────────────────────

ROOM_SIGNALS = "signals"
ROOM_LIVE_MARKET_STATE = "live:market-state"


def _market_room(symbol: str) -> str:
    return f"market:{symbol.upper()}"


# ── Result dataclasses ───────────────────────────────────────────────────────

@dataclass
class StartResult:
    started: bool
    trading_date: date
    subscribed_symbols: list[str]
    message: str


@dataclass
class StopResult:
    stopped: bool
    signals_generated: int
    duration_seconds: float
    message: str


# ── Service ──────────────────────────────────────────────────────────────────

class LiveSignalService:
    """
    Application-level coordinator for live signal generation.

    Designed to be instantiated as a module-level singleton; safe because all
    mutable state lives inside the injected engines/repositories.
    """

    def __init__(
        self,
        engine: Optional[LiveMarketEngine] = None,
        shortlist_service: Optional[ShortlistService] = None,
        signal_repo: Optional[LiveSignalRepository] = None,
        state_repo: Optional[IntradayMarketStateRepository] = None,
        session: Optional[MarketSessionEngine] = None,
        health_monitor: Optional[LiveHealthMonitor] = None,
    ) -> None:
        self._engine: LiveMarketEngine = engine or live_market_engine
        self._shortlist_svc: ShortlistService = shortlist_service or ShortlistService()
        self._signal_repo: LiveSignalRepository = signal_repo or LiveSignalRepository()
        self._state_repo: IntradayMarketStateRepository = (
            state_repo or IntradayMarketStateRepository()
        )
        self._session: MarketSessionEngine = session or self._engine.session
        self._health: LiveHealthMonitor = health_monitor or live_health_monitor

        # Wire pipeline hooks once. on_signal handles persist + broadcast.
        # on_candle handles state mirroring + market-state broadcast.
        self._engine.signal_engine.on_signal(self._handle_generated_signal)
        self._engine.candle_builder.on_candle(self._handle_built_candle)

        self._started_at: Optional[datetime] = None
        self._stopped_at: Optional[datetime] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(
        self, target_date: Optional[date] = None
    ) -> StartResult:
        """
        Start the live engine for `target_date` (defaults to today).

        Fetches today's shortlist, registers each symbol, and activates the
        signal engine. Idempotent — if the engine is already running, this
        becomes a no-op (the existing watchlist is preserved).
        """
        if self._engine.running:
            logger.info("LiveSignalService.start called but engine already running.")
            return StartResult(
                started=False,
                trading_date=self._session.current_trading_date(),
                subscribed_symbols=self._engine.watchlist,
                message="Live engine already running.",
            )

        trading_date = target_date or self._session.current_trading_date()
        shortlist = await self._shortlist_svc.generate_shortlist(target_date=trading_date)
        # The shortlist now contains both tradable and skipped rows (skipped
        # rows are surfaced in the UI with a reason); only tradable ones get
        # subscribed in the live engine.
        candidates = [
            _entry_to_candidate(e) for e in shortlist.entries if e.tradable
        ]

        await self._engine.start(candidates)
        self._started_at = now_utc()
        self._stopped_at = None

        # Seed empty IntradayMarketState rows so the API has something to
        # return immediately and reset() at EOD has rows to clean.
        await self._seed_state_rows(trading_date, candidates)

        logger.info(
            "LiveSignalService started for %s with %d shortlisted symbols.",
            trading_date, len(candidates),
        )
        await ws_manager.broadcast_to_room(
            {
                "event": "live.engine.started",
                "trading_date": trading_date.isoformat(),
                "symbols": [c.symbol for c in candidates],
            },
            ROOM_LIVE_MARKET_STATE,
        )
        return StartResult(
            started=True,
            trading_date=trading_date,
            subscribed_symbols=[c.symbol for c in candidates],
            message=f"Live engine started with {len(candidates)} symbols.",
        )

    async def stop(self) -> StopResult:
        """Stop the live engine and surface a clean summary."""
        if not self._engine.running:
            return StopResult(
                stopped=False,
                signals_generated=0,
                duration_seconds=0.0,
                message="Live engine not running.",
            )

        signals_before = self._engine.signal_engine.stats["signals_emitted"]
        t0 = self._started_at or now_utc()
        await self._engine.stop()
        self._stopped_at = now_utc()
        duration = (self._stopped_at - t0).total_seconds()

        await ws_manager.broadcast_to_room(
            {
                "event": "live.engine.stopped",
                "stopped_at": self._stopped_at.isoformat(),
                "signals_generated": signals_before,
                "duration_seconds": round(duration, 2),
            },
            ROOM_LIVE_MARKET_STATE,
        )
        logger.info(
            "LiveSignalService stopped. signals=%d duration=%.1fs",
            signals_before, duration,
        )
        return StopResult(
            stopped=True,
            signals_generated=signals_before,
            duration_seconds=round(duration, 2),
            message="Live engine stopped.",
        )

    async def reset_daily(self) -> int:
        """
        Reset intraday state at session end.

        Called by the scheduler's session-cleanup job at 15:30 IST. Clears
        the in-memory engine state AND deletes today's IntradayMarketState
        rows so tomorrow starts clean.
        """
        if self._engine.running:
            await self.stop()
        self._engine.candle_builder.reset()
        deleted = await self._session.reset_intraday_state()
        logger.info("Daily reset complete: %d IntradayMarketState rows cleared.", deleted)
        return deleted

    # ── Public API used by routes / scheduler ────────────────────────────────

    @property
    def engine(self) -> LiveMarketEngine:
        return self._engine

    @property
    def started_at(self) -> Optional[datetime]:
        return self._started_at

    @property
    def stopped_at(self) -> Optional[datetime]:
        return self._stopped_at

    async def broadcast_health_heartbeat(self) -> dict:
        """
        Build a health snapshot and broadcast it to the live:market-state room.

        Designed to be called periodically by the scheduler so the dashboard
        can colour-code the engine status without polling /api/v1/live/health.
        Returns the broadcast payload (also useful for test assertions).
        """
        snap = self._health.evaluate()
        payload = {
            "event": "live.health",
            "status": snap.status.value,
            "running": snap.running,
            "market_open": snap.market_open,
            "entry_window_open": snap.entry_window_open,
            "ticks_received": snap.ticks_received,
            "ticks_dropped": snap.ticks_dropped,
            "candles_emitted": snap.candles_emitted,
            "signals_emitted": snap.signals_emitted,
            "reconnect_count": snap.reconnect_count,
            "seconds_since_last_tick": snap.seconds_since_last_tick,
            "stale_symbols": snap.stale_symbols,
            "notes": snap.notes,
        }
        await ws_manager.broadcast_to_room(payload, ROOM_LIVE_MARKET_STATE)
        return payload

    async def note_broker_reconnect(self) -> None:
        """
        Pass-through for broker bridges: increments the reconnect counter and
        broadcasts a `live.reconnect` event so the UI can warn the user.
        """
        await self._engine.note_reconnect()
        await ws_manager.broadcast_to_room(
            {
                "event": "live.reconnect",
                "reconnect_count": self._engine.stats.reconnect_count,
                "at": now_utc().isoformat(),
            },
            ROOM_LIVE_MARKET_STATE,
        )

    async def status_snapshot(self) -> dict:
        """Return a JSON-ready high-level engine status."""
        trading_date = self._session.current_trading_date()
        trading_dt = date_to_utc_midnight(trading_date)

        signals_today = await self._signal_repo.count_for_date(trading_dt)
        locked_rows = await self._state_repo.get_locked_for_date(trading_dt)
        snapshot = self._session.snapshot()

        return {
            "running": self._engine.running,
            "subscribed_symbols": self._engine.watchlist,
            "signals_today": signals_today,
            "trade_locked_today": len(locked_rows),
            "session_active": snapshot.entry_window_open,
            "market_open": snapshot.is_market_open,
            "last_started_at": self._started_at,
            "last_stopped_at": self._stopped_at,
        }

    # ── Internal: engine pipeline hooks ──────────────────────────────────────

    async def _handle_generated_signal(self, gen: GeneratedSignal) -> None:
        """Persist a generated signal, lock the symbol, broadcast."""
        signal = _signal_doc_from_generated(gen)
        try:
            persisted = await self._signal_repo.insert_unique(signal)
        except DuplicateSignalError as exc:
            # Race / duplicate suppression. Lock the symbol so we stop emitting
            # any further on it today, but do NOT broadcast.
            self._engine.signal_engine.lock_symbol(gen.symbol)
            logger.warning(
                "Duplicate signal suppressed for %s on %s.",
                gen.symbol, gen.trading_date,
            )
            return
        except ValidationException as exc:
            logger.warning("Signal rejected: %s", exc.message)
            return

        # Hard-lock to mirror DB invariant into the engine state.
        self._engine.signal_engine.lock_symbol(gen.symbol)

        # Update intraday state row: breakout + signal + locked.
        await self._update_state_for_signal(gen, signal_id=persisted.signal_id)

        # Mark broadcast status and ship to WS.
        await self._signal_repo.update_status(persisted.signal_id, LiveSignalStatus.BROADCAST)
        payload = _signal_to_ws_payload(persisted)
        await ws_manager.broadcast_to_room(payload, ROOM_SIGNALS)
        await ws_manager.broadcast_to_room(
            {**payload, "event": "live.breakout"},
            _market_room(gen.symbol),
        )

        logger.info(
            "[%s] LiveSignal persisted+broadcast: %s @ %.2f (SL %.2f)",
            gen.symbol, gen.signal_type, gen.entry_price, gen.stop_loss,
        )

    async def _handle_built_candle(self, candle: BuiltCandle) -> None:
        """
        Mirror ORB completion into IntradayMarketState and broadcast a tick of
        live market state for the symbol. Only acts on 15-min candles.
        """
        from app.utils.candle_intervals import CandleInterval

        if candle.interval is not CandleInterval.FIFTEEN_MINUTE:
            return

        symbol_state = self._engine.signal_engine.get_symbol_state(candle.symbol)
        if symbol_state is None:
            return  # not a shortlisted symbol — nothing to mirror

        trading_dt = date_to_utc_midnight(symbol_state.trading_date)
        row = await self._state_repo.get(candle.symbol, trading_dt) or IntradayMarketState(
            symbol=candle.symbol,
            trading_date=trading_dt,
        )

        # Reflect the engine's view onto the row.
        if symbol_state.first_candle is not None:
            row.first_candle_completed = True
            row.first_candle_high = symbol_state.orb_high
            row.first_candle_low = symbol_state.orb_low
            row.first_candle_range_percent = symbol_state.orb_range_percent
            if symbol_state.orb_skipped_reason:
                row.metadata["orb_skipped_reason"] = symbol_state.orb_skipped_reason

        row.signal_generated = symbol_state.signal_emitted_at is not None
        row.trade_locked = symbol_state.trade_locked
        row.mark_updated()
        await self._state_repo.upsert(row)

        # Lightweight per-symbol market state broadcast (close + ORB context).
        await ws_manager.broadcast_to_room(
            {
                "event": "live.candle",
                "symbol": candle.symbol,
                "interval": str(candle.interval),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "start_time": candle.start_time.isoformat(),
                "end_time": candle.end_time.isoformat(),
                "orb_high": row.first_candle_high,
                "orb_low": row.first_candle_low,
                "orb_range_percent": row.first_candle_range_percent,
                "first_candle_completed": row.first_candle_completed,
                "trade_locked": row.trade_locked,
            },
            _market_room(candle.symbol),
        )

    # ── Internal: seeding ─────────────────────────────────────────────────────

    async def _seed_state_rows(
        self,
        trading_date: date,
        candidates: list[ShortlistedCandidate],
    ) -> None:
        """Create empty IntradayMarketState rows for the shortlist."""
        trading_dt = date_to_utc_midnight(trading_date)
        rows: list[IntradayMarketState] = []
        for c in candidates:
            existing = await self._state_repo.get(c.symbol, trading_dt)
            if existing is not None:
                continue
            rows.append(
                IntradayMarketState(
                    symbol=c.symbol,
                    trading_date=trading_dt,
                    metadata={
                        "probability": c.probability,
                        "direction_hint": c.direction_hint,
                    },
                )
            )
        if rows:
            await self._state_repo.bulk_upsert(rows)

    async def _update_state_for_signal(
        self, gen: GeneratedSignal, signal_id: str
    ) -> None:
        trading_dt = date_to_utc_midnight(gen.trading_date)
        row = await self._state_repo.get(gen.symbol, trading_dt) or IntradayMarketState(
            symbol=gen.symbol,
            trading_date=trading_dt,
        )
        row.first_candle_completed = True
        row.first_candle_high = gen.first_candle_high
        row.first_candle_low = gen.first_candle_low
        row.first_candle_range_percent = gen.orb_range_percent
        row.breakout_detected = True
        row.breakout_side = (
            IntradayBreakoutSide.UP
            if gen.breakout_side is LiveBreakoutSide.UP
            else IntradayBreakoutSide.DOWN
        )
        row.signal_generated = True
        row.trade_locked = True
        row.signal_id = signal_id
        row.mark_updated()
        await self._state_repo.upsert(row)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _entry_to_candidate(entry: ShortlistEntry) -> ShortlistedCandidate:
    return ShortlistedCandidate(
        symbol=entry.symbol,
        probability=entry.continuation_probability,
        direction_hint=entry.direction,
    )


def _signal_doc_from_generated(gen: GeneratedSignal) -> LiveSignal:
    """Map the engine's value object to a persistable Beanie document."""
    return LiveSignal(
        symbol=gen.symbol,
        trading_date=date_to_utc_midnight(gen.trading_date),
        signal_type=gen.signal_type,
        breakout_side=gen.breakout_side,
        entry_price=gen.entry_price,
        stop_loss=gen.stop_loss,
        first_candle_high=gen.first_candle_high,
        first_candle_low=gen.first_candle_low,
        orb_range_percent=gen.orb_range_percent,
        breakout_time=gen.breakout_time,
        probability_score=gen.probability_score,
        strategy_id=gen.strategy_id,
        strategy_name=gen.strategy_name,
        strategy_version=gen.strategy_version,
        metadata=dict(gen.metadata),
    )


def _signal_to_ws_payload(signal: LiveSignal) -> dict:
    return {
        "event": "live.signal",
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "trading_date": signal.trading_date.date().isoformat(),
        "signal_type": signal.signal_type.value,
        "breakout_side": signal.breakout_side.value,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "first_candle_high": signal.first_candle_high,
        "first_candle_low": signal.first_candle_low,
        "orb_range_percent": signal.orb_range_percent,
        "breakout_time": signal.breakout_time.isoformat(),
        "probability_score": signal.probability_score,
    }


# ── Module-level singleton ───────────────────────────────────────────────────

live_signal_service: LiveSignalService = LiveSignalService()
