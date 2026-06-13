"""
Daily shortlist API routes.

GET  /api/v1/shortlist/today           — Today's tradable shortlist
POST /api/v1/shortlist/run             — Manually trigger a shortlist run
GET  /api/v1/shortlist/status          — Current state of the run manager
GET  /api/v1/shortlist/tradable-stocks — All stocks with tradable=True
GET  /api/v1/shortlist/{date}          — Shortlist for a specific date

Route declaration order matters: literal paths (/run, /status, /today,
/tradable-stocks) MUST be declared before the dynamic /{target_date} or
FastAPI will route "/run" to the date handler and fail to parse it as a date.

Routes call services only — no direct repository or Beanie access here.
"""

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Query

from app.schemas.strategy import (
    ShortlistEntryResponse,
    ShortlistResponse,
    ShortlistRunRequest,
    ShortlistRunResponse,
    ShortlistStatusResponse,
)
from app.services.shortlist_service import ShortlistService, shortlist_run_manager
from app.utils.logger import get_logger
from app.utils.trading_day import last_completed_trading_day

router = APIRouter()
logger = get_logger(__name__)

_shortlist_svc = ShortlistService()


@router.get(
    "/today",
    response_model=ShortlistResponse,
    summary="Today's tradable shortlist",
)
async def get_today_shortlist(
    probability_threshold: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Override the default probability threshold (0.0–1.0)",
    ),
) -> ShortlistResponse:
    """
    Generate the tradable shortlist for the current trading session.

    Targets the upcoming trading session — the day we are currently in or
    about to trade (today before the 15:30 IST close, otherwise the next
    trading day). So a pre-market or intraday view shows *today's* list, not
    yesterday's.

    Logic:
      - Looks up the prior session's one-side day records (the setup day)
      - Filters by continuation probability >= threshold
      - Returns sorted by probability (highest edge first)

    This call is safe to make multiple times — the result is deterministic
    for a given trading day. If it returns empty because the previous
    evening's pipeline was missed, trigger POST /api/v1/shortlist/run to
    rebuild it on demand.
    """
    result = await _shortlist_svc.generate_shortlist(
        probability_threshold=probability_threshold,
    )
    return _build_response(result)


@router.post(
    "/run",
    response_model=ShortlistRunResponse,
    summary="Manually trigger a shortlist run",
)
async def run_shortlist(
    body: ShortlistRunRequest = Body(default_factory=ShortlistRunRequest),
) -> ShortlistRunResponse:
    """
    Manual / fallback shortlist run.

    Two modes (controlled by `full_pipeline`):
      * `full_pipeline=True` (default) — Fallback for when the daily cron has
        not run. Pulls today's candles from Angel One, runs OSD detection,
        recomputes continuation statistics, and then generates the shortlist.
        This mirrors the full daily chain (15:45 → 16:30 IST) in one call.
      * `full_pipeline=False` — Fast read path. Skips all upstream steps and
        only filters existing MongoDB data; this is what the 16:30 IST
        scheduler job uses.

    Single-flight: a 409 Conflict is returned if another run (manual or
    scheduler) is in progress. Defaults `target_date` to the upcoming
    trading session so a morning or intraday recovery run targets today's list.
    """
    logger.info(
        "Received manual shortlist run request: target_date=%s threshold=%s full_pipeline=%s",
        body.target_date, body.probability_threshold, body.full_pipeline,
    )
    result = await shortlist_run_manager.run(
        target_date=body.target_date,
        probability_threshold=body.probability_threshold,
        trigger="manual",
        full_pipeline=body.full_pipeline,
    )

    metrics = result.pipeline_metrics
    return ShortlistRunResponse(
        status="success",
        target_date=result.target_date,
        total_checked=result.total_candidates_checked,
        total_shortlisted=sum(1 for e in result.entries if e.tradable),
        duration_seconds=round(result.duration_seconds, 3),
        threshold_pct=round(result.threshold_used * 100, 2),
        full_pipeline=body.full_pipeline,
        data_date=metrics.data_date if metrics else None,
        candles_synced=metrics.candles_synced if metrics else None,
        sync_failed_symbols=metrics.sync_failed_symbols if metrics else None,
        osd_one_side_days=metrics.osd_one_side_days if metrics else None,
        tradable_symbols=metrics.tradable_symbols if metrics else None,
    )


@router.get(
    "/status",
    response_model=ShortlistStatusResponse,
    summary="Current shortlist run status",
)
async def get_shortlist_status() -> ShortlistStatusResponse:
    """
    Return the latest run-manager state — useful for UIs that need to:
      * disable the "Run" button while a run is in flight, and
      * display the last successful run's stats.

    Reflects BOTH manual and scheduler runs since they share the manager.
    """
    snap = shortlist_run_manager.snapshot()
    return ShortlistStatusResponse(**snap.to_dict())


