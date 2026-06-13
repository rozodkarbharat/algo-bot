"""
Angel One scrip-master client.

Angel One publishes a daily JSON file with every tradable instrument across
all exchanges. We download it, cache it on disk for 24h, and expose a small
filtering API so other code can resolve `symbol -> instrument_token` without
talking to the network.

Public URL (no auth):
    https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json

Each row roughly looks like::

    {
        "token": "2885",
        "symbol": "RELIANCE-EQ",
        "name": "RELIANCE",
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": "",
        "exch_seg": "NSE",
        "tick_size": "5.000000"
    }

For NSE equity we filter by `exch_seg == "NSE"`, empty `instrumenttype`, and
`symbol` ending in ``-EQ`` (the cash-segment marker).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from app.config.settings import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
_DEFAULT_TTL_HOURS = 24


@dataclass(frozen=True)
class ScripMasterRow:
    token: str
    name: str           # clean ticker, e.g. "RELIANCE"
    symbol: str         # exchange symbol, e.g. "RELIANCE-EQ"
    exch_seg: str       # "NSE" | "BSE" | "NFO" | "MCX" | "CDS"
    instrumenttype: str


class ScripMasterService:
    """
    Asynchronous, file-backed cache around the Angel One scrip master JSON.

    Usage:
        master = ScripMasterService()
        await master.refresh()                  # downloads + caches
        tokens = await master.nse_equities()    # {"RELIANCE": "2885", ...}
        token = await master.lookup_token("TCS")
    """

    def __init__(
        self,
        url: str = _SCRIP_MASTER_URL,
        ttl_hours: float = _DEFAULT_TTL_HOURS,
        cache_path: Optional[Path] = None,
    ) -> None:
        self._url = url
        self._ttl_seconds = ttl_hours * 3600
        self._cache_path = cache_path or Path(settings.CACHE_DIR) / "angelone_scrip_master.json"
        self._lock = asyncio.Lock()
        self._mem_cache_signature: Optional[float] = None
        self._mem_nse_eq: Optional[dict[str, str]] = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def refresh(self, force: bool = False) -> Path:
        """Download the scrip master if the on-disk cache is stale or missing."""
        async with self._lock:
            if not force and self._is_fresh():
                return self._cache_path

            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Fetching Angel One scrip master from %s …", self._url)
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
            data = resp.text
            self._cache_path.write_text(data)
            self._mem_cache_signature = None  # invalidate in-memory cache
            self._mem_nse_eq = None
            logger.info(
                "Angel One scrip master cached: %s (%.1f MB)",
                self._cache_path, len(data) / (1024 * 1024),
            )
            return self._cache_path

    async def nse_equities(self) -> dict[str, str]:
        """
        Return a mapping of NSE equity ticker -> Angel One token.

        Filters to the NSE cash segment: ``exch_seg=NSE``, empty
        ``instrumenttype``, and ``symbol`` ending in ``-EQ``.
        """
        await self.refresh()
        signature = self._cache_path.stat().st_mtime
        if self._mem_nse_eq is not None and self._mem_cache_signature == signature:
            return self._mem_nse_eq

        rows = self._load_rows()
        mapping: dict[str, str] = {}
        for row in rows:
            if row.exch_seg != "NSE":
                continue
            if row.instrumenttype:
                continue
            if not row.symbol.endswith("-EQ"):
                continue
            mapping[row.name.upper()] = row.token

        self._mem_nse_eq = mapping
        self._mem_cache_signature = signature
        logger.info("Scrip master parsed: %d NSE equity instruments.", len(mapping))
        return mapping

    async def lookup_token(self, symbol: str) -> Optional[str]:
        """Return the Angel One token for an NSE equity symbol (case-insensitive)."""
        mapping = await self.nse_equities()
        return mapping.get(symbol.upper())

    # ── Internal ──────────────────────────────────────────────────────────────

    def _is_fresh(self) -> bool:
        if not self._cache_path.exists():
            return False
        age = time.time() - self._cache_path.stat().st_mtime
        return age < self._ttl_seconds

    def _load_rows(self) -> list[ScripMasterRow]:
        try:
            raw = json.loads(self._cache_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Scrip master cache unreadable: {exc}") from exc

        out: list[ScripMasterRow] = []
        for entry in raw:
            try:
                out.append(
                    ScripMasterRow(
                        token=str(entry.get("token", "")).strip(),
                        name=str(entry.get("name", "")).strip(),
                        symbol=str(entry.get("symbol", "")).strip(),
                        exch_seg=str(entry.get("exch_seg", "")).strip(),
                        instrumenttype=str(entry.get("instrumenttype", "")).strip(),
                    )
                )
            except (AttributeError, TypeError):
                continue
        return out


scrip_master = ScripMasterService()
