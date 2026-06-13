"""Tests for the Angel One scrip-master service."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.brokers.angelone.scrip_master import ScripMasterService


def _write_master(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))


@pytest.mark.asyncio
async def test_nse_equities_filters_cash_segment(tmp_path: Path) -> None:
    cache = tmp_path / "scrip.json"
    _write_master(
        cache,
        [
            # Cash-segment equities — should be returned.
            {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
             "exch_seg": "NSE", "instrumenttype": ""},
            {"token": "11536", "symbol": "TCS-EQ", "name": "TCS",
             "exch_seg": "NSE", "instrumenttype": ""},
            # Same name but BSE — should be excluded.
            {"token": "9999", "symbol": "RELIANCE", "name": "RELIANCE",
             "exch_seg": "BSE", "instrumenttype": ""},
            # F&O contract — should be excluded.
            {"token": "12345", "symbol": "RELIANCE25DECFUT", "name": "RELIANCE",
             "exch_seg": "NFO", "instrumenttype": "FUTSTK"},
            # Non-EQ series (e.g. BE) — should be excluded.
            {"token": "5555", "symbol": "ABCD-BE", "name": "ABCD",
             "exch_seg": "NSE", "instrumenttype": ""},
        ],
    )

    svc = ScripMasterService(cache_path=cache, ttl_hours=24)
    mapping = await svc.nse_equities()

    assert mapping == {"RELIANCE": "2885", "TCS": "11536"}


@pytest.mark.asyncio
async def test_lookup_token_is_case_insensitive(tmp_path: Path) -> None:
    cache = tmp_path / "scrip.json"
    _write_master(
        cache,
        [
            {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
             "exch_seg": "NSE", "instrumenttype": ""},
        ],
    )
    svc = ScripMasterService(cache_path=cache, ttl_hours=24)

    assert await svc.lookup_token("reliance") == "2885"
    assert await svc.lookup_token("RELIANCE") == "2885"
    assert await svc.lookup_token("UNKNOWN") is None


@pytest.mark.asyncio
async def test_in_memory_cache_invalidates_on_file_change(tmp_path: Path) -> None:
    cache = tmp_path / "scrip.json"
    _write_master(
        cache,
        [
            {"token": "1", "symbol": "A-EQ", "name": "A",
             "exch_seg": "NSE", "instrumenttype": ""},
        ],
    )
    svc = ScripMasterService(cache_path=cache, ttl_hours=24)

    first = await svc.nse_equities()
    assert first == {"A": "1"}

    # Rewrite cache with a different payload and bump mtime so freshness still holds.
    _write_master(
        cache,
        [
            {"token": "2", "symbol": "B-EQ", "name": "B",
             "exch_seg": "NSE", "instrumenttype": ""},
        ],
    )
    new_mtime = time.time() + 1
    cache.touch()
    import os
    os.utime(cache, (new_mtime, new_mtime))

    second = await svc.nse_equities()
    assert second == {"B": "2"}
