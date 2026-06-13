"""Tests for StockUniverseService.seed_universe_from_index — purely in-memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.services import stock_universe_service as universe_module
from app.services.stock_universe_service import StockUniverseService


# ── Stub backends ─────────────────────────────────────────────────────────────


class _StubScripMaster:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    async def nse_equities(self) -> dict[str, str]:
        return dict(self._mapping)

    async def lookup_token(self, symbol: str) -> Optional[str]:
        return self._mapping.get(symbol.upper())

    async def refresh(self, force: bool = False) -> None:  # noqa: ARG002
        return None


class _StubNseIndex:
    def __init__(self, symbols: list[str]) -> None:
        self._symbols = list(symbols)

    def supported_indices(self) -> list[str]:
        return ["NIFTY500"]

    async def get_symbols(self, index: str, force_refresh: bool = False) -> list[str]:  # noqa: ARG002
        return list(self._symbols)


@dataclass
class _FakeStock:
    """Lightweight stand-in mimicking the Stock model surface area we use."""

    symbol: str = ""
    exchange: str = "NSE"
    instrument_token: str = ""
    company_name: str = ""
    indices: list[str] = field(default_factory=list)
    sector: Optional[str] = None
    is_active: bool = True
    updated: bool = False

    def mark_updated(self) -> None:
        self.updated = True


class _StubRepo:
    def __init__(self) -> None:
        self._by_symbol: dict[str, _FakeStock] = {}

    async def get_stock_by_symbol(self, symbol: str) -> Optional[_FakeStock]:
        return self._by_symbol.get(symbol.upper())

    async def find_one_by_token(self, token: str) -> Optional[_FakeStock]:
        for stock in self._by_symbol.values():
            if stock.instrument_token == token:
                return stock
        return None

    async def create_stock(self, stock: _FakeStock) -> _FakeStock:
        fake = _FakeStock(
            symbol=stock.symbol,
            exchange=stock.exchange,
            instrument_token=stock.instrument_token,
            company_name=stock.company_name,
            indices=list(stock.indices),
            sector=stock.sector,
            is_active=stock.is_active,
        )
        self._by_symbol[fake.symbol.upper()] = fake
        return fake

    async def save(self, stock: _FakeStock) -> _FakeStock:
        self._by_symbol[stock.symbol.upper()] = stock
        return stock

    async def get_active_count(self) -> int:
        return sum(1 for s in self._by_symbol.values() if s.is_active)


# ── Tests ─────────────────────────────────────────────────────────────────────


class _StockClassProxy:
    """Proxy that emulates ``Stock(**kwargs)`` AND ``Stock.find_one({...})``."""

    def __init__(self, repo: "_StubRepo") -> None:
        self._repo = repo

    def __call__(self, **kwargs: object) -> _FakeStock:  # noqa: D401
        return _FakeStock(**kwargs)  # type: ignore[arg-type]

    async def find_one(self, query: dict) -> Optional[_FakeStock]:
        token = query.get("instrument_token")
        if not isinstance(token, str):
            return None
        return await self._repo.find_one_by_token(token)


@pytest.fixture()
def _repo() -> _StubRepo:
    return _StubRepo()


@pytest.fixture(autouse=True)
def _patch_stock_model(monkeypatch: pytest.MonkeyPatch, _repo: _StubRepo) -> None:
    """Replace Beanie's Stock with a proxy that works as both ctor and query."""
    monkeypatch.setattr(universe_module, "Stock", _StockClassProxy(_repo))
    # Stash the repo on the module so `_build_service` can re-use the same one.
    monkeypatch.setattr(universe_module, "_test_repo", _repo, raising=False)


def _build_service(symbols: list[str], token_map: dict[str, str]) -> tuple[StockUniverseService, _StubRepo]:
    svc = StockUniverseService(
        scrip_master_svc=_StubScripMaster(token_map),
        nse_index_svc=_StubNseIndex(symbols),
    )
    repo: _StubRepo = universe_module._test_repo  # type: ignore[attr-defined]
    svc._repo = repo  # type: ignore[assignment]
    return svc, repo


