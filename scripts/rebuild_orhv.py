"""
One-off maintenance script: rebuild all ORHV-derived data from existing candles.

Why this exists
---------------
The ORHV detection (Condition A/B) and Phase 3 entry breakout were changed from
CLOSE-based to TOUCH-based (high/low). Every ORHV-derived document in MongoDB was
computed with the old logic and is therefore stale. Raw ``historical_candles``
are unaffected — only the derived collections need recomputation.

What it rebuilds (in dependency order)
--------------------------------------
  1. orhv_setups       — Phase 1 detection (re-run on every date with candle data)
  2. orhv_validations  — Phase 2 validation (lookback reads the rebuilt setups)
  3. orhv_statistics   — auto-updated as a side effect of Phase 2
  4. orhv_signals      — wiped (past live signals are discarded)

Ordering matters: Phase 2's historical lookback reads stored ``orhv_setups`` via
``get_candidates_before_date``. ALL detection must finish before ANY validation
runs, otherwise win rates undercount prior occurrences.

Prerequisites
-------------
  * .env configured with a reachable MONGO_URI.
  * Stock universe seeded (POST /api/v1/stocks/initialize) — detection iterates
    over active stocks.
  * historical_candles populated for the date range you want to rebuild.

Usage
-----
  .venv/bin/python scripts/rebuild_orhv.py                 # full history, drop + rebuild
  .venv/bin/python scripts/rebuild_orhv.py --from 2024-01-01 --to 2024-06-30
  .venv/bin/python scripts/rebuild_orhv.py --no-drop       # keep existing docs (upsert)
  .venv/bin/python scripts/rebuild_orhv.py --skip-validation

  # Backfill candles from Angel One FIRST, then detect + validate. This is what
  # you need when the DB has little/no history (otherwise prior occurrences = 0).
  .venv/bin/python scripts/rebuild_orhv.py --backfill-days 400
  .venv/bin/python scripts/rebuild_orhv.py --backfill-from 2024-01-01
  .venv/bin/python scripts/rebuild_orhv.py --backfill-days 400 --symbols UPL,RELIANCE
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Ensure the repo root is importable when run as `python scripts/rebuild_orhv.py`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.database.mongodb import connect_db, disconnect_db  # noqa: E402
from app.services.historical_data_service import HistoricalDataService  # noqa: E402
from app.services.orhv_service import ORHVService  # noqa: E402
from app.utils.trading_day import last_completed_trading_day  # noqa: E402
from app.strategy.strategies.opening_range_historical_validation.models import (  # noqa: E402
    ORHVSetup,
    ORHVSignalRecord,
    ORHVStatistics,
    ORHVValidationRecord,
)
from app.models.historical_candle import HistoricalCandle  # noqa: E402
from app.utils.candle_intervals import CandleInterval  # noqa: E402
from app.utils.logger import get_logger  # noqa: E402

logger = get_logger("rebuild_orhv")


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


async def _distinct_candle_dates(interval: str) -> list[date]:
    """Return the sorted distinct trading dates that have candle data."""
    raw = await HistoricalCandle.get_pymongo_collection().distinct(
        "trading_date", {"interval": interval}
    )
    dates: set[date] = set()
    for dt in raw:
        if isinstance(dt, datetime):
            dates.add(dt.date())
    return sorted(dates)


async def _backfill_candles(
    from_date: date,
    to_date: date,
    interval: str,
    symbols: list[str] | None,
) -> None:
    """
    Fetch historical candles from Angel One for [from_date, to_date].

    This is the missing piece when the DB has little history: Phase 2 can only
    find prior occurrences for days that were detected, and detection only runs
    on days that have candles. So we must pull the candles first.
    """
    logger.info(
        "=== Candle backfill: %s → %s (%s)%s ===",
        from_date, to_date, interval,
        f" for {symbols}" if symbols else " for ALL active stocks",
    )
    svc = HistoricalDataService()
    result = await svc.sync_historical_data(
        from_date=from_date,
        to_date=to_date,
        interval=CandleInterval(interval),
        symbols=symbols,
    )
    logger.info(
        "Candle backfill complete: %d ok / %d skipped / %d failed | %d buckets inserted | %.1fs",
        result.successful, result.skipped, result.failed,
        result.records_inserted, result.duration_seconds,
    )
    if result.failed_symbols:
        logger.warning(
            "Backfill failed for %d symbol(s): %s",
            len(result.failed_symbols),
            ", ".join(result.failed_symbols[:20]) + ("…" if len(result.failed_symbols) > 20 else ""),
        )


async def _drop_derived_collections(wipe_signals: bool) -> None:
    logger.info("Clearing derived ORHV collections…")
    n_setups = await ORHVSetup.delete_all()
    n_vals = await ORHVValidationRecord.delete_all()
    n_stats = await ORHVStatistics.delete_all()
    logger.info(
        "Deleted: %s setups, %s validations, %s statistics.",
        getattr(n_setups, "deleted_count", n_setups),
        getattr(n_vals, "deleted_count", n_vals),
        getattr(n_stats, "deleted_count", n_stats),
    )
    if wipe_signals:
        n_sig = await ORHVSignalRecord.delete_all()
        logger.info(
            "Wiped orhv_signals: %s removed.",
            getattr(n_sig, "deleted_count", n_sig),
        )


async def rebuild(
    from_date: date | None,
    to_date: date | None,
    drop: bool,
    wipe_signals: bool,
    skip_validation: bool,
    interval: str,
    backfill_from: date | None,
    symbols: list[str] | None,
) -> None:
    t0 = time.monotonic()
    await connect_db()
    try:
        # ── Candle backfill (optional) ────────────────────────────────────────
        # Must run BEFORE computing distinct dates so detection sees fresh data.
        if backfill_from:
            await _backfill_candles(
                from_date=backfill_from,
                to_date=to_date or last_completed_trading_day(),
                interval=interval,
                symbols=symbols,
            )

        all_dates = await _distinct_candle_dates(interval)
        if not all_dates:
            logger.error(
                "No %s candle data found in historical_candles. "
                "Seed the universe and sync candles first.",
                interval,
            )
            return

        if from_date:
            all_dates = [d for d in all_dates if d >= from_date]
        if to_date:
            all_dates = [d for d in all_dates if d <= to_date]

        if not all_dates:
            logger.error("No candle dates in the requested range.")
            return

        logger.info(
            "Rebuilding ORHV over %d trading day(s): %s → %s",
            len(all_dates), all_dates[0], all_dates[-1],
        )

        if drop and symbols:
            logger.warning(
                "--symbols given: skipping global drop (it would wipe other symbols). "
                "Setups/validations will be upserted in place."
            )
        elif drop:
            await _drop_derived_collections(wipe_signals=wipe_signals)
        elif wipe_signals:
            await ORHVSignalRecord.delete_all()
            logger.info("Wiped orhv_signals (kept setups/validations for upsert).")

        svc = ORHVService()

        # ── Phase 1: detection across ALL dates first ─────────────────────────
        logger.info("=== Phase 1: detection ===")
        total_candidates = 0
        for i, d in enumerate(all_dates, start=1):
            summary = await svc.run_detection_for_date(trading_date=d, symbols=symbols)
            total_candidates += summary.candidates_found
            logger.info(
                "[detect %d/%d] %s — %d candidates / %d rejected / %d no-data",
                i, len(all_dates), d,
                summary.candidates_found, summary.rejected, summary.no_data,
            )
        logger.info("Phase 1 complete: %d total candidates.", total_candidates)

        # ── Phase 2: validation (after all setups exist) ──────────────────────
        if skip_validation:
            logger.info("Skipping Phase 2 validation (--skip-validation).")
        else:
            logger.info("=== Phase 2: validation ===")
            total_tradable = 0
            for i, d in enumerate(all_dates, start=1):
                summary = await svc.run_validation_for_date(candidate_date=d, symbols=symbols)
                total_tradable += summary.tradable
                if summary.total_candidates:
                    logger.info(
                        "[validate %d/%d] %s — %d tradable / %d not / %d insufficient (of %d)",
                        i, len(all_dates), d,
                        summary.tradable, summary.not_tradable,
                        summary.insufficient_history, summary.total_candidates,
                    )
            logger.info("Phase 2 complete: %d tradable validations.", total_tradable)

        logger.info(
            "ORHV rebuild finished in %.1fs.", time.monotonic() - t0
        )
    finally:
        await disconnect_db()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild ORHV-derived MongoDB data.")
    parser.add_argument("--from", dest="from_date", type=_parse_date, default=None,
                        help="Start date (YYYY-MM-DD). Defaults to earliest candle date.")
    parser.add_argument("--to", dest="to_date", type=_parse_date, default=None,
                        help="End date (YYYY-MM-DD). Defaults to latest candle date.")
    parser.add_argument("--no-drop", dest="drop", action="store_false",
                        help="Do not clear collections first (upsert in place).")
    parser.add_argument("--keep-signals", dest="wipe_signals", action="store_false",
                        help="Keep orhv_signals (default: wipe them).")
    parser.add_argument("--skip-validation", action="store_true",
                        help="Run Phase 1 detection only.")
    parser.add_argument("--interval", default=CandleInterval.FIFTEEN_MINUTE.value,
                        help="Candle interval to use (default FIFTEEN_MINUTE).")
    parser.add_argument("--backfill-days", dest="backfill_days", type=int, default=None,
                        help="Fetch candles from Angel One for the last N calendar days "
                             "BEFORE detection (e.g. 400 for ~1 year).")
    parser.add_argument("--backfill-from", dest="backfill_from", type=_parse_date, default=None,
                        help="Fetch candles from Angel One starting at this date "
                             "(YYYY-MM-DD). Overrides --backfill-days.")
    parser.add_argument("--symbols", dest="symbols", default=None,
                        help="Comma-separated symbols to limit backfill + rebuild "
                             "(e.g. UPL,RELIANCE). Default: all active stocks.")
    parser.set_defaults(drop=True, wipe_signals=True)
    args = parser.parse_args()

    backfill_from: date | None = args.backfill_from
    if backfill_from is None and args.backfill_days is not None:
        backfill_from = date.today() - timedelta(days=args.backfill_days)

    symbols: list[str] | None = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    asyncio.run(rebuild(
        from_date=args.from_date,
        to_date=args.to_date,
        drop=args.drop,
        wipe_signals=args.wipe_signals,
        skip_validation=args.skip_validation,
        interval=args.interval,
        backfill_from=backfill_from,
        symbols=symbols,
    ))


if __name__ == "__main__":
    main()
