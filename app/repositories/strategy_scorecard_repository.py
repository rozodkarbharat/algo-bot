"""
Repository for StrategyScorecard documents.

Raw-dict query pattern — no ORM-style Beanie field expressions (Beanie 2.x / Pydantic v2).

Scorecards are immutable historical records.  Every computation produces a new
document; there is no in-place update path.  The leaderboard method fetches all
scorecards sorted by (strategy_id asc, computed_at desc), deduplicates in Python
to keep the most-recent scorecard per strategy, then re-sorts by overall_score
descending.  This avoids a complex aggregation pipeline while remaining correct
for collections that grow at a moderate pace (one scorecard per strategy per run).
"""

from app.core.exceptions import DatabaseException
from app.models.strategy_scorecard import StrategyScorecard
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class StrategyScorecardRepository(BaseRepository[StrategyScorecard]):
    document_model = StrategyScorecard

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_scorecard_id(self, scorecard_id: str) -> StrategyScorecard | None:
        """Return the scorecard matching `scorecard_id`, or None if not found."""
        return await StrategyScorecard.find_one({"scorecard_id": scorecard_id})

    async def get_latest_for_strategy(self, strategy_id: str) -> StrategyScorecard | None:
        """Return the most recently computed scorecard for `strategy_id`, or None."""
        results = (
            await StrategyScorecard.find({"strategy_id": strategy_id})
            .sort("-computed_at")
            .limit(1)
            .to_list()
        )
        return results[0] if results else None

    async def get_all_for_strategy(self, strategy_id: str) -> list[StrategyScorecard]:
        """Return all scorecards for `strategy_id`, newest first."""
        return (
            await StrategyScorecard.find({"strategy_id": strategy_id})
            .sort("-computed_at")
            .to_list()
        )

    async def get_leaderboard(self, limit: int = 20) -> list[StrategyScorecard]:
        """
        Return up to `limit` strategies ranked by their most-recent overall_score.

        Algorithm:
        1. Fetch all scorecards sorted by (strategy_id asc, computed_at desc) so
           the first document encountered for each strategy_id is the latest one.
        2. Deduplicate in Python, keeping the first (most-recent) scorecard per
           strategy.
        3. Sort the deduplicated list by overall_score descending (None scores sort
           to the bottom).
        4. Slice to `limit`.
        """
        try:
            all_scorecards: list[StrategyScorecard] = (
                await StrategyScorecard.find({})
                .sort([("strategy_id", 1), ("computed_at", -1)])
                .to_list()
            )
        except Exception as exc:
            logger.error("get_leaderboard: fetch failed: %s", exc, exc_info=True)
            raise DatabaseException("Failed to fetch scorecards for leaderboard.", detail=str(exc))

        # Deduplicate — keep the first entry per strategy_id (already the newest)
        seen: set[str] = set()
        latest_per_strategy: list[StrategyScorecard] = []
        for sc in all_scorecards:
            if sc.strategy_id not in seen:
                seen.add(sc.strategy_id)
                latest_per_strategy.append(sc)

        # Sort by overall_score descending; treat None as -infinity
        latest_per_strategy.sort(
            key=lambda sc: sc.overall_score if sc.overall_score is not None else float("-inf"),
            reverse=True,
        )

        return latest_per_strategy[:limit]

    # ── Writes ────────────────────────────────────────────────────────────────

    async def upsert_for_strategy(self, scorecard: StrategyScorecard) -> StrategyScorecard:
        """
        Persist a new scorecard document.

        Scorecards are immutable historical records — every call inserts a fresh
        document rather than mutating an existing one.  The method is named
        ``upsert_for_strategy`` to match the service-layer convention, but the
        implementation is a plain insert.
        """
        try:
            return await self.create(scorecard)
        except Exception as exc:
            logger.error(
                "upsert_for_strategy: insert failed for strategy_id=%s: %s",
                scorecard.strategy_id,
                exc,
                exc_info=True,
            )
            raise DatabaseException(
                "Failed to insert StrategyScorecard.",
                detail=str(exc),
            )
