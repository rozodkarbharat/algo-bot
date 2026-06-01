"""
Unit tests for StockAnalyticsEngine, TimeAnalyticsEngine,
MarketConditionAnalyticsEngine, and FailureAnalyticsEngine.

Tests verify:
  - Per-symbol metrics (win rate, SL hit rate, expectancy)
  - Tradability score ranking
  - Time bucket classification and win-rate trend
  - Market condition day classification
  - Failure pattern detection: fake breakouts, SL clustering, choppy days

All tests are pure Python — no database, no I/O.

Run with:
    pytest tests/test_stock_analytics.py -v
"""

from datetime import datetime, timezone

import pytest

from app.models.backtest_trade import ExitReason, TradeSide
from app.research.failure_analytics import FailureAnalyticsEngine
from app.research.market_condition_analytics import MarketConditionAnalyticsEngine
from app.research.stock_analytics import StockAnalyticsEngine, _MIN_TRADES_FOR_RANKING
from app.research.time_analytics import TimeAnalyticsEngine
from app.strategy.trade_simulator import SimulatedTrade


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trade(
    symbol: str = "RELIANCE",
    pnl: float = 100.0,
    exit_reason: ExitReason = ExitReason.EOD_EXIT,
    side: TradeSide = TradeSide.LONG,
    entry_time: datetime = datetime(2024, 1, 3, 4, 15, tzinfo=timezone.utc),
    exit_time: datetime = datetime(2024, 1, 3, 9, 45, tzinfo=timezone.utc),
    orb_high: float = 2600.0,
    orb_low: float = 2560.0,
    entry_price: float = 2602.0,
    exit_price: float = 2650.0,
    probability_score: float = 0.72,
) -> SimulatedTrade:
    rr = (exit_price - entry_price) / (entry_price - orb_low) if entry_price != orb_low else None
    return SimulatedTrade(
        symbol=symbol,
        trade_side=side,
        breakout_side="UP" if side == TradeSide.LONG else "DOWN",
        orb_high=orb_high,
        orb_low=orb_low,
        probability_score=probability_score,
        entry_time=entry_time,
        entry_price=entry_price,
        stop_loss=orb_low,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=exit_reason,
        quantity=38,
        capital_used=98_876.0,
        pnl=pnl,
        pnl_percent=pnl / 98_876.0 * 100,
        risk_reward=rr,
    )


def _no_breakout(symbol: str = "TCS") -> SimulatedTrade:
    return SimulatedTrade(
        symbol=symbol,
        trade_side=TradeSide.LONG,
        breakout_side="UP",
        orb_high=3500.0,
        orb_low=3450.0,
        probability_score=0.65,
        entry_time=None,
        entry_price=None,
        stop_loss=3450.0,
        exit_time=None,
        exit_price=None,
        exit_reason=ExitReason.NO_BREAKOUT,
        quantity=0,
        capital_used=0.0,
        pnl=0.0,
        pnl_percent=0.0,
        risk_reward=None,
    )


# ── StockAnalyticsEngine tests ─────────────────────────────────────────────────

