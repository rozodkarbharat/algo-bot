"""Tests for the NSE index constituents fetcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.brokers.angelone.nse_index_constituents import NSEIndexConstituentsService


def _write_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Reliance Industries Ltd.,Energy,RELIANCE,EQ,INE002A01018\n"
        "Tata Consultancy Services Ltd.,IT,TCS,EQ,INE467B01029\n"
        "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"
    )


@pytest.mark.asyncio
async def test_parse_symbols_from_cached_csv(tmp_path: Path) -> None:
    svc = NSEIndexConstituentsService(cache_dir=tmp_path, ttl_hours=24)
    _write_csv(svc._cache_path("NIFTY500"))

    symbols = await svc.get_symbols("NIFTY500")
    assert symbols == ["RELIANCE", "TCS", "HDFCBANK"]


@pytest.mark.asyncio
async def test_rejects_unknown_index(tmp_path: Path) -> None:
    svc = NSEIndexConstituentsService(cache_dir=tmp_path, ttl_hours=24)
    with pytest.raises(ValueError, match="Unknown NSE index"):
        await svc.get_symbols("NIFTY9000")
