"""
BacktestTrade repository — data-access layer for the backtest_trades collection.

Uses bulk_write for performance when saving large batches of simulated trades.
All queries use raw MongoDB filter dicts (Beanie 2.x / Pydantic v2 requirement).
"""

from typing import Optional

from pymongo import InsertOne, ASCENDING

from app.core.exceptions import DatabaseException
from app.models.backtest_trade import BacktestTrade, ExitReason
from app.repositories.base_repository import BaseRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BacktestTradeRepository(BaseRepository[BacktestTrade]):
    document_model = BacktestTrade

    # ── Writes ────────────────────────────────────────────────────────────────

    async def bulk_insert_trades(self, trades: list[BacktestTrade]) -> int:
        """
        Insert many BacktestTrade documents in a single bulk_write call.

        Returns the count of inserted documents.
        Uses unordered InsertOne operations so a single failure doesn't abort
        the entire batch.
        """
        if not trades:
            return 0
        try:
            collection = BacktestTrade.get_motor_collection()
            operations = [
                InsertOne(t.model_dump(exclude={"id"})) for t in trades
            ]
            result = await collection.bulk_write(operations, ordered=False)
            logger.debug(
                "bulk_insert_trades: %d inserted for run_id=%s",
                result.inserted_count,
                trades[0].run_id if trades else "?",
            )
            return result.inserted_count
        except Exception as exc:
            logger.error("bulk_insert_trades failed: %s", exc, exc_info=True)
            raise DatabaseException("Bulk insert of BacktestTrade records failed.", detail=str(exc))

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_trades_for_run(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        exit_reason: Optional[ExitReason] = None,
        limit: int = 500,
        skip: int = 0,
    ) -> list[BacktestTrade]:
        """Return paginated trades for a run, sorted by trading_date ASC."""
        try:
            query: dict = {"run_id": run_id}
            if symbol:
                query["symbol"] = symbol.upper()
            if exit_reason:
                query["exit_reason"] = exit_reason.value
            return (
                await BacktestTrade.find(query)
                .sort("trading_date")
                .skip(skip)
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch trades for run {run_id}.", detail=str(exc)
            )

    async def count_trades_for_run(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        exit_reason: Optional[ExitReason] = None,
    ) -> int:
        """Count trades for a run, with optional filters."""
        try:
            query: dict = {"run_id": run_id}
            if symbol:
                query["symbol"] = symbol.upper()
            if exit_reason:
                query["exit_reason"] = exit_reason.value
            return await BacktestTrade.find(query).count()
        except Exception as exc:
            raise DatabaseException(
                f"Failed to count trades for run {run_id}.", detail=str(exc)
            )

    async def get_all_trades_for_run(self, run_id: str) -> list[BacktestTrade]:
        """
        Return ALL trade documents for a run in chronological order.

        Used by MetricsEngine — fetches entire result set into memory.
        Only call this for completed runs; stream large runs in batches.
        """
        try:
            return (
                await BacktestTrade.find({"run_id": run_id})
                .sort("trading_date")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch all trades for run {run_id}.", detail=str(exc)
            )

    async def get_trades_by_symbol(
        self,
        run_id: str,
        symbol: str,
    ) -> list[BacktestTrade]:
        """Return all trades for a specific symbol within a run."""
        try:
            return (
                await BacktestTrade.find(
                    {"run_id": run_id, "symbol": symbol.upper()}
                )
                .sort("trading_date")
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch trades for {symbol} in run {run_id}.", detail=str(exc)
            )

    async def get_executed_trades_by_strategy(
        self,
        strategy_id: str,
        limit: int = 50_000,
    ) -> list[BacktestTrade]:
        """
        Return all executed trades for a strategy across ALL backtest runs.

        Excludes NO_BREAKOUT candidates (pnl=0 by contract).
        Used by MonteCarloService to build the historical P&L population.
        """
        try:
            return (
                await BacktestTrade.find(
                    {
                        "strategy_id": strategy_id,
                        "exit_reason": {"$ne": ExitReason.NO_BREAKOUT.value},
                    }
                )
                .sort("trading_date")
                .limit(limit)
                .to_list()
            )
        except Exception as exc:
            raise DatabaseException(
                f"Failed to fetch executed trades for strategy '{strategy_id}'.",
                detail=str(exc),
            )

    async def delete_trades_for_run(self, run_id: str) -> int:
        """Delete all trade documents for a run. Returns count deleted."""
        try:
            collection = BacktestTrade.get_motor_collection()
            result = await collection.delete_many({"run_id": run_id})
            return result.deleted_count
        except Exception as exc:
            raise DatabaseException(
                f"Failed to delete trades for run {run_id}.", detail=str(exc)
            )