class TestStockAnalyticsEngine:
    def test_empty_trades_returns_empty_result(self):
        engine = StockAnalyticsEngine()
        result = engine.analyse([])
        assert result.symbol_analytics == []
        assert result.top_performers == []

    def test_single_win_trade(self):
        engine = StockAnalyticsEngine()
        trade = _trade("RELIANCE", pnl=500.0, exit_reason=ExitReason.EOD_EXIT)
        result = engine.analyse([trade])
        analytics = next(a for a in result.symbol_analytics if a.symbol == "RELIANCE")
        assert analytics.total_trades == 1
        assert analytics.winning_trades == 1
        assert analytics.win_rate == 1.0
        assert analytics.total_pnl == 500.0

    def test_win_rate_calculation(self):
        engine = StockAnalyticsEngine()
        trades = [
            _trade("TCS", pnl=200.0),
            _trade("TCS", pnl=300.0),
            _trade("TCS", pnl=-100.0),
            _trade("TCS", pnl=-50.0),
        ]
        result = engine.analyse(trades)
        a = next(x for x in result.symbol_analytics if x.symbol == "TCS")
        assert a.total_trades == 4
        assert a.win_rate == 0.5
        assert a.total_pnl == pytest.approx(350.0)

    def test_sl_hit_rate(self):
        engine = StockAnalyticsEngine()
        trades = [
            _trade("INFY", pnl=-80.0, exit_reason=ExitReason.SL_HIT),
            _trade("INFY", pnl=-70.0, exit_reason=ExitReason.SL_HIT),
            _trade("INFY", pnl=200.0, exit_reason=ExitReason.EOD_EXIT),
            _trade("INFY", pnl=150.0, exit_reason=ExitReason.EOD_EXIT),
        ]
        result = engine.analyse(trades)
        a = next(x for x in result.symbol_analytics if x.symbol == "INFY")
        assert a.sl_hit_rate == 0.5

    def test_breakout_success_rate_includes_no_breakout(self):
        engine = StockAnalyticsEngine()
        trades = [
            _trade("HDFC", pnl=100.0),
            _trade("HDFC", pnl=200.0),
            _no_breakout("HDFC"),
            _no_breakout("HDFC"),
        ]
        result = engine.analyse(trades)
        a = next(x for x in result.symbol_analytics if x.symbol == "HDFC")
        # 2 executed / 4 total candidates = 0.5
        assert a.breakout_success_rate == pytest.approx(0.5)

    def test_expectancy_formula(self):
        engine = StockAnalyticsEngine()
        # 3 wins avg ₹200, 2 losses avg -₹100 → win_rate=0.6, loss_rate=0.4
        # expectancy = 0.6*200 - 0.4*100 = 120 - 40 = 80
        trades = [
            _trade("WIPRO", pnl=200.0),
            _trade("WIPRO", pnl=200.0),
            _trade("WIPRO", pnl=200.0),
            _trade("WIPRO", pnl=-100.0),
            _trade("WIPRO", pnl=-100.0),
        ]
        result = engine.analyse(trades)
        a = next(x for x in result.symbol_analytics if x.symbol == "WIPRO")
        assert a.expectancy == pytest.approx(80.0, abs=1.0)

    def test_min_trade_filter_for_rankings(self):
        engine = StockAnalyticsEngine()
        # Only 1 trade — below _MIN_TRADES_FOR_RANKING
        trades = [_trade("RARE", pnl=999.0)]
        result = engine.analyse(trades)
        symbols_in_rankings = [a.symbol for a in result.top_performers]
        assert "RARE" not in symbols_in_rankings

    def test_top_performers_sorted_by_tradability_score(self):
        engine = StockAnalyticsEngine()
        # Build 2 symbols with enough trades
        trades = (
            [_trade("GOOD", pnl=500.0) for _ in range(_MIN_TRADES_FOR_RANKING + 1)]
            + [_trade("BAD", pnl=-200.0) for _ in range(_MIN_TRADES_FOR_RANKING + 1)]
        )
        result = engine.analyse(trades)
        if len(result.top_performers) >= 2:
            scores = [a.tradability_score for a in result.top_performers]
            assert scores == sorted(scores, reverse=True)

    def test_max_drawdown_is_non_negative(self):
        engine = StockAnalyticsEngine()
        trades = [
            _trade("SBIN", pnl=500.0),
            _trade("SBIN", pnl=-800.0),
            _trade("SBIN", pnl=200.0),
        ]
        result = engine.analyse(trades)
        a = next(x for x in result.symbol_analytics if x.symbol == "SBIN")
        assert a.max_drawdown >= 0.0


# ── TimeAnalyticsEngine tests ──────────────────────────────────────────────────

