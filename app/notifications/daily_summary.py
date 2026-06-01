"""
Daily Summary Engine.

Queries MongoDB for today's trading activity and assembles a summary dict
that can be passed directly to notification templates.

Data gathered:
  - Total signals generated today (LiveSignal collection)
  - Closed paper trades today (PaperTrade collection)
  - Open paper positions today (PaperPosition collection, unrealized PnL)
  - Per-stock breakdown to identify top and worst performers

The engine is intentionally read-only: it never modifies any document.
It uses direct Beanie queries (via the repositories) to avoid re-implementing
query logic.

Schedule: dispatched by notification_jobs.py at 15:45 IST Mon–Fri.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.paper_position import PaperPosition
from app.models.paper_trade import PaperTrade
from app.models.live_signal import LiveSignal
from app.utils.logger import get_logger
from app.utils.market_time import IST

logger = get_logger(__name__)


def _today_utc_window() -> tuple[datetime, datetime]:
    """
    Return (start, end) UTC datetimes for today's NSE trading session.

    The 'day' is defined as midnight IST → midnight IST the following day,
    converted to UTC so MongoDB range queries work correctly.
    """
    now_ist = datetime.now(IST)
    day_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_ist = day_start_ist + timedelta(days=1)
    return day_start_ist.astimezone(timezone.utc), day_end_ist.astimezone(timezone.utc)


async def build_daily_summary(mode: str = "Paper") -> dict:
    """
    Assemble today's summary data dict.

    The returned dict matches the signature expected by both
    telegram_templates.daily_summary() and email_templates.daily_summary().

    Args:
        mode: "Paper" or "Live" — used as a display label only.

    Returns:
        Summary dict with keys:
          trading_date, total_signals, total_trades, winning_trades,
          losing_trades, realized_pnl, unrealized_pnl, top_stock,
          top_stock_pnl, worst_stock, worst_stock_pnl, mode, win_rate, total_pnl
    """
    start_utc, end_utc = _today_utc_window()
    trading_date = datetime.now(IST).strftime("%d %b %Y")

    try:
        signals_count = await _count_today_signals(start_utc, end_utc)
    except Exception as exc:
        logger.warning("Could not fetch today's signals for summary: %s", exc)
        signals_count = 0

    try:
        trades = await _fetch_today_trades(start_utc, end_utc)
    except Exception as exc:
        logger.warning("Could not fetch today's trades for summary: %s", exc)
        trades = []

    try:
        open_positions = await _fetch_open_positions(start_utc, end_utc)
    except Exception as exc:
        logger.warning("Could not fetch open positions for summary: %s", exc)
        open_positions = []

    # Trade statistics
    winning = [t for t in trades if t.pnl > 0]
    losing = [t for t in trades if t.pnl <= 0]
    realized_pnl = round(sum(t.pnl for t in trades), 2)
    unrealized_pnl = round(sum(p.unrealized_pnl for p in open_positions), 2)
    win_rate = (len(winning) / len(trades) * 100) if trades else 0.0

    # Per-symbol PnL for top/worst ranking
    symbol_pnl: dict[str, float] = {}
    for t in trades:
        symbol_pnl[t.symbol] = round(symbol_pnl.get(t.symbol, 0.0) + t.pnl, 2)

    top_stock: Optional[str] = None
    top_stock_pnl: Optional[float] = None
    worst_stock: Optional[str] = None
    worst_stock_pnl: Optional[float] = None

    if symbol_pnl:
        best = max(symbol_pnl, key=lambda s: symbol_pnl[s])
        worst = min(symbol_pnl, key=lambda s: symbol_pnl[s])
        top_stock = best
        top_stock_pnl = symbol_pnl[best]
        worst_stock = worst
        worst_stock_pnl = symbol_pnl[worst]
        # Only report worst if it's different from best
        if worst == best:
            worst_stock = None
            worst_stock_pnl = None

    summary = {
        "trading_date": trading_date,
        "total_signals": signals_count,
        "total_trades": len(trades),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": round(realized_pnl + unrealized_pnl, 2),
        "win_rate": round(win_rate, 2),
        "top_stock": top_stock,
        "top_stock_pnl": top_stock_pnl,
        "worst_stock": worst_stock,
        "worst_stock_pnl": worst_stock_pnl,
        "mode": mode,
    }

    logger.info(
        "Daily summary built: %d signals, %d trades, P&L ₹%.2f",
        signals_count, len(trades), realized_pnl + unrealized_pnl,
    )
    return summary


# ── Private helpers ────────────────────────────────────────────────────────────

async def _count_today_signals(start_utc: datetime, end_utc: datetime) -> int:
    return await LiveSignal.find(
        {"created_at": {"$gte": start_utc, "$lt": end_utc}}
    ).count()


async def _fetch_today_trades(start_utc: datetime, end_utc: datetime) -> list[PaperTrade]:
    return await PaperTrade.find(
        {"closed_at": {"$gte": start_utc, "$lt": end_utc}}
    ).to_list()


async def _fetch_open_positions(start_utc: datetime, end_utc: datetime) -> list[PaperPosition]:
    return await PaperPosition.find(
        {"trading_date": {"$gte": start_utc, "$lt": end_utc}, "status": "OPEN"}
    ).to_list()
