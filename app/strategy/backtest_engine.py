"""
Historical replay engine for the One-Side ORB strategy.

Pure Python — NO database calls, NO broker imports, NO I/O.
Receives pre-fetched data structures and replays the strategy over the
specified date range, returning SimulatedTrade results.

Strategy replay logic:
  For each trading day D in [from_date, to_date]:
    For each symbol S in the universe:
      1. Look up yesterday's (D-1) OneSideDay record for S.
      2. If yesterday was NOT a one-side day → skip S today.
      3. Look up continuation_probability for S.
      4. If probability < threshold → skip S today.
      5. Get today's (D) candles for S.
      6. Extract first 15-min candle (ORB).
      7. If first candle range > max_orb_range_pct → skip (SL too wide).
      8. Build TradeSetup from yesterday's direction + today's ORB.
      9. Run TradeSimulator on today's candles.
      10. Collect SimulatedTrade result.

Performance design:
  - All data is pre-fetched by BacktestService and passed as in-memory dicts.
  - The engine loops over dates/symbols without any I/O.
  - CPU-bound calculation — BacktestService runs this in a thread-pool
    executor so it doesn't block the event loop.

Scalability:
  - Multi-strategy: subclass BacktestEngine and override _build_trade_setup().
  - Distributed: split symbol list across workers; merge SimulatedTrade lists.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from app.models.backtest_trade import TradeSide
from app.models.historical_candle import CandleData
from app.strategy.trade_simulator import SimulatedTrade, TradeSetup, TradeSimulator
from app.utils.logger import get_logger
from app.utils.trading_day import get_trading_days

logger = get_logger(__name__)

# IST offset in minutes: IST = UTC+5:30 = 330 min ahead of UTC
_IST_OFFSET_MINUTES = 330


@dataclass
class BacktestConfig:
    """
    Complete configuration for a One-Side ORB backtest run.

    All parameters are serialisable to dict so they can be stored in the
    BacktestRun.configuration field for reproducibility.
    """

    # Date range
    from_date: date
    to_date: date

    # Symbol scope — None = use whatever symbols are passed to the engine
    symbols: Optional[list[str]] = None

    # Strategy parameters
    probability_threshold: float = 0.70      # min continuation probability
    min_move_percent: float = 1.0            # OSD detection threshold (not re-applied here)
    max_orb_range_pct: float = 1.0           # skip if first candle range > this %

    # Entry window — candle OPEN time must be ≤ this (IST HH:MM string)
    max_entry_time_ist: str = "11:30"        # → 06:00 UTC

    # Capital / cost
    capital_per_trade: float = 100_000.0     # ₹ per simulated trade
    slippage_pct: float = 0.05               # % slippage on fills
    brokerage_per_side: float = 20.0         # ₹ flat per trade side
    sl_buffer_pct: float = 0.0              # extra SL buffer beyond ORB

    def to_dict(self) -> dict:
        return {
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat(),
            "symbols": self.symbols,
            "probability_threshold": self.probability_threshold,
            "min_move_percent": self.min_move_percent,
            "max_orb_range_pct": self.max_orb_range_pct,
            "max_entry_time_ist": self.max_entry_time_ist,
            "capital_per_trade": self.capital_per_trade,
            "slippage_pct": self.slippage_pct,
            "brokerage_per_side": self.brokerage_per_side,
            "sl_buffer_pct": self.sl_buffer_pct,
        }


@dataclass
class BacktestEngineResult:
    """Aggregate output returned by BacktestEngine.run()."""

    trades: list[SimulatedTrade] = field(default_factory=list)
    total_candidate_days: int = 0   # days where setup was valid
    total_no_data_days: int = 0     # days skipped due to missing candles
    symbols_processed: list[str] = field(default_factory=list)
    trading_days_processed: int = 0


# Type aliases for the pre-fetched data structures
OsdHistory = dict[str, dict[str, Optional[dict]]]
# osd_history[symbol]["YYYY-MM-DD"] = {"is_one_side": bool, "direction": str|None}

CandleHistory = dict[str, dict[str, list[CandleData]]]
# candle_history[symbol]["YYYY-MM-DD"] = [CandleData, ...]


class BacktestEngine:
    """
    Replays the One-Side ORB strategy over historical data.

    Usage:
        engine = BacktestEngine(config)
        result = engine.run(
            symbols=["RELIANCE", "TCS", ...],
            prob_scores={"RELIANCE": 0.72, "TCS": 0.68, ...},
            osd_history=osd_history_dict,
            candle_history=candle_history_dict,
        )

    Note: run() is synchronous (pure Python). Call from asyncio via
    asyncio.get_event_loop().run_in_executor(None, engine.run, ...).
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._simulator = TradeSimulator()
        # Parse max entry time IST → UTC offset
        self._entry_end_utc_hour, self._entry_end_utc_minute = self._parse_entry_time(
            config.max_entry_time_ist
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        symbols: list[str],
        prob_scores: dict[str, float],
        osd_history: OsdHistory,
        candle_history: CandleHistory,
    ) -> BacktestEngineResult:
        """
        Execute the full backtest replay.

        Args:
            symbols:       List of symbols to backtest.
            prob_scores:   symbol → continuation_probability mapping.
            osd_history:   symbol → date_str → OSD record dict.
            candle_history: symbol → date_str → candle list.

        Returns:
            BacktestEngineResult with all SimulatedTrade records.
        """
        result = BacktestEngineResult(symbols_processed=list(symbols))
        trading_days = get_trading_days(self._config.from_date, self._config.to_date)
        result.trading_days_processed = len(trading_days)

        logger.info(
            "BacktestEngine starting: %d symbols × %d trading days",
            len(symbols),
            len(trading_days),
        )

        for trading_date in trading_days:
            day_trades = self._process_day(
                trading_date=trading_date,
                symbols=symbols,
                prob_scores=prob_scores,
                osd_history=osd_history,
                candle_history=candle_history,
                result=result,
            )
            result.trades.extend(day_trades)

        logger.info(
            "BacktestEngine complete: %d candidate days → %d trades (%d no-entry, %d no-data)",
            result.total_candidate_days,
            sum(1 for t in result.trades if t.exit_reason.value != "NO_BREAKOUT"),
            sum(1 for t in result.trades if t.exit_reason.value == "NO_BREAKOUT"),
            result.total_no_data_days,
        )
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _process_day(
        self,
        trading_date: date,
        symbols: list[str],
        prob_scores: dict[str, float],
        osd_history: OsdHistory,
        candle_history: CandleHistory,
        result: BacktestEngineResult,
    ) -> list[SimulatedTrade]:
        """Process all symbols for a single trading day. Returns that day's trades."""
        day_trades: list[SimulatedTrade] = []
        date_str = trading_date.isoformat()

        # Find the previous trading day (needed for OSD lookup)
        prev_date_str = self._previous_trading_day_str(trading_date, osd_history, symbols)

        for symbol in symbols:
            # ── Gate 1: Yesterday must have been an OSD ─────────────────────
            yesterday_osd = self._get_osd_record(osd_history, symbol, prev_date_str)
            if yesterday_osd is None or not yesterday_osd.get("is_one_side", False):
                continue

            yesterday_direction = yesterday_osd.get("direction")
            if yesterday_direction not in ("UP", "DOWN"):
                continue

            # ── Gate 2: Continuation probability >= threshold ────────────────
            prob = prob_scores.get(symbol, 0.0)
            if prob < self._config.probability_threshold:
                continue

            # ── Gate 3: Today's candles must exist ───────────────────────────
            today_candles = candle_history.get(symbol, {}).get(date_str)
            if not today_candles or len(today_candles) < 2:
                result.total_no_data_days += 1
                continue

            # ── Gate 4: First candle range must be ≤ max_orb_range_pct ───────
            first_candle = today_candles[0]
            if first_candle.low <= 0:
                continue
            orb_range_pct = (first_candle.high - first_candle.low) / first_candle.low * 100.0
            if orb_range_pct > self._config.max_orb_range_pct:
                logger.debug(
                    "[%s] %s: ORB range %.2f%% > %.2f%% — skipping.",
                    symbol, date_str, orb_range_pct, self._config.max_orb_range_pct,
                )
                continue

            # ── Build TradeSetup ─────────────────────────────────────────────
            result.total_candidate_days += 1
            trade_side = TradeSide.LONG if yesterday_direction == "UP" else TradeSide.SHORT
            setup = self._build_trade_setup(
                symbol=symbol,
                trade_side=trade_side,
                breakout_side=yesterday_direction,
                orb_high=first_candle.high,
                orb_low=first_candle.low,
                probability_score=prob,
            )

            # ── Simulate ─────────────────────────────────────────────────────
            trade = self._simulator.simulate(setup, today_candles)
            day_trades.append(trade)

        return day_trades

    def _build_trade_setup(
        self,
        symbol: str,
        trade_side: TradeSide,
        breakout_side: str,
        orb_high: float,
        orb_low: float,
        probability_score: float,
    ) -> TradeSetup:
        """Construct a TradeSetup from the engine config and per-day parameters."""
        return TradeSetup(
            symbol=symbol,
            trade_side=trade_side,
            breakout_side=breakout_side,
            orb_high=orb_high,
            orb_low=orb_low,
            probability_score=probability_score,
            entry_window_end_utc_hour=self._entry_end_utc_hour,
            entry_window_end_utc_minute=self._entry_end_utc_minute,
            sl_buffer_pct=self._config.sl_buffer_pct,
            slippage_pct=self._config.slippage_pct,
            brokerage_per_side=self._config.brokerage_per_side,
            capital_per_trade=self._config.capital_per_trade,
        )

    @staticmethod
    def _get_osd_record(
        osd_history: OsdHistory,
        symbol: str,
        date_str: Optional[str],
    ) -> Optional[dict]:
        """Safely fetch an OSD record from the pre-loaded history dict."""
        if date_str is None:
            return None
        return osd_history.get(symbol, {}).get(date_str)

    @staticmethod
    def _previous_trading_day_str(
        current_date: date,
        osd_history: OsdHistory,
        symbols: list[str],
    ) -> Optional[str]:
        """
        Find the most recent date key in osd_history that is before current_date.

        Returns an ISO date string (YYYY-MM-DD) or None if no prior date exists.
        This approach avoids hardcoding weekend/holiday calendars — it discovers
        the prior trading day from the data that is actually present.
        """
        if not symbols:
            return None

        # Collect all distinct date strings across all symbols in osd_history
        all_dates: set[str] = set()
        for sym in symbols:
            if sym in osd_history:
                all_dates.update(osd_history[sym].keys())

        current_str = current_date.isoformat()
        prior_dates = sorted(d for d in all_dates if d < current_str)
        return prior_dates[-1] if prior_dates else None

    @staticmethod
    def _parse_entry_time(ist_time_str: str) -> tuple[int, int]:
        """
        Convert "HH:MM" (IST) string to (UTC_hour, UTC_minute).

        IST = UTC + 5:30, so to convert IST→UTC: subtract 5h30m.
        """
        try:
            parts = ist_time_str.split(":")
            ist_hour, ist_minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            logger.warning(
                "Invalid max_entry_time_ist '%s'; defaulting to 11:30.", ist_time_str
            )
            ist_hour, ist_minute = 11, 30

        total_utc_minutes = ist_hour * 60 + ist_minute - _IST_OFFSET_MINUTES
        utc_hour = total_utc_minutes // 60
        utc_minute = total_utc_minutes % 60
        return utc_hour, utc_minute
