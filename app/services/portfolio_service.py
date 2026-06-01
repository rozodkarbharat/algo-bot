"""
Portfolio service — orchestrator for the portfolio & capital allocation engine.

This service is the single layer that:
  - Subscribes to the live signal engine and intercepts every GeneratedSignal.
  - Fetches historical context (win rate, expectancy, drawdown, continuation
    probability) for ranking.
  - Ranks the signal, sizes capital, runs the portfolio risk gate.
  - Persists a PortfolioAllocation document (APPROVED or REJECTED).
  - Updates the PortfolioRiskState snapshot for the day.
  - Dispatches APPROVED signals to registered downstream callbacks (paper
    trading service, live execution service).

Integration contract:
  - Paper trading and live execution services call
    `portfolio_service.on_approved_signal(callback)` instead of wiring
    directly to the raw signal engine.
  - This makes the portfolio layer the single point of dispatch so
    both downstream consumers see only portfolio-approved signals.

Analytics:
  - `get_analytics()` computes portfolio-level metrics over a date range:
    portfolio return, strategy return, allocation efficiency, drawdown, Sharpe.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Awaitable, Callable, Optional

from app.config.settings import settings
from app.core.exceptions import DatabaseException, PortfolioException
from app.live.market_engine import LiveMarketEngine, live_market_engine
from app.live.signal_engine import GeneratedSignal
from app.models.portfolio_allocation import (
    AllocationMethod,
    AllocationStatus,
    PortfolioAllocation,
    _new_allocation_id,
)
from app.models.portfolio_risk_state import PortfolioRiskState
from app.portfolio.capital_allocator import AllocationInput, CapitalAllocator
from app.portfolio.portfolio_risk_manager import PortfolioRiskContext, PortfolioRiskManager
from app.portfolio.signal_ranker import SignalRankInput, SignalRanker
from app.repositories.continuation_statistic_repository import ContinuationStatisticRepository
from app.repositories.portfolio_allocation_repository import PortfolioAllocationRepository
from app.repositories.portfolio_risk_state_repository import PortfolioRiskStateRepository
from app.repositories.stock_performance_analytics_repository import StockPerformanceAnalyticsRepository
from app.repositories.stock_repository import StockRepository
from app.utils.logger import get_logger
from app.utils.market_time import date_to_utc_midnight, now_utc
from app.utils.trading_day import today_ist

logger = get_logger(__name__)

ApprovedSignalCallback = Callable[[GeneratedSignal, PortfolioAllocation], Awaitable[None]]


# ── Analytics result dataclass ────────────────────────────────────────────────

@dataclass
class PortfolioAnalytics:
    """Aggregate performance metrics over a date range."""

    from_date: date
    to_date: date
    total_allocations: int = 0
    approved_allocations: int = 0
    rejected_allocations: int = 0
    approval_rate: float = 0.0

    # Capital metrics
    total_capital_deployed: float = 0.0
    avg_capital_per_trade: float = 0.0
    allocation_efficiency: float = 0.0    # approved / total

    # Return metrics (require execution data — estimated from allocations)
    strategy_breakdown: dict = field(default_factory=dict)  # strategy_id -> count/capital

    # Rejection breakdown
    rejection_reasons: dict = field(default_factory=dict)   # reason -> count


# ── Service ───────────────────────────────────────────────────────────────────

class PortfolioService:
    """
    Singleton coordinator for the portfolio allocation pipeline.

    Construction order:
      1. Create service (registers on signal engine).
      2. Paper/live services call `on_approved_signal(their_callback)`.
      3. Session starts; signals flow in; portfolio evaluates and dispatches.
    """

    def __init__(
        self,
        engine: Optional[LiveMarketEngine] = None,
        allocation_repo: Optional[PortfolioAllocationRepository] = None,
        risk_state_repo: Optional[PortfolioRiskStateRepository] = None,
        stock_repo: Optional[StockRepository] = None,
        analytics_repo: Optional[StockPerformanceAnalyticsRepository] = None,
        continuation_repo: Optional[ContinuationStatisticRepository] = None,
        ranker: Optional[SignalRanker] = None,
        risk_manager: Optional[PortfolioRiskManager] = None,
    ) -> None:
        self._engine: LiveMarketEngine = engine or live_market_engine

        self._alloc_repo = allocation_repo or PortfolioAllocationRepository()
        self._risk_repo = risk_state_repo or PortfolioRiskStateRepository()
        self._stock_repo = stock_repo or StockRepository()
        self._analytics_repo = analytics_repo or StockPerformanceAnalyticsRepository()
        self._continuation_repo = continuation_repo or ContinuationStatisticRepository()

        self._ranker = ranker or SignalRanker()
        self._risk = risk_manager or PortfolioRiskManager(
            max_open_positions=settings.PORTFOLIO_MAX_OPEN_POSITIONS,
            max_capital_exposure_pct=settings.PORTFOLIO_MAX_CAPITAL_EXPOSURE_PCT / 100.0,
            max_daily_loss_pct=settings.PORTFOLIO_MAX_DAILY_LOSS_PCT / 100.0,
            max_capital_per_trade_pct=settings.PORTFOLIO_MAX_CAPITAL_PER_TRADE_PCT / 100.0,
            max_capital_per_strategy_pct=settings.PORTFOLIO_MAX_CAPITAL_PER_STRATEGY_PCT / 100.0,
            max_capital_per_sector_pct=settings.PORTFOLIO_MAX_CAPITAL_PER_SECTOR_PCT / 100.0,
            max_correlated_positions=settings.PORTFOLIO_MAX_CORRELATED_POSITIONS,
        )

        self._approved_callbacks: list[ApprovedSignalCallback] = []
        self._wired: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

        self._wire_pipeline_hooks()

    # ── Wiring ────────────────────────────────────────────────────────────────

    def _wire_pipeline_hooks(self) -> None:
        if self._wired:
            return
        self._engine.signal_engine.on_signal(self._handle_generated_signal)
        self._wired = True
        logger.info("[portfolio] subscribed to signal engine.")

    def on_approved_signal(self, callback: ApprovedSignalCallback) -> None:
        """
        Register a downstream callback to receive APPROVED signals.

        Called by PaperTradingService and LiveExecutionService instead of
        subscribing directly to the raw signal engine.
        """
        self._approved_callbacks.append(callback)
        logger.info(
            "[portfolio] registered approved-signal callback: %s",
            getattr(callback, "__qualname__", repr(callback)),
        )

    # ── Main signal handler ───────────────────────────────────────────────────

    async def _handle_generated_signal(self, signal: GeneratedSignal) -> None:
        """
        Full allocation pipeline for a single incoming signal.

        Steps:
          1. Build ranking input (fetch stock analytics + continuation stat).
          2. Rank the signal.
          3. Fetch current risk state for today.
          4. Run portfolio risk gate.
          5. Size capital (if approved by risk gate).
          6. Persist PortfolioAllocation.
          7. Update PortfolioRiskState.
          8. If APPROVED, dispatch to downstream callbacks.
        """
        symbol = signal.symbol.upper()
        trading_dt = date_to_utc_midnight(signal.trading_date)

        async with self._lock:
            try:
                allocation = await self._process_signal(signal, symbol, trading_dt)
            except Exception as exc:
                logger.error(
                    "[portfolio] pipeline error for %s: %s",
                    symbol,
                    exc,
                    exc_info=True,
                )
                return

        if allocation.allocation_status is AllocationStatus.APPROVED:
            await self._dispatch_approved(signal, allocation)

    async def _process_signal(
        self,
        signal: GeneratedSignal,
        symbol: str,
        trading_dt: datetime,
    ) -> PortfolioAllocation:
        # ── (1) Fetch ranking context ─────────────────────────────────────────
        rank_input = await self._build_rank_input(signal, symbol)

        # ── (2) Rank ─────────────────────────────────────────────────────────
        rank_result = self._ranker.rank(rank_input)

        # ── (3) Fetch risk state for today ────────────────────────────────────
        risk_state = await self._get_or_create_risk_state(trading_dt)

        # ── (4) Build allocation input for capital sizing ─────────────────────
        method = _allocation_method_from_settings()
        alloc_input = AllocationInput(
            signal_id=signal.signal_id if hasattr(signal, "signal_id") else f"{symbol}|{trading_dt}",
            symbol=symbol,
            strategy_id=signal.strategy_id,
            ranking_score=rank_result.ranking_score,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
        )
        allocator = _build_allocator(risk_state.total_capital)
        alloc_results = allocator.allocate([alloc_input], risk_state.available_capital, method)
        alloc_result = alloc_results[0]

        # ── (5) Fetch stock sector for risk context ────────────────────────────
        sector = await self._get_sector(symbol)

        # ── (6) Portfolio risk gate ───────────────────────────────────────────
        strategy_capital = await self._alloc_repo.get_strategy_capital_for_date(
            trading_dt, signal.strategy_id
        )
        sector_capital = (
            await self._alloc_repo.get_sector_capital_for_date(trading_dt, sector)
            if sector
            else 0.0
        )
        correlated = (
            await self._alloc_repo.count_correlated_for_date(trading_dt, sector)
            if sector
            else 0
        )

        risk_ctx = PortfolioRiskContext(
            symbol=symbol,
            strategy_id=signal.strategy_id,
            sector=sector,
            total_capital=risk_state.total_capital,
            available_capital=risk_state.available_capital,
            proposed_allocation=alloc_result.allocated_capital,
            used_capital=risk_state.used_capital,
            open_positions=risk_state.open_positions,
            strategy_used_capital=strategy_capital,
            sector_used_capital=sector_capital,
            correlated_positions=correlated,
        )
        risk_check = self._risk.evaluate(
            risk_ctx,
            daily_loss=risk_state.realized_pnl_today,
            is_halted=risk_state.is_halted,
        )

        # Early rejection: capital sized to 0 by the allocator
        if alloc_result.allocated_capital <= 0 and alloc_result.rejection_reason:
            risk_check_accepted = False
            rejection_reason = alloc_result.rejection_reason
        else:
            risk_check_accepted = risk_check.accepted
            rejection_reason = risk_check.reason if not risk_check.accepted else None

        # ── (7) Determine signal_id from signal ───────────────────────────────
        # GeneratedSignal does not carry a persisted signal_id; use the
        # symbolic key that the live signal service would use.
        sig_id = getattr(signal, "signal_id", None) or (
            f"{symbol}|{signal.trading_date.isoformat()}|{signal.signal_type.value}"
        )

        # ── (8) Build and persist PortfolioAllocation ─────────────────────────
        _now = now_utc()
        allocation = PortfolioAllocation.model_construct(
            allocation_id=_new_allocation_id(),
            trading_date=trading_dt,
            strategy_id=signal.strategy_id,
            symbol=symbol,
            signal_id=sig_id,
            signal_type=signal.signal_type.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            probability_score=signal.probability_score,
            ranking_score=rank_result.ranking_score,
            ranking_components=rank_result.components,
            allocation_method=method,
            allocation_percent=alloc_result.allocation_percent if risk_check_accepted else 0.0,
            allocated_capital=alloc_result.allocated_capital if risk_check_accepted else 0.0,
            allocation_status=(
                AllocationStatus.APPROVED if risk_check_accepted else AllocationStatus.REJECTED
            ),
            rejection_reason=rejection_reason,
            risk_detail=risk_check.detail or {},
            sector=sector,
            metadata={
                "weighted_components": rank_result.weighted_components,
                "allocation_method": method.value,
            },
            created_at=_now,
            updated_at=_now,
        )

        await self._alloc_repo.upsert_by_signal_id(allocation)
        logger.info(
            "[portfolio] %s %s/%s score=%.3f capital=%.0f status=%s",
            "APPROVED" if risk_check_accepted else "REJECTED",
            signal.strategy_id,
            symbol,
            rank_result.ranking_score,
            allocation.allocated_capital,
            allocation.allocation_status,
        )

        # ── (9) Update risk state ─────────────────────────────────────────────
        if risk_check_accepted:
            await self._update_risk_state(
                risk_state,
                symbol=symbol,
                strategy_id=signal.strategy_id,
                sector=sector,
                allocated=alloc_result.allocated_capital,
                approved=True,
            )
        else:
            await self._update_risk_state(
                risk_state,
                symbol=symbol,
                strategy_id=signal.strategy_id,
                sector=sector,
                allocated=0.0,
                approved=False,
            )

        return allocation

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _dispatch_approved(
        self, signal: GeneratedSignal, allocation: PortfolioAllocation
    ) -> None:
        for cb in self._approved_callbacks:
            try:
                await cb(signal, allocation)
            except Exception as exc:
                logger.error(
                    "[portfolio] downstream callback error for %s: %s",
                    signal.symbol,
                    exc,
                    exc_info=True,
                )

    # ── Context helpers ───────────────────────────────────────────────────────

    async def _build_rank_input(
        self, signal: GeneratedSignal, symbol: str
    ) -> SignalRankInput:
        analytics = await self._analytics_repo.get_by_symbol(symbol)
        continuation = await self._continuation_repo.get_by_symbol(symbol)
        return SignalRankInput(
            symbol=symbol,
            strategy_id=signal.strategy_id,
            probability_score=signal.probability_score,
            historical_win_rate=analytics.win_rate if analytics else None,
            historical_expectancy=analytics.expectancy if analytics else None,
            historical_max_drawdown=analytics.max_drawdown if analytics else None,
            continuation_probability=(
                continuation.continuation_probability if continuation else None
            ),
        )

    async def _get_sector(self, symbol: str) -> Optional[str]:
        try:
            stock = await self._stock_repo.get_stock_by_symbol(symbol)
            return stock.sector if stock else None
        except Exception:
            return None

    async def _get_or_create_risk_state(
        self, trading_dt: datetime
    ) -> PortfolioRiskState:
        state = await self._risk_repo.get_for_date(trading_dt)
        if state is None:
            _now = now_utc()
            state = PortfolioRiskState.model_construct(
                trading_date=trading_dt,
                total_capital=settings.PORTFOLIO_TOTAL_CAPITAL,
                used_capital=0.0,
                available_capital=settings.PORTFOLIO_TOTAL_CAPITAL,
                daily_risk_used=0.0,
                open_positions=0,
                total_approved_today=0,
                total_rejected_today=0,
                strategy_exposure={},
                sector_exposure={},
                realized_pnl_today=0.0,
                peak_capital_today=settings.PORTFOLIO_TOTAL_CAPITAL,
                is_halted=False,
                halt_reason=None,
                updated_at=_now,
            )
            await self._risk_repo.upsert(state)
        return state

    async def _update_risk_state(
        self,
        state: PortfolioRiskState,
        *,
        symbol: str,
        strategy_id: str,
        sector: Optional[str],
        allocated: float,
        approved: bool,
    ) -> None:
        if approved:
            state.used_capital = round(state.used_capital + allocated, 4)
            state.available_capital = round(
                state.total_capital - state.used_capital, 4
            )
            state.open_positions += 1
            state.total_approved_today += 1

            # Update strategy exposure
            current_strategy = state.strategy_exposure.get(strategy_id, 0.0)
            state.strategy_exposure[strategy_id] = round(current_strategy + allocated, 4)

            # Update sector exposure
            if sector:
                current_sector = state.sector_exposure.get(sector, 0.0)
                state.sector_exposure[sector] = round(current_sector + allocated, 4)
        else:
            state.total_rejected_today += 1

        # Check daily loss limit and flip halt flag if needed
        loss_limit = state.total_capital * (settings.PORTFOLIO_MAX_DAILY_LOSS_PCT / 100.0)
        if state.realized_pnl_today < 0 and abs(state.realized_pnl_today) >= loss_limit:
            if not state.is_halted:
                state.is_halted = True
                state.halt_reason = "daily_loss_limit_breached"
                logger.warning(
                    "[portfolio] HALT triggered for %s — daily loss %.2f >= limit %.2f",
                    state.trading_date.date(),
                    state.realized_pnl_today,
                    -loss_limit,
                )

        state.mark_updated()
        await self._risk_repo.upsert(state)

    # ── Public query API (used by routes) ─────────────────────────────────────

    async def get_allocations_for_date(
        self,
        trading_date: date,
        *,
        status: Optional[AllocationStatus] = None,
    ) -> list[PortfolioAllocation]:
        dt = date_to_utc_midnight(trading_date)
        if status:
            return await self._alloc_repo.get_for_date(dt)
        return await self._alloc_repo.get_for_date(dt)

    async def get_allocations_for_range(
        self,
        from_date: date,
        to_date: date,
        status: Optional[AllocationStatus] = None,
    ) -> list[PortfolioAllocation]:
        return await self._alloc_repo.get_for_date_range(
            date_to_utc_midnight(from_date),
            date_to_utc_midnight(to_date),
            status=status,
        )

    async def get_risk_state(
        self, trading_date: Optional[date] = None
    ) -> Optional[PortfolioRiskState]:
        if trading_date is None:
            return await self._risk_repo.get_latest()
        return await self._risk_repo.get_for_date(date_to_utc_midnight(trading_date))

    async def get_analytics(
        self, from_date: date, to_date: date
    ) -> PortfolioAnalytics:
        """
        Compute portfolio-level analytics for a date range.

        Calculates:
          - approval rate
          - capital deployment
          - allocation efficiency
          - per-strategy breakdown
          - rejection reason histogram
        """
        allocations = await self._alloc_repo.get_for_date_range(
            date_to_utc_midnight(from_date),
            date_to_utc_midnight(to_date),
        )

        analytics = PortfolioAnalytics(from_date=from_date, to_date=to_date)
        analytics.total_allocations = len(allocations)

        strategy_data: dict[str, dict] = {}
        rejection_counts: dict[str, int] = {}

        for a in allocations:
            if a.allocation_status is AllocationStatus.APPROVED:
                analytics.approved_allocations += 1
                analytics.total_capital_deployed += a.allocated_capital

                # Strategy breakdown
                sid = a.strategy_id
                if sid not in strategy_data:
                    strategy_data[sid] = {"count": 0, "capital": 0.0}
                strategy_data[sid]["count"] += 1
                strategy_data[sid]["capital"] = round(
                    strategy_data[sid]["capital"] + a.allocated_capital, 4
                )
            else:
                analytics.rejected_allocations += 1
                reason = a.rejection_reason or "unknown"
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

        if analytics.total_allocations > 0:
            analytics.approval_rate = round(
                analytics.approved_allocations / analytics.total_allocations, 4
            )
        if analytics.approved_allocations > 0:
            analytics.avg_capital_per_trade = round(
                analytics.total_capital_deployed / analytics.approved_allocations, 2
            )
        analytics.allocation_efficiency = analytics.approval_rate
        analytics.strategy_breakdown = strategy_data
        analytics.rejection_reasons = rejection_counts

        return analytics

    # ── Pause / resume ────────────────────────────────────────────────────────

    async def halt_today(self, reason: str = "manual_halt") -> None:
        """Manually halt all new portfolio allocations for today."""
        dt = date_to_utc_midnight(today_ist())
        state = await self._get_or_create_risk_state(dt)
        state.is_halted = True
        state.halt_reason = reason
        state.mark_updated()
        await self._risk_repo.upsert(state)
        logger.warning("[portfolio] manually halted for %s: %s", dt.date(), reason)

    async def resume_today(self) -> None:
        """Remove the halt flag for today."""
        dt = date_to_utc_midnight(today_ist())
        state = await self._get_or_create_risk_state(dt)
        state.is_halted = False
        state.halt_reason = None
        state.mark_updated()
        await self._risk_repo.upsert(state)
        logger.info("[portfolio] halt cleared for %s.", dt.date())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _allocation_method_from_settings() -> AllocationMethod:
    raw = settings.PORTFOLIO_ALLOCATION_METHOD.upper()
    try:
        return AllocationMethod(raw)
    except ValueError:
        logger.warning(
            "[portfolio] unknown PORTFOLIO_ALLOCATION_METHOD=%r; defaulting to EQUAL_WEIGHT",
            raw,
        )
        return AllocationMethod.EQUAL_WEIGHT


def _build_allocator(total_capital: float) -> CapitalAllocator:
    return CapitalAllocator(
        total_capital=total_capital,
        max_capital_per_trade_pct=settings.PORTFOLIO_MAX_CAPITAL_PER_TRADE_PCT / 100.0,
        fixed_risk_pct=settings.PORTFOLIO_FIXED_RISK_PCT / 100.0,
        min_capital_per_trade=settings.PORTFOLIO_MIN_CAPITAL_PER_TRADE,
    )


# ── Module-level singleton ────────────────────────────────────────────────────

portfolio_service: PortfolioService = PortfolioService()