class TestTimeAnalyticsEngine:
    def _early_trade(self, symbol: str = "X", pnl: float = 100.0) -> SimulatedTrade:
        # 09:30 IST = 04:00 UTC
        return _trade(
            symbol=symbol,
            pnl=pnl,
            entry_time=datetime(2024, 1, 3, 4, 5, tzinfo=timezone.utc),
        )

    def _late_trade(self, symbol: str = "X", pnl: float = -50.0) -> SimulatedTrade:
        # 11:00 IST = 05:30 UTC
        return _trade(
            symbol=symbol,
            pnl=pnl,
            entry_time=datetime(2024, 1, 3, 5, 35, tzinfo=timezone.utc),
        )

    def test_empty_trades_returns_empty_buckets(self):
        engine = TimeAnalyticsEngine()
        result = engine.analyse([])
        assert result.best_bucket is None
        assert result.worst_bucket is None

    def test_no_breakout_trades_excluded(self):
        engine = TimeAnalyticsEngine()
        trades = [_no_breakout("X")] * 5
        result = engine.analyse(trades)
        for bucket in result.buckets:
            assert bucket.total_entries == 0

    def test_early_trades_go_into_first_bucket(self):
        engine = TimeAnalyticsEngine()
        trades = [self._early_trade() for _ in range(5)]
        result = engine.analyse(trades)
        first_bucket = result.buckets[0]
        assert first_bucket.label == "09:30–10:00"
        assert first_bucket.total_entries == 5

    def test_win_rate_per_bucket(self):
        engine = TimeAnalyticsEngine()
        # 3 wins, 1 loss in 09:30-10:00 bucket
        trades = (
            [self._early_trade(pnl=100.0) for _ in range(3)]
            + [self._early_trade(pnl=-50.0)]
        )
        result = engine.analyse(trades)
        first = result.buckets[0]
        assert first.win_rate == pytest.approx(0.75)

    def test_best_bucket_is_highest_win_rate(self):
        engine = TimeAnalyticsEngine()
        # Early bucket: 4 wins (100% win rate)
        # Late bucket: 1 win, 2 losses (~33%)
        trades = (
            [self._early_trade(pnl=200.0) for _ in range(4)]
            + [self._late_trade(pnl=100.0)]
            + [self._late_trade(pnl=-50.0)]
            + [self._late_trade(pnl=-80.0)]
        )
        result = engine.analyse(trades)
        assert result.best_bucket == "09:30–10:00"


# ── MarketConditionAnalyticsEngine tests ──────────────────────────────────────

class TestMarketConditionAnalyticsEngine:
    def test_empty_trades_returns_empty_result(self):
        engine = MarketConditionAnalyticsEngine()
        result = engine.analyse([])
        assert result.day_profiles == []

    def test_gap_up_day_when_all_longs(self):
        engine = MarketConditionAnalyticsEngine()
        # 5 long signals on one day → gap_up (>70% long)
        trades = [
            _trade(side=TradeSide.LONG, pnl=100.0)
            for _ in range(5)
        ]
        result = engine.analyse(trades)
        # Should classify as gap_up
        conditions = {p.condition for p in result.day_profiles}
        assert "gap_up" in conditions or "trending" in conditions

    def test_volatile_day_when_many_sl_hits(self):
        engine = MarketConditionAnalyticsEngine()
        # Mix LONG and SHORT to avoid gap_up/gap_down classification
        # then have 5 SL hits out of 6 → sl_rate > 0.55 → volatile
        trades = [
            _trade(pnl=-100.0, exit_reason=ExitReason.SL_HIT, side=TradeSide.LONG)
            for _ in range(3)
        ] + [
            _trade(pnl=-100.0, exit_reason=ExitReason.SL_HIT, side=TradeSide.SHORT)
            for _ in range(2)
        ] + [_trade(pnl=200.0, exit_reason=ExitReason.EOD_EXIT, side=TradeSide.LONG)]
        result = engine.analyse(trades)
        conditions = {p.condition for p in result.day_profiles}
        assert "volatile" in conditions

    def test_condition_stats_match_day_profiles(self):
        engine = MarketConditionAnalyticsEngine()
        trades = [_trade(pnl=100.0) for _ in range(8)]
        result = engine.analyse(trades)
        total_days_in_stats = sum(s.total_days for s in result.condition_stats)
        assert total_days_in_stats == len(result.day_profiles)


# ── FailureAnalyticsEngine tests ──────────────────────────────────────────────