@router.get(
    "/tradable-stocks",
    response_model=list[ShortlistEntryResponse],
    summary="All stocks with tradable continuation probability",
)
async def get_tradable_stocks() -> list[ShortlistEntryResponse]:
    """
    Return all stocks that currently have tradable=True continuation statistics.

    These are stocks that have historically shown >= threshold continuation
    probability with a sufficient sample size.

    Note: This returns the full tradable universe, not filtered by yesterday's
    one-side day condition. Use /shortlist/today for the actionable daily list.
    """
    stats = await _shortlist_svc.get_tradable_stocks()
    ref_date = last_completed_trading_day()
    return [
        _build_entry_response_from_stat(s, reference_date=ref_date)
        for s in stats
    ]


@router.get(
    "/{target_date}",
    response_model=ShortlistResponse,
    summary="Shortlist for a specific date",
)
async def get_shortlist_for_date(
    target_date: date,
    probability_threshold: Optional[float] = Query(
        None, ge=0.0, le=1.0
    ),
) -> ShortlistResponse:
    """
    Generate the shortlist for any historical or future trading date.

    Useful for backtesting: "what would the shortlist have looked like on date X?"
    Data must be available in MongoDB for the date range to produce results.
    """
    result = await _shortlist_svc.generate_shortlist(
        target_date=target_date,
        probability_threshold=probability_threshold,
    )
    return _build_response(result)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ui_direction(raw: str) -> str:
    """Map strategy UP/DOWN to dashboard BULLISH/BEARISH."""
    upper = (raw or "").upper()
    if upper in ("UP", "BULLISH"):
        return "BULLISH"
    if upper in ("DOWN", "BEARISH"):
        return "BEARISH"
    return upper or "BULLISH"


def _orb_range_pct(high: float, low: float) -> float:
    if low <= 0:
        return 0.0
    return round((high - low) / low * 100, 2)


def _entry_and_stop(ui_direction: str, orb_high: float, orb_low: float, breakout_price: Optional[float]) -> tuple[float, float]:
    if ui_direction == "BULLISH":
        entry = breakout_price if breakout_price is not None else orb_high
        return entry, orb_low
    entry = breakout_price if breakout_price is not None else orb_low
    return entry, orb_high


def _build_entry_response(entry) -> ShortlistEntryResponse:
    """Convert ShortlistEntry dataclass to API response."""
    ui_dir = _ui_direction(entry.direction)
    orb_high = entry.first_candle_high
    orb_low = entry.first_candle_low
    entry_trigger, stop_loss = _entry_and_stop(ui_dir, orb_high, orb_low, entry.breakout_price)
    prob = entry.continuation_probability

    return ShortlistEntryResponse(
        symbol=entry.symbol,
        direction=ui_dir,
        first_candle_high=orb_high,
        first_candle_low=orb_low,
        breakout_price=entry.breakout_price,
        move_percent=entry.move_percent,
        continuation_probability=prob,
        continuation_probability_pct=round(prob * 100, 2),
        total_occurrences=entry.total_occurrences,
        yesterday_date=entry.yesterday_date,
        orb_high=orb_high,
        orb_low=orb_low,
        entry_trigger=entry_trigger,
        stop_loss=stop_loss,
        probability=prob,
        first_candle_range_pct=_orb_range_pct(orb_high, orb_low),
        tradable=getattr(entry, "tradable", True),
        reason_skipped=getattr(entry, "reason_skipped", None),
    )


def _build_entry_response_from_stat(stat, reference_date: date) -> ShortlistEntryResponse:
    """Tradable-universe row (no yesterday OSD context)."""
    ui_dir = "BULLISH"
    orb_high = 0.0
    orb_low = 0.0
    entry_trigger, stop_loss = _entry_and_stop(ui_dir, orb_high, orb_low, None)
    prob = stat.continuation_probability

    return ShortlistEntryResponse(
        symbol=stat.symbol,
        direction=ui_dir,
        first_candle_high=orb_high,
        first_candle_low=orb_low,
        continuation_probability=prob,
        continuation_probability_pct=round(prob * 100, 2),
        total_occurrences=stat.total_occurrences,
        yesterday_date=reference_date,
        orb_high=orb_high,
        orb_low=orb_low,
        entry_trigger=entry_trigger,
        stop_loss=stop_loss,
        probability=prob,
        first_candle_range_pct=0.0,
        tradable=stat.tradable,
        reason_skipped=stat.metadata.get("rejection_reason") if not stat.tradable else None,
    )


def _build_response(result, generated_at: Optional[datetime] = None) -> ShortlistResponse:
    """Convert ShortlistResult to ShortlistResponse schema."""
    entries = [_build_entry_response(e) for e in result.entries]
    when = generated_at or datetime.now(timezone.utc)
    pool = result.total_candidates_checked
    # total_tradable counts only entries that passed all gating checks;
    # `entries` may also contain skipped rows (tradable=False) so UI can
    # surface them with a "Skipped" badge.
    tradable = sum(1 for e in entries if e.tradable)

    return ShortlistResponse(
        trading_date=result.target_date,
        target_date=result.target_date,
        yesterday=result.yesterday,
        total_candidates=pool,
        total_tradable=tradable,
        total_checked=pool,
        threshold_pct=round(result.threshold_used * 100, 1),
        generated_at=when,
        entries=entries,
    )
