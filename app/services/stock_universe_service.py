"""
Stock universe service.

Manages the authoritative list of instruments the system tracks.
Initially pre-loaded with the NIFTY50 constituent stocks.

Architecture note:
  This service owns the "what symbols do we track?" question.
  The historical data service asks this service for the active
  symbol list — it never queries the DB directly.

Extending to NIFTY100 / NIFTY200:
  1. Add the symbol list below.
  2. Pass index="NIFTY100" to initialise_universe().
  3. Scheduler jobs can target specific indices.

Instrument tokens:
  The `instrument_token` values below are Angel One's `symboltoken` identifiers.
  These MUST be verified against the Angel One scrip master before use:
    URL: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
  Filter by: exchange="NSE", instrumenttype="EQ", and match the symbol name.
"""

from app.core.exceptions import DatabaseException
from app.models.stock import Stock
from app.repositories.stock_repository import StockRepository
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Static universe data ──────────────────────────────────────────────────────
# Token source: Angel One SmartAPI scrip master (NSE EQ segment)
# Last verified: 2024 — re-download and verify before production use.

_NIFTY50_STOCKS: list[dict] = [
    {"symbol": "RELIANCE",    "company_name": "Reliance Industries",           "instrument_token": "2885",  "sector": "Energy"},
    {"symbol": "TCS",         "company_name": "Tata Consultancy Services",      "instrument_token": "11536", "sector": "Information Technology"},
    {"symbol": "HDFCBANK",    "company_name": "HDFC Bank",                      "instrument_token": "1333",  "sector": "Financials"},
    {"symbol": "INFY",        "company_name": "Infosys",                        "instrument_token": "1594",  "sector": "Information Technology"},
    {"symbol": "ICICIBANK",   "company_name": "ICICI Bank",                     "instrument_token": "4963",  "sector": "Financials"},
    {"symbol": "HINDUNILVR",  "company_name": "Hindustan Unilever",             "instrument_token": "1394",  "sector": "Consumer Staples"},
    {"symbol": "ITC",         "company_name": "ITC",                            "instrument_token": "1660",  "sector": "Consumer Staples"},
    {"symbol": "SBIN",        "company_name": "State Bank of India",            "instrument_token": "3045",  "sector": "Financials"},
    {"symbol": "BHARTIARTL",  "company_name": "Bharti Airtel",                  "instrument_token": "10604", "sector": "Communication Services"},
    {"symbol": "KOTAKBANK",   "company_name": "Kotak Mahindra Bank",            "instrument_token": "1922",  "sector": "Financials"},
    {"symbol": "LT",          "company_name": "Larsen & Toubro",                "instrument_token": "11483", "sector": "Industrials"},
    {"symbol": "ASIANPAINT",  "company_name": "Asian Paints",                   "instrument_token": "236",   "sector": "Materials"},
    {"symbol": "MARUTI",      "company_name": "Maruti Suzuki India",            "instrument_token": "10999", "sector": "Consumer Discretionary"},
    {"symbol": "AXISBANK",    "company_name": "Axis Bank",                      "instrument_token": "5900",  "sector": "Financials"},
    {"symbol": "BAJFINANCE",  "company_name": "Bajaj Finance",                  "instrument_token": "317",   "sector": "Financials"},
    {"symbol": "WIPRO",       "company_name": "Wipro",                          "instrument_token": "3787",  "sector": "Information Technology"},
    {"symbol": "HCLTECH",     "company_name": "HCL Technologies",               "instrument_token": "7229",  "sector": "Information Technology"},
    {"symbol": "SUNPHARMA",   "company_name": "Sun Pharmaceutical Industries",  "instrument_token": "3351",  "sector": "Health Care"},
    {"symbol": "TITAN",       "company_name": "Titan Company",                  "instrument_token": "3506",  "sector": "Consumer Discretionary"},
    {"symbol": "ULTRACEMCO",  "company_name": "UltraTech Cement",               "instrument_token": "11532", "sector": "Materials"},
    {"symbol": "NESTLEIND",   "company_name": "Nestle India",                   "instrument_token": "17963", "sector": "Consumer Staples"},
    {"symbol": "BAJAJFINSV",  "company_name": "Bajaj Finserv",                  "instrument_token": "16675", "sector": "Financials"},
    {"symbol": "TATAMOTORS",  "company_name": "Tata Motors",                    "instrument_token": "3456",  "sector": "Consumer Discretionary"},
    {"symbol": "POWERGRID",   "company_name": "Power Grid Corporation of India","instrument_token": "14977", "sector": "Utilities"},
    {"symbol": "NTPC",        "company_name": "NTPC",                           "instrument_token": "11630", "sector": "Utilities"},
    {"symbol": "TATASTEEL",   "company_name": "Tata Steel",                     "instrument_token": "3499",  "sector": "Materials"},
    {"symbol": "TECHM",       "company_name": "Tech Mahindra",                  "instrument_token": "13538", "sector": "Information Technology"},
    {"symbol": "JSWSTEEL",    "company_name": "JSW Steel",                      "instrument_token": "11723", "sector": "Materials"},
    {"symbol": "ONGC",        "company_name": "Oil and Natural Gas Corporation","instrument_token": "2475",  "sector": "Energy"},
    {"symbol": "GRASIM",      "company_name": "Grasim Industries",              "instrument_token": "1232",  "sector": "Materials"},
    {"symbol": "ADANIENT",    "company_name": "Adani Enterprises",              "instrument_token": "25",    "sector": "Industrials"},
    {"symbol": "ADANIPORTS",  "company_name": "Adani Ports and Special Economic Zone", "instrument_token": "15083", "sector": "Industrials"},
    {"symbol": "APOLLOHOSP",  "company_name": "Apollo Hospitals Enterprise",    "instrument_token": "157",   "sector": "Health Care"},
    {"symbol": "BPCL",        "company_name": "Bharat Petroleum Corporation",   "instrument_token": "526",   "sector": "Energy"},
    {"symbol": "BRITANNIA",   "company_name": "Britannia Industries",           "instrument_token": "547",   "sector": "Consumer Staples"},
    {"symbol": "CIPLA",       "company_name": "Cipla",                          "instrument_token": "694",   "sector": "Health Care"},
    {"symbol": "COALINDIA",   "company_name": "Coal India",                     "instrument_token": "20374", "sector": "Energy"},
    {"symbol": "DIVISLAB",    "company_name": "Divi's Laboratories",            "instrument_token": "10940", "sector": "Health Care"},
    {"symbol": "DRREDDY",     "company_name": "Dr. Reddy's Laboratories",       "instrument_token": "881",   "sector": "Health Care"},
    {"symbol": "EICHERMOT",   "company_name": "Eicher Motors",                  "instrument_token": "910",   "sector": "Consumer Discretionary"},
    {"symbol": "HEROMOTOCO",  "company_name": "Hero MotoCorp",                  "instrument_token": "1348",  "sector": "Consumer Discretionary"},
    {"symbol": "HINDALCO",    "company_name": "Hindalco Industries",            "instrument_token": "1363",  "sector": "Materials"},
    {"symbol": "INDUSINDBK",  "company_name": "IndusInd Bank",                  "instrument_token": "5258",  "sector": "Financials"},
    {"symbol": "MM",          "company_name": "Mahindra & Mahindra",            "instrument_token": "2031",  "sector": "Consumer Discretionary"},
    {"symbol": "SBILIFE",     "company_name": "SBI Life Insurance Company",     "instrument_token": "21808", "sector": "Financials"},
    {"symbol": "SHRIRAMFIN",  "company_name": "Shriram Finance",                "instrument_token": "4306",  "sector": "Financials"},
    {"symbol": "TATACONSUM",  "company_name": "Tata Consumer Products",         "instrument_token": "3432",  "sector": "Consumer Staples"},
    {"symbol": "TRENT",       "company_name": "Trent",                          "instrument_token": "1964",  "sector": "Consumer Discretionary"},
    {"symbol": "LTIM",        "company_name": "LTIMindtree",                    "instrument_token": "17818", "sector": "Information Technology"},
    {"symbol": "HDFCLIFE",    "company_name": "HDFC Life Insurance Company",    "instrument_token": "467",   "sector": "Financials"},
    {"symbol": "BEL",         "company_name": "Bharat Electronics",             "instrument_token": "383",   "sector": "Industrials"},
]

