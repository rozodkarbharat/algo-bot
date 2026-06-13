"""
NSE index constituents fetcher.

NSE publishes the constituent lists of its broad-based indices as plain CSVs
in the archives:

    https://archives.nseindia.com/content/indices/ind_nifty50list.csv
    https://archives.nseindia.com/content/indices/ind_nifty100list.csv
    https://archives.nseindia.com/content/indices/ind_nifty200list.csv
    https://archives.nseindia.com/content/indices/ind_nifty500list.csv

Each CSV has the columns: Company Name, Industry, Symbol, Series, ISIN Code.
We pluck the ``Symbol`` column and cache the file to disk for 7 days
(constituent lists rebalance quarterly).

NSE blocks default Python user-agents — sending a normal browser UA is enough.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from pathlib import Path
from typing import Optional

import httpx

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


_INDEX_CSV_URLS: dict[str, str] = {
    "NIFTY50": "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY100": "https://archives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY200": "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "NIFTY500": "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
}

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_DEFAULT_TTL_HOURS = 24 * 7


class NSEIndexConstituentsService:
    """Async fetcher + on-disk cache for NSE index constituent lists."""

    def __init__(
        self,
        ttl_hours: float = _DEFAULT_TTL_HOURS,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self._ttl_seconds = ttl_hours * 3600
        self._cache_dir = cache_dir or Path(settings.CACHE_DIR) / "nse_index_constituents"
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def supported_indices(self) -> list[str]:
        return list(_INDEX_CSV_URLS.keys())

    async def get_symbols(self, index: str, force_refresh: bool = False) -> list[str]:
        """Return the canonical symbol list for ``index`` (e.g. ``"NIFTY500"``)."""
        index = index.upper()
        if index not in _INDEX_CSV_URLS:
            raise ValueError(
                f"Unknown NSE index '{index}'. Supported: {self.supported_indices()}"
            )

        path = self._cache_path(index)
        async with self._lock:
            if force_refresh or not self._is_fresh(path):
                await self._download(index, path)

        return self._parse_symbols(path)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _cache_path(self, index: str) -> Path:
        return self._cache_dir / f"{index.lower()}.csv"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        return (time.time() - path.stat().st_mtime) < self._ttl_seconds

    async def _download(self, index: str, dest: Path) -> None:
        url = _INDEX_CSV_URLS[index]
        dest.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Fetching %s constituents from %s …", index, url)
        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/csv,*/*"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        dest.write_text(resp.text)
        logger.info("%s constituents cached: %s (%d bytes)", index, dest, len(resp.text))

    @staticmethod
    def _parse_symbols(path: Path) -> list[str]:
        text = path.read_text()
        reader = csv.DictReader(io.StringIO(text))
        symbols: list[str] = []
        for row in reader:
            sym = (row.get("Symbol") or row.get("symbol") or "").strip().upper()
            if sym:
                symbols.append(sym)
        return symbols


nse_index_constituents = NSEIndexConstituentsService()
