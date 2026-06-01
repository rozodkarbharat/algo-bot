"""
Validation Service — orchestrates all validation engines for the API layer.

Routes call this service exclusively; no direct Beanie access from route handlers.
"""

from datetime import datetime, timezone
from typing import Optional

from app.utils.logger import get_logger
from app.validation.health_score_engine import HealthScoreEngine
from app.validation.latency_tracker import LatencyTracker
from app.validation.reality_gap_analyzer import RealityGapAnalyzer
from app.validation.signal_quality_engine import SignalQualityEngine
from app.validation.slippage_analyzer import SlippageAnalyzer

logger = get_logger(__name__)


class ValidationService:
    """
    Thin orchestration layer over the 5 validation engines.

    Each public method maps 1:1 to an API endpoint, keeping route handlers
    free of business logic.
    """

    def __init__(self) -> None:
        self._signal_quality = SignalQualityEngine()
        self._slippage = SlippageAnalyzer()
        self._latency = LatencyTracker()
        self._reality_gap = RealityGapAnalyzer()
        self._health_score = HealthScoreEngine()

    # ── Signal quality ────────────────────────────────────────────────────────

    async def get_signal_quality(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str] = None,
    ):
        try:
            return await self._signal_quality.compute(from_date, to_date, strategy_id)
        except Exception as exc:
            logger.error("ValidationService.get_signal_quality failed: %s", exc, exc_info=True)
            raise

    # ── Slippage ──────────────────────────────────────────────────────────────

    async def get_slippage(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str] = None,
        trading_mode: str = "PAPER",
    ):
        try:
            return await self._slippage.compute(from_date, to_date, strategy_id, trading_mode)
        except Exception as exc:
            logger.error("ValidationService.get_slippage failed: %s", exc, exc_info=True)
            raise

    # ── Latency ───────────────────────────────────────────────────────────────

    async def get_latency(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str] = None,
    ):
        try:
            return await self._latency.compute(from_date, to_date, strategy_id)
        except Exception as exc:
            logger.error("ValidationService.get_latency failed: %s", exc, exc_info=True)
            raise

    # ── Reality gap ───────────────────────────────────────────────────────────

    async def get_reality_gap(
        self,
        strategy_id: str,
        from_date: datetime,
        to_date: datetime,
    ):
        try:
            return await self._reality_gap.compute(strategy_id, from_date, to_date)
        except Exception as exc:
            logger.error("ValidationService.get_reality_gap failed: %s", exc, exc_info=True)
            raise

    # ── Health score ──────────────────────────────────────────────────────────

    async def get_health(
        self,
        from_date: datetime,
        to_date: datetime,
        strategy_id: Optional[str] = None,
    ) -> list:
        """
        Compute health scores for one or all active strategies.

        When strategy_id is None the service discovers active strategy IDs by
        inspecting which strategies have signals in the requested window.
        """
        from app.models.live_signal import LiveSignal  # local to avoid circular

        if strategy_id:
            strategy_ids = [strategy_id]
        else:
            signals = await LiveSignal.find(
                LiveSignal.trading_date >= from_date,
                LiveSignal.trading_date <= to_date,
            ).to_list()
            strategy_ids = list({s.strategy_id for s in signals}) or ["one_side_orb"]

        results = []
        for sid in strategy_ids:
            try:
                result = await self._health_score.compute(sid, from_date, to_date)
                results.append(result)
            except Exception as exc:
                logger.warning("Health score failed for strategy %s: %s", sid, exc)

        return results