# ── Service ───────────────────────────────────────────────────────────────────

class StockUniverseService:
    """
    Manages the stock universe lifecycle.

    Responsibilities:
      - Seed the database with NIFTY50 stocks
      - Provide the canonical list of symbols for data ingestion
      - Support future index expansions (NIFTY100, NIFTY200, custom)
    """

    def __init__(self) -> None:
        self._repo = StockRepository()

    # ── Public API ────────────────────────────────────────────────────────────

    async def initialise_universe(self, index: str = "NIFTY50") -> int:
        """
        Seed MongoDB with the static stock universe for the given index.

        Idempotent — stocks that already exist are upserted, not duplicated.
        Returns the number of newly inserted stocks.
        """
        universe = self._get_universe_data(index)
        if not universe:
            logger.warning("No stock data found for index '%s'.", index)
            return 0

        stocks = [
            Stock(
                symbol=s["symbol"],
                exchange="NSE",
                instrument_token=s["instrument_token"],
                company_name=s["company_name"],
                indices=[index],
                sector=s.get("sector"),
                is_active=True,
            )
            for s in universe
        ]

        inserted = await self._repo.bulk_insert_stocks(stocks, skip_duplicates=True)
        logger.info(
            "Universe '%s' initialised: %d new stocks added (total=%d).",
            index, inserted, len(stocks),
        )
        return inserted

    async def get_active_symbols(self, index: str | None = None) -> list[str]:
        """
        Return active ticker symbols, optionally filtered by index membership.

        Args:
            index: e.g. "NIFTY50" — None returns all active symbols.
        """
        if index:
            stocks = await self._repo.get_stocks_by_index(index)
        else:
            stocks = await self._repo.get_all_active_stocks()
        return [s.symbol for s in stocks]

    async def get_active_stocks(self, index: str | None = None) -> list[Stock]:
        """Return full Stock documents, optionally filtered by index."""
        if index:
            return await self._repo.get_stocks_by_index(index)
        return await self._repo.get_all_active_stocks()

    async def get_stock(self, symbol: str) -> Stock | None:
        """Return a single Stock by symbol."""
        return await self._repo.get_stock_by_symbol(symbol)

    async def add_stock(self, stock: Stock) -> Stock:
        """Register a new stock in the database."""
        return await self._repo.upsert_stock(stock)

    async def deactivate_stock(self, symbol: str) -> bool:
        """Mark a stock as inactive (excluded from future ingestion jobs)."""
        return await self._repo.deactivate_stock(symbol)

    async def get_stock_count(self) -> dict[str, int]:
        """Return active and total stock counts."""
        total = await self._repo.count()
        active = await self._repo.get_active_count()
        return {"total": total, "active": active}

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _get_universe_data(index: str) -> list[dict]:
        """Return the static data list for a given index name."""
        registry: dict[str, list[dict]] = {
            "NIFTY50": _NIFTY50_STOCKS,
            # Add NIFTY100, NIFTY200, etc. here as they are built out.
        }
        return registry.get(index, [])