class TestFailureAnalyticsEngine:
    def test_empty_trades_returns_zero_stats(self):
        engine = FailureAnalyticsEngine()
        result = engine.analyse([])
        assert result.total_executed == 0
        assert result.total_sl_hits == 0
        assert result.overall_sl_hit_rate == 0.0

    def test_overall_sl_hit_rate(self):
        engine = FailureAnalyticsEngine()
        trades = [
            _trade(pnl=-80.0, exit_reason=ExitReason.SL_HIT),
            _trade(pnl=-90.0, exit_reason=ExitReason.SL_HIT),
            _trade(pnl=200.0, exit_reason=ExitReason.EOD_EXIT),
            _trade(pnl=100.0, exit_reason=ExitReason.EOD_EXIT),
        ]
        result = engine.analyse(trades)
        assert result.overall_sl_hit_rate == pytest.approx(0.5)

    def test_fake_breakout_rate(self):
        engine = FailureAnalyticsEngine()
        # SL hits are treated as fake breakouts in the current engine
        trades = [
            _trade(pnl=-80.0, exit_reason=ExitReason.SL_HIT),
            _trade(pnl=200.0, exit_reason=ExitReason.EOD_EXIT),
        ]
        result = engine.analyse(trades)
        # 1 out of 2 executed trades hit SL → fake_breakout_rate = 0.5
        assert result.fake_breakout_rate == pytest.approx(0.5)
        assert result.fake_breakout_count == 1

    def test_high_risk_symbols_flagged(self):
        engine = FailureAnalyticsEngine()
        # Build 6 SL hits for "TOXIC" symbol (100% sl rate)
        trades = [
            _trade("TOXIC", pnl=-100.0, exit_reason=ExitReason.SL_HIT)
            for _ in range(6)
        ] + [
            _trade("SAFE", pnl=200.0, exit_reason=ExitReason.EOD_EXIT)
            for _ in range(6)
        ]
        result = engine.analyse(trades)
        flagged = [s.symbol for s in result.high_risk_symbols]
        assert "TOXIC" in flagged
        assert "SAFE" not in flagged

    def test_sl_cluster_days_detected(self):
        engine = FailureAnalyticsEngine()
        # 4 SL hits on same day
        same_day_exit = datetime(2024, 1, 3, 5, 0, tzinfo=timezone.utc)
        trades = [
            _trade(
                pnl=-100.0,
                exit_reason=ExitReason.SL_HIT,
                exit_time=same_day_exit,
            )
            for _ in range(4)
        ]
        result = engine.analyse(trades)
        assert len(result.sl_cluster_days) >= 1
        assert result.max_sl_hits_single_day >= 4

    def test_choppy_days_detected(self):
        engine = FailureAnalyticsEngine()
        # Use NO_BREAKOUT trades WITH an entry_time so date-grouping works.
        # (SimulatedTrade has no trading_date field; entry_time is the grouping key.)
        # 10 NO_BREAKOUT + 1 executed on the same IST date → 10/11 = 91% NB rate
        same_day = datetime(2024, 1, 3, 4, 15, tzinfo=timezone.utc)
        nb_trades = [
            SimulatedTrade(
                symbol="SYM",
                trade_side=TradeSide.LONG,
                breakout_side="UP",
                orb_high=100.0,
                orb_low=98.0,
                probability_score=0.70,
                entry_time=same_day,  # give it a time so date-grouping works
                entry_price=None,
                stop_loss=98.0,
                exit_time=None,
                exit_price=None,
                exit_reason=ExitReason.NO_BREAKOUT,
                quantity=0,
                capital_used=0.0,
                pnl=0.0,
                pnl_percent=0.0,
                risk_reward=None,
            )
            for _ in range(10)
        ]
        trades = nb_trades + [_trade("SYM", pnl=100.0, entry_time=same_day)]
        result = engine.analyse(trades)
        assert len(result.choppy_days) >= 1
        assert result.avg_no_breakout_rate > 0.0

    def test_sl_timing_distribution_sums_to_100(self):
        engine = FailureAnalyticsEngine()
        trades = [
            _trade(pnl=-100.0, exit_reason=ExitReason.SL_HIT)
            for _ in range(10)
        ]
        result = engine.analyse(trades)
        total_pct = sum(s.pct_of_all_sl for s in result.sl_timing_distribution)
        assert total_pct == pytest.approx(100.0, abs=1.0)

    def test_no_breakout_trades_excluded_from_sl_stats(self):
        engine = FailureAnalyticsEngine()
        trades = [_no_breakout() for _ in range(10)]
        result = engine.analyse(trades)
        assert result.total_executed == 0
        assert result.total_sl_hits == 0
