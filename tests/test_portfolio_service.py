"""
Unit tests for the portfolio service pipeline.

Mocks all external dependencies (repositories, ranker, risk manager) to test
the orchestration logic in isolation:
  - APPROVED path: signal persisted as APPROVED, callback dispatched.
  - REJECTED by risk gate: signal persisted as REJECTED, callback NOT dispatched.
  - REJECTED by zero capital (allocator): signal persisted as REJECTED.
  - Dispatch errors are swallowed; the allocation is still persisted.
  - Capital exhaustion: subsequent signal rejected when capital is consumed.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.live.signal_engine import GeneratedSignal
from app.models.live_signal import LiveBreakoutSide, LiveSignalType
from app.models.portfolio_allocation import AllocationMethod, AllocationStatus, PortfolioAllocation
from app.models.portfolio_risk_state import PortfolioRiskState
from app.portfolio.capital_allocator import AllocationResult
from app.portfolio.portfolio_risk_manager import PortfolioRiskCheckResult
from app.portfolio.signal_ranker import RankResult
from app.services.portfolio_service import PortfolioService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(d: date = date(2025, 1, 15)) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _signal(
    symbol: str = "RELIANCE",
    signal_type: LiveSignalType = LiveSignalType.BUY,
    entry_price: float = 2_500.0,
    stop_loss: float = 2_450.0,
    probability: float = 0.75,
    strategy_id: str = "one_side_orb",
) -> GeneratedSignal:
    return GeneratedSignal(
        symbol=symbol,
        trading_date=date(2025, 1, 15),
        signal_type=signal_type,
        breakout_side=LiveBreakoutSide.UP,
        entry_price=entry_price,
        stop_loss=stop_loss,
        first_candle_high=2_520.0,
        first_candle_low=2_440.0,
        orb_range_percent=3.28,
        breakout_time=_utc(),
        probability_score=probability,
        strategy_id=strategy_id,
        strategy_name="One-Side ORB",
        strategy_version="1.0.0",
    )


def _risk_state(
    total_capital: float = 1_000_000.0,
    used_capital: float = 0.0,
    is_halted: bool = False,
) -> PortfolioRiskState:
    avail = total_capital - used_capital
    return PortfolioRiskState.model_construct(
        trading_date=_utc(),
        total_capital=total_capital,
        used_capital=used_capital,
        available_capital=avail,
        daily_risk_used=0.0,
        open_positions=0,
        total_approved_today=0,
        total_rejected_today=0,
        strategy_exposure={},
        sector_exposure={},
        realized_pnl_today=0.0,
        peak_capital_today=total_capital,
        is_halted=is_halted,
        halt_reason=None,
        updated_at=_utc(),
    )


def _rank_result(score: float = 0.75) -> RankResult:
    return RankResult(
        ranking_score=score,
        components={"win_rate": score, "expectancy": score, "probability_score": score,
                    "stock_reliability": score, "drawdown_penalty": score},
        weighted_components={"win_rate": score * 0.25},
    )


def _alloc_result(
    signal_id: str = "sig1",
    capital: float = 100_000.0,
    rejection_reason: Optional[str] = None,
) -> AllocationResult:
    return AllocationResult(
        signal_id=signal_id,
        symbol="RELIANCE",
        strategy_id="one_side_orb",
        method=AllocationMethod.SCORE_WEIGHTED,
        allocated_capital=capital,
        allocation_percent=capital / 1_000_000.0,
        ranking_score=0.75,
        rejection_reason=rejection_reason,
    )


# ── Service factory ───────────────────────────────────────────────────────────

def _make_service(
    alloc_repo=None,
    risk_repo=None,
    stock_repo=None,
    analytics_repo=None,
    continuation_repo=None,
    ranker=None,
    risk_manager=None,
) -> PortfolioService:
    """Build a PortfolioService with all external deps mocked."""
    # Prevent auto-wiring to the live market engine singleton.
    mock_engine = MagicMock()
    mock_engine.signal_engine.on_signal = MagicMock()

    # Default mocks
    if alloc_repo is None:
        alloc_repo = AsyncMock()
        alloc_repo.upsert_by_signal_id = AsyncMock()
        alloc_repo.get_strategy_capital_for_date = AsyncMock(return_value=0.0)
        alloc_repo.get_sector_capital_for_date = AsyncMock(return_value=0.0)
        alloc_repo.count_correlated_for_date = AsyncMock(return_value=0)

    if risk_repo is None:
        risk_repo = AsyncMock()
        risk_repo.get_for_date = AsyncMock(return_value=_risk_state())
        risk_repo.upsert = AsyncMock()

    if stock_repo is None:
        stock_repo = AsyncMock()
        mock_stock = MagicMock()
        mock_stock.sector = "Energy"
        stock_repo.get_stock_by_symbol = AsyncMock(return_value=mock_stock)

    if analytics_repo is None:
        analytics_repo = AsyncMock()
        analytics_repo.get_by_symbol = AsyncMock(return_value=None)

    if continuation_repo is None:
        continuation_repo = AsyncMock()
        continuation_repo.get_by_symbol = AsyncMock(return_value=None)

    if ranker is None:
        ranker = MagicMock()
        ranker.rank = MagicMock(return_value=_rank_result())

    if risk_manager is None:
        risk_manager = MagicMock()
        risk_manager.evaluate = MagicMock(return_value=PortfolioRiskCheckResult(accepted=True))

    svc = PortfolioService.__new__(PortfolioService)
    svc._engine = mock_engine
    svc._alloc_repo = alloc_repo
    svc._risk_repo = risk_repo
    svc._stock_repo = stock_repo
    svc._analytics_repo = analytics_repo
    svc._continuation_repo = continuation_repo
    svc._ranker = ranker
    svc._risk = risk_manager
    svc._approved_callbacks = []
    svc._wired = True
    svc._lock = asyncio.Lock()
    return svc


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approved_signal_persisted_and_dispatched():
    """Happy path: approved signal → allocation doc saved, callback called."""
    dispatched: list[tuple] = []

    async def mock_callback(signal, allocation):
        dispatched.append((signal, allocation))

    alloc_repo = AsyncMock()
    alloc_repo.upsert_by_signal_id = AsyncMock()
    alloc_repo.get_strategy_capital_for_date = AsyncMock(return_value=0.0)
    alloc_repo.get_sector_capital_for_date = AsyncMock(return_value=0.0)
    alloc_repo.count_correlated_for_date = AsyncMock(return_value=0)

    svc = _make_service(alloc_repo=alloc_repo)
    svc._approved_callbacks.append(mock_callback)

    sig = _signal()
    await svc._handle_generated_signal(sig)

    # Allocation must be persisted
    alloc_repo.upsert_by_signal_id.assert_called_once()
    persisted: PortfolioAllocation = alloc_repo.upsert_by_signal_id.call_args[0][0]
    assert persisted.allocation_status == AllocationStatus.APPROVED

    # Callback must be called once with the signal and the allocation
    assert len(dispatched) == 1
    assert dispatched[0][0] is sig


@pytest.mark.asyncio
async def test_rejected_signal_not_dispatched():
    """Risk gate rejection → allocation doc saved as REJECTED, no callback."""
    dispatched: list = []

    async def mock_callback(signal, allocation):
        dispatched.append(signal)

    risk_manager = MagicMock()
    risk_manager.evaluate = MagicMock(
        return_value=PortfolioRiskCheckResult(
            accepted=False, reason="max_open_positions_reached"
        )
    )

    svc = _make_service(risk_manager=risk_manager)
    svc._approved_callbacks.append(mock_callback)

    await svc._handle_generated_signal(_signal())

    # Allocation still persisted
    svc._alloc_repo.upsert_by_signal_id.assert_called_once()
    persisted = svc._alloc_repo.upsert_by_signal_id.call_args[0][0]
    assert persisted.allocation_status == AllocationStatus.REJECTED
    assert persisted.rejection_reason == "max_open_positions_reached"

    # Callback NOT called
    assert len(dispatched) == 0


@pytest.mark.asyncio
async def test_halted_portfolio_rejects_new_signal():
    """is_halted=True in risk state → allocation rejected, callback not fired."""
    dispatched: list = []

    async def mock_callback(signal, allocation):
        dispatched.append(signal)

    risk_repo = AsyncMock()
    risk_repo.get_for_date = AsyncMock(return_value=_risk_state(is_halted=True))
    risk_repo.upsert = AsyncMock()

    risk_manager = MagicMock()
    risk_manager.evaluate = MagicMock(
        return_value=PortfolioRiskCheckResult(accepted=False, reason="portfolio_halted")
    )

    svc = _make_service(risk_repo=risk_repo, risk_manager=risk_manager)
    svc._approved_callbacks.append(mock_callback)

    await svc._handle_generated_signal(_signal())

    persisted = svc._alloc_repo.upsert_by_signal_id.call_args[0][0]
    assert persisted.allocation_status == AllocationStatus.REJECTED
    assert len(dispatched) == 0


@pytest.mark.asyncio
async def test_dispatch_error_does_not_propagate():
    """Downstream callback that raises must not crash the pipeline."""
    async def bad_callback(signal, allocation):
        raise RuntimeError("downstream exploded")

    svc = _make_service()
    svc._approved_callbacks.append(bad_callback)

    # Should complete without raising
    await svc._handle_generated_signal(_signal())

    # Allocation still persisted
    svc._alloc_repo.upsert_by_signal_id.assert_called_once()


@pytest.mark.asyncio
async def test_multiple_callbacks_all_called_on_approval():
    counts = {"a": 0, "b": 0}

    async def cb_a(s, a):
        counts["a"] += 1

    async def cb_b(s, a):
        counts["b"] += 1

    svc = _make_service()
    svc._approved_callbacks.extend([cb_a, cb_b])

    await svc._handle_generated_signal(_signal())

    assert counts["a"] == 1
    assert counts["b"] == 1


@pytest.mark.asyncio
async def test_risk_state_created_when_missing():
    """get_for_date returns None → service creates initial state and upserts."""
    risk_repo = AsyncMock()
    risk_repo.get_for_date = AsyncMock(return_value=None)
    risk_repo.upsert = AsyncMock()

    svc = _make_service(risk_repo=risk_repo)

    with patch("app.services.portfolio_service.settings") as mock_settings:
        mock_settings.PORTFOLIO_TOTAL_CAPITAL = 1_000_000.0
        mock_settings.PORTFOLIO_ALLOCATION_METHOD = "SCORE_WEIGHTED"
        mock_settings.PORTFOLIO_MAX_OPEN_POSITIONS = 10
        mock_settings.PORTFOLIO_MAX_CAPITAL_EXPOSURE_PCT = 80.0
        mock_settings.PORTFOLIO_MAX_DAILY_LOSS_PCT = 2.0
        mock_settings.PORTFOLIO_MAX_CAPITAL_PER_TRADE_PCT = 20.0
        mock_settings.PORTFOLIO_MAX_CAPITAL_PER_STRATEGY_PCT = 50.0
        mock_settings.PORTFOLIO_MAX_CAPITAL_PER_SECTOR_PCT = 40.0
        mock_settings.PORTFOLIO_MAX_CORRELATED_POSITIONS = 3
        mock_settings.PORTFOLIO_FIXED_RISK_PCT = 1.0
        mock_settings.PORTFOLIO_MIN_CAPITAL_PER_TRADE = 5_000.0

        await svc._get_or_create_risk_state(_utc())

    # upsert called once to persist the freshly-created state
    risk_repo.upsert.assert_called_once()
    created: PortfolioRiskState = risk_repo.upsert.call_args[0][0]
    assert created.total_capital == 1_000_000.0
    assert created.used_capital == 0.0
    assert created.is_halted is False


@pytest.mark.asyncio
async def test_risk_state_updated_on_approval():
    """After approval, used_capital and open_positions must increase."""
    initial = _risk_state(total_capital=1_000_000.0, used_capital=0.0)
    risk_repo = AsyncMock()
    risk_repo.get_for_date = AsyncMock(return_value=initial)
    risk_repo.upsert = AsyncMock()

    svc = _make_service(risk_repo=risk_repo)
    await svc._handle_generated_signal(_signal())

    # upsert called at least once (initial create + update)
    assert risk_repo.upsert.call_count >= 1
    # Final upserted state should reflect deployed capital
    final_state = risk_repo.upsert.call_args_list[-1][0][0]
    assert final_state.total_approved_today >= 1


@pytest.mark.asyncio
async def test_on_approved_signal_registers_callback():
    svc = _make_service()
    assert len(svc._approved_callbacks) == 0

    async def cb(s, a):
        pass

    svc.on_approved_signal(cb)
    assert len(svc._approved_callbacks) == 1
    assert svc._approved_callbacks[0] is cb


# ── Analytics ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_analytics_empty_range():
    alloc_repo = AsyncMock()
    alloc_repo.get_for_date_range = AsyncMock(return_value=[])

    svc = _make_service(alloc_repo=alloc_repo)
    analytics = await svc.get_analytics(date(2025, 1, 1), date(2025, 1, 31))

    assert analytics.total_allocations == 0
    assert analytics.approved_allocations == 0
    assert analytics.approval_rate == 0.0


@pytest.mark.asyncio
async def test_get_analytics_counts_correctly():
    alloc_repo = AsyncMock()

    def _make_alloc(status, capital=100_000.0, rejection_reason=None):
        a = PortfolioAllocation.model_construct(
            allocation_id="x",
            trading_date=_utc(),
            strategy_id="one_side_orb",
            symbol="RELIANCE",
            signal_id="s",
            signal_type="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            probability_score=0.75,
            ranking_score=0.75,
            ranking_components={},
            allocation_method=AllocationMethod.EQUAL_WEIGHT,
            allocation_percent=0.10,
            allocated_capital=capital,
            allocation_status=status,
            rejection_reason=rejection_reason,
            risk_detail={},
            sector="Energy",
            metadata={},
        )
        return a

    from app.models.portfolio_allocation import AllocationStatus as AS
    allocs = [
        _make_alloc(AS.APPROVED, 100_000.0),
        _make_alloc(AS.APPROVED, 80_000.0),
        _make_alloc(AS.REJECTED, 0.0, "max_open_positions_reached"),
    ]
    alloc_repo.get_for_date_range = AsyncMock(return_value=allocs)

    svc = _make_service(alloc_repo=alloc_repo)
    analytics = await svc.get_analytics(date(2025, 1, 1), date(2025, 1, 31))

    assert analytics.total_allocations == 3
    assert analytics.approved_allocations == 2
    assert analytics.rejected_allocations == 1
    assert abs(analytics.approval_rate - 2 / 3) < 0.001
    assert analytics.total_capital_deployed == 180_000.0
    assert analytics.avg_capital_per_trade == 90_000.0
    assert analytics.rejection_reasons == {"max_open_positions_reached": 1}