@pytest.mark.asyncio
async def test_seed_inserts_matched_symbols_and_reports_unmatched() -> None:
    svc, repo = _build_service(
        symbols=["RELIANCE", "TCS", "MYSTERYCORP"],
        token_map={"RELIANCE": "2885", "TCS": "11536"},
    )

    result = await svc.seed_universe_from_index("NIFTY500")

    assert result.index == "NIFTY500"
    assert result.total_symbols == 3
    assert result.inserted == 2
    assert result.updated == 0
    assert result.unmatched == ["MYSTERYCORP"]

    assert repo._by_symbol["RELIANCE"].instrument_token == "2885"
    assert repo._by_symbol["RELIANCE"].indices == ["NIFTY500"]
    assert repo._by_symbol["TCS"].instrument_token == "11536"


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_appends_index() -> None:
    svc, repo = _build_service(
        symbols=["RELIANCE"],
        token_map={"RELIANCE": "2885"},
    )
    repo._by_symbol["RELIANCE"] = _FakeStock(
        symbol="RELIANCE",
        exchange="NSE",
        instrument_token="2885",
        company_name="Reliance",
        indices=["NIFTY50"],
        sector=None,
        is_active=True,
    )

    result = await svc.seed_universe_from_index("NIFTY500")

    assert result.inserted == 0
    assert result.updated == 1
    assert repo._by_symbol["RELIANCE"].indices == ["NIFTY50", "NIFTY500"]


@pytest.mark.asyncio
async def test_seed_reactivates_and_retokens_existing_stock() -> None:
    svc, repo = _build_service(
        symbols=["TCS"],
        token_map={"TCS": "11536"},
    )
    repo._by_symbol["TCS"] = _FakeStock(
        symbol="TCS",
        exchange="NSE",
        instrument_token="OLD_TOKEN",
        company_name="Tata Consultancy",
        indices=["NIFTY500"],
        sector=None,
        is_active=False,
    )

    result = await svc.seed_universe_from_index("NIFTY500")

    assert result.inserted == 0
    assert result.updated == 1
    saved = repo._by_symbol["TCS"]
    assert saved.is_active is True
    assert saved.instrument_token == "11536"
    assert saved.updated is True


@pytest.mark.asyncio
async def test_seed_displaces_stale_token_holder() -> None:
    """A new symbol whose target token is held by an unrelated row should succeed."""
    svc, repo = _build_service(
        symbols=["NEWCO"],
        token_map={"NEWCO": "17818"},  # NEWCO's scrip-master token
    )
    # OLDCO is in DB and currently holds 17818 (stale — scrip master no longer
    # maps OLDCO to this token).
    repo._by_symbol["OLDCO"] = _FakeStock(
        symbol="OLDCO",
        exchange="NSE",
        instrument_token="17818",
        company_name="Old Co",
        indices=["NIFTY50"],
        sector=None,
        is_active=True,
    )

    result = await svc.seed_universe_from_index("NIFTY500")

    assert result.inserted == 1
    assert result.unmatched == []  # no conflicts
    assert repo._by_symbol["NEWCO"].instrument_token == "17818"
    # OLDCO got displaced: deactivated, token suffixed to free the unique index.
    assert repo._by_symbol["OLDCO"].is_active is False
    assert repo._by_symbol["OLDCO"].instrument_token.startswith("17818__stale_")


@pytest.mark.asyncio
async def test_seed_skips_save_when_nothing_changes() -> None:
    svc, repo = _build_service(
        symbols=["INFY"],
        token_map={"INFY": "1594"},
    )
    repo._by_symbol["INFY"] = _FakeStock(
        symbol="INFY",
        exchange="NSE",
        instrument_token="1594",
        company_name="Infosys",
        indices=["NIFTY500"],
        sector=None,
        is_active=True,
    )

    result = await svc.seed_universe_from_index("NIFTY500")

    assert result.inserted == 0
    assert result.updated == 0
    assert repo._by_symbol["INFY"].updated is False
