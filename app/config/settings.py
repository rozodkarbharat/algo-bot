"""
Central configuration module using Pydantic BaseSettings.

All environment variables are loaded here. This is the single source of truth
for application configuration across all environments (dev, staging, prod).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, MongoDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables or .env file.

    Pydantic BaseSettings automatically reads from environment variables
    and validates types at startup — misconfigured deployments fail fast.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = Field(default="TradingBot", description="Application name")
    APP_ENV: Literal["development", "staging", "production"] = Field(
        default="development", description="Deployment environment"
    )
    DEBUG: bool = Field(default=False, description="Enable debug mode")
    API_V1_PREFIX: str = Field(default="/api/v1", description="API v1 route prefix")

    # ── MongoDB ───────────────────────────────────────────────────────────────
    MONGO_URI: str = Field(
        default="mongodb://localhost:27017",
        description="MongoDB connection URI",
    )
    DATABASE_NAME: str = Field(default="trading_bot", description="MongoDB database name")
    MONGO_MAX_CONNECTIONS: int = Field(default=10, description="Motor connection pool size")
    MONGO_MIN_CONNECTIONS: int = Field(default=1, description="Motor min connection pool size")

    # ── Security ──────────────────────────────────────────────────────────────
    JWT_SECRET: str = Field(
        default="change-me-in-production",
        description="Secret key for JWT signing",
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30)

    # ── Broker — AngelOne ─────────────────────────────────────────────────────
    ANGELONE_API_KEY: str = Field(default="", description="AngelOne SmartAPI key")
    ANGELONE_CLIENT_ID: str = Field(default="", description="AngelOne client ID")
    ANGELONE_PASSWORD: str = Field(default="", description="AngelOne login password")
    ANGELONE_TOTP_SECRET: str = Field(default="", description="AngelOne TOTP secret for 2FA")

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="Allowed CORS origins (React dashboard)",
    )

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(default="INFO", description="Root log level")
    LOG_DIR: str = Field(default="logs", description="Directory for log files")

    # ── Scheduler ─────────────────────────────────────────────────────────────
    SCHEDULER_TIMEZONE: str = Field(
        default="Asia/Kolkata", description="Timezone for APScheduler jobs"
    )

    # ── Data ingestion ────────────────────────────────────────────────────────
    ANGELONE_BASE_URL: str = Field(
        default="https://apiconnect.angelone.in",
        description="Angel One SmartAPI base URL",
    )
    # Seconds to wait between consecutive Angel One API calls (rate-limit guard).
    INGESTION_API_DELAY_SECONDS: float = Field(
        default=0.5, description="Delay between Angel One API calls in seconds"
    )
    # Max symbols processed in parallel during historical ingestion.
    INGESTION_CONCURRENCY: int = Field(
        default=3, description="Concurrent symbols during historical data ingestion"
    )
    # Default historical fetch start date (ISO: YYYY-MM-DD).
    INGESTION_DEFAULT_START_DATE: str = Field(
        default="2020-01-01", description="Default start date for historical ingestion"
    )

    # ── Strategy Engine — One-Side Day ────────────────────────────────────────
    # Minimum % move from the ORB breakout level to the day extreme.
    # Days with a smaller move are classified as invalid (not one-sided).
    OSD_MIN_MOVE_PERCENT: float = Field(
        default=1.0,
        description="Minimum % move from ORB level for a valid one-side day",
    )
    # Continuation probability threshold above which a stock is tradable.
    # Must be met with a sufficient sample size (OSD_MIN_OCCURRENCES).
    OSD_CONTINUATION_THRESHOLD: float = Field(
        default=0.70,
        description="Minimum continuation probability (0.0–1.0) for tradable flag",
    )
    # Lookback window in trading days for probability calculation.
    # 252 ≈ 1 trading year, 1260 ≈ 5 trading years.
    OSD_LOOKBACK_DAYS: int = Field(
        default=252,
        description="Trading-day lookback window for continuation probability",
    )
    # Minimum number of one-side occurrences before the tradable flag can be set.
    # Prevents noise from symbols with very few historical OSD days.
    OSD_MIN_OCCURRENCES: int = Field(
        default=10,
        description="Minimum one-side occurrences required for tradable=True",
    )

    # ── Backtesting Engine ────────────────────────────────────────────────────
    # Capital allocated per trade in the simulation (₹).
    BACKTEST_CAPITAL_PER_TRADE: float = Field(
        default=100000.0,
        description="Capital allocated per simulated trade (₹)",
    )
    # Max first-candle ORB range on execution day; wider candles are skipped.
    BACKTEST_MAX_ORB_RANGE_PCT: float = Field(
        default=1.0,
        description="Max first-candle ORB range % on execution day before skipping",
    )
    # Latest 15-min candle open time (IST) that is eligible for entry.
    # "11:30" means candles opening AT or BEFORE 11:30 AM IST can trigger entry.
    BACKTEST_MAX_ENTRY_TIME_IST: str = Field(
        default="11:30",
        description="Latest candle time (HH:MM IST) allowed for breakout entry",
    )
    # Simulated slippage as a % of the entry/exit price.
    BACKTEST_SLIPPAGE_PCT: float = Field(
        default=0.05,
        description="Slippage % applied to simulated entry and exit prices",
    )
    # Flat brokerage cost per trade side (charged twice: entry + exit).
    BACKTEST_BROKERAGE_PER_SIDE: float = Field(
        default=20.0,
        description="Flat brokerage per trade side (₹) — applied to entry and exit",
    )
    # Stop-loss buffer as a % of the ORB boundary price.
    BACKTEST_SL_BUFFER_PCT: float = Field(
        default=0.0,
        description="Additional SL buffer % beyond the ORB boundary (default: exact boundary)",
    )
    # Number of trades to write to DB in each bulk-insert batch.
    BACKTEST_BATCH_SIZE: int = Field(
        default=100,
        description="Bulk-insert batch size when persisting simulated trades",
    )
    # Minimum continuation probability to qualify a symbol as a candidate.
    BACKTEST_PROB_THRESHOLD: float = Field(
        default=0.70,
        description="Minimum continuation probability for a symbol to enter the backtest shortlist",
    )

    # ── Paper Trading Engine ──────────────────────────────────────────────────
    # Starting virtual capital for the default paper-trading account (₹).
    PAPER_STARTING_CAPITAL: float = Field(
        default=1_000_000.0,
        description="Starting virtual capital for the paper-trading account (₹)",
    )
    # Capital deployed per paper trade (₹). Falls back to whatever the
    # account has available when this exceeds available_capital.
    PAPER_CAPITAL_PER_TRADE: float = Field(
        default=100_000.0,
        description="Capital deployed per paper trade (₹)",
    )
    # Slippage applied to entry/exit fills as a % of the trigger price.
    PAPER_SLIPPAGE_PCT: float = Field(
        default=0.05,
        description="Slippage % applied to paper-trading entry and exit fills",
    )
    # Flat brokerage charged on each side of a paper trade (entry + exit).
    PAPER_BROKERAGE_PER_SIDE: float = Field(
        default=20.0,
        description="Flat brokerage charged per paper trade side (₹)",
    )
    # Hard cap on simultaneously open paper positions.
    PAPER_MAX_OPEN_POSITIONS: int = Field(
        default=5,
        description="Maximum concurrent open paper positions",
    )
    # Hard cap on the number of paper entries taken in a single session.
    PAPER_MAX_TRADES_PER_DAY: int = Field(
        default=10,
        description="Maximum paper-trade entries per trading day",
    )
    # Daily loss limit as a % of starting capital — paper trading is paused
    # once realised + unrealised loss breaches this threshold.
    PAPER_MAX_DAILY_LOSS_PCT: float = Field(
        default=2.0,
        description="Max allowable daily loss as % of starting capital before pausing",
    )
    # Number of consecutive losing trades that triggers a cooldown.
    PAPER_CONSECUTIVE_LOSS_COOLDOWN: int = Field(
        default=3,
        description="Consecutive losing trades before risk manager triggers cooldown",
    )
    # EOD force-exit time (IST HH:MM) — all open paper positions are closed.
    PAPER_EOD_EXIT_TIME_IST: str = Field(
        default="15:15",
        description="IST time (HH:MM) at which all open paper positions are force-closed",
    )
    # Max position size as a % of account starting capital.
    PAPER_MAX_POSITION_PCT: float = Field(
        default=20.0,
        description="Max position size as % of starting capital (single trade)",
    )

    # ── Live Signal Engine ────────────────────────────────────────────────────
    # Max ORB range % allowed on the live execution day; wider candles are
    # skipped to avoid signalling on excessively volatile opens.
    LIVE_MAX_ORB_RANGE_PCT: float = Field(
        default=1.0,
        description="Max first-candle ORB range % allowed for live signal generation",
    )
    # Latest 15-min candle open time (IST) eligible for live entry.
    LIVE_MAX_ENTRY_TIME_IST: str = Field(
        default="11:30",
        description="Latest candle time (HH:MM IST) allowed for live breakout entry",
    )
    # Whether the candle builder should drop ticks outside NSE session hours.
    LIVE_RESPECT_MARKET_HOURS: bool = Field(
        default=True,
        description="Drop ticks received outside NSE regular session hours",
    )

    # ── Live Execution Engine (REAL broker orders) ────────────────────────────
    # Master switch — when False, the execution engine refuses to place real
    # orders even if all other checks pass. Keep False until production cutover.
    LIVE_EXEC_ENABLED: bool = Field(
        default=False,
        description="Master switch for placing REAL broker orders (default OFF)",
    )
    # Capital deployed per real-money trade (₹). Used to size quantity.
    LIVE_EXEC_CAPITAL_PER_TRADE: float = Field(
        default=50_000.0,
        description="Capital deployed per real-money trade (₹)",
    )
    # Total live-trading capital pool (₹). Used by the risk manager to enforce
    # max exposure and drawdown percentages.
    LIVE_EXEC_TOTAL_CAPITAL: float = Field(
        default=500_000.0,
        description="Total live-trading capital pool (₹) — basis for risk percentages",
    )
    # Hard cap on the number of live entries per trading day.
    LIVE_EXEC_MAX_TRADES_PER_DAY: int = Field(
        default=5,
        description="Maximum live-trade entries per trading day",
    )
    # Hard cap on simultaneously open live positions.
    LIVE_EXEC_MAX_OPEN_POSITIONS: int = Field(
        default=3,
        description="Maximum concurrent open live positions",
    )
    # Max position size as a % of LIVE_EXEC_TOTAL_CAPITAL.
    LIVE_EXEC_MAX_POSITION_PCT: float = Field(
        default=15.0,
        description="Max single-trade exposure as % of total capital",
    )
    # Max aggregate capital exposure as a % of LIVE_EXEC_TOTAL_CAPITAL.
    LIVE_EXEC_MAX_CAPITAL_EXPOSURE_PCT: float = Field(
        default=50.0,
        description="Max aggregate exposure across open positions as % of total capital",
    )
    # Daily loss limit as a % of total capital — auto-halt above this.
    LIVE_EXEC_MAX_DAILY_LOSS_PCT: float = Field(
        default=1.5,
        description="Daily loss limit as % of total capital before auto-halt",
    )
    # Max drawdown limit as a % of total capital — auto-halt above this.
    LIVE_EXEC_MAX_DRAWDOWN_PCT: float = Field(
        default=5.0,
        description="Max drawdown as % of total capital before auto-halt",
    )
    # IST time (HH:MM) at which all open live positions are force-closed.
    LIVE_EXEC_EOD_EXIT_TIME_IST: str = Field(
        default="15:15",
        description="IST time (HH:MM) at which all open live positions are force-closed",
    )
    # Default order type for entry orders (MARKET is safer for breakouts).
    LIVE_EXEC_DEFAULT_ORDER_TYPE: Literal["MARKET", "LIMIT"] = Field(
        default="MARKET",
        description="Default order type for live entry orders",
    )
    # Default product code used by the broker adapter (INTRADAY = MIS).
    LIVE_EXEC_DEFAULT_PRODUCT: Literal["INTRADAY", "DELIVERY"] = Field(
        default="INTRADAY",
        description="Default product code (INTRADAY=MIS for same-day square-off)",
    )
    # Max market-data staleness allowed before refusing to act on a signal.
    LIVE_EXEC_MAX_DATA_STALENESS_SECONDS: float = Field(
        default=120.0,
        description="Refuse signals whose triggering candle is older than this (seconds)",
    )
    # Max retries when placing a broker order on transient failures.
    LIVE_EXEC_ORDER_MAX_RETRIES: int = Field(
        default=2,
        description="Max retries when broker order placement fails transiently",
    )
    # Base back-off (seconds) between order placement retries (exponential).
    LIVE_EXEC_ORDER_RETRY_BACKOFF_SECONDS: float = Field(
        default=1.0,
        description="Base back-off (seconds) for exponential retries on order placement",
    )
    # Polling interval (seconds) for reconciling order status with the broker.
    LIVE_EXEC_ORDER_POLL_INTERVAL_SECONDS: float = Field(
        default=5.0,
        description="Interval (seconds) at which the engine polls broker order status",
    )
    # Default exchange code passed to the broker if a Stock doc lacks one.
    LIVE_EXEC_DEFAULT_EXCHANGE: str = Field(
        default="NSE",
        description="Default exchange code for live orders",
    )
    # Strict market-hours enforcement on live orders. Disable for paper-style
    # simulation in non-market hours during development.
    LIVE_EXEC_REQUIRE_MARKET_OPEN: bool = Field(
        default=True,
        description="Reject live orders received outside NSE market hours",
    )

    # ── Refresh tokens ────────────────────────────────────────────────────────
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=30,
        description="Refresh token validity in days",
    )
    # When False, all routes accept unauthenticated requests and behave as if
    # an admin user is logged in. Set True in production.
    AUTH_REQUIRED: bool = Field(
        default=False,
        description="Enforce JWT authentication on protected routes",
    )

    # ── Account lockout ───────────────────────────────────────────────────────
    LOGIN_MAX_ATTEMPTS: int = Field(
        default=5,
        description="Consecutive failed login attempts before the account is locked",
    )
    LOGIN_LOCKOUT_MINUTES: int = Field(
        default=30,
        description="Minutes an account remains locked after hitting LOGIN_MAX_ATTEMPTS",
    )

    # ── Seed admin user ───────────────────────────────────────────────────────
    INITIAL_ADMIN_USERNAME: str = Field(
        default="admin",
        description="Username for the auto-created admin user on first startup",
    )
    INITIAL_ADMIN_EMAIL: str = Field(
        default="admin@tradingbot.local",
        description="E-mail for the auto-created admin user",
    )
    INITIAL_ADMIN_PASSWORD: str = Field(
        default="change-me-on-first-login",
        description="Initial admin password — CHANGE immediately after first login",
    )

    # ── Rate limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = Field(
        default=True,
        description="Enable in-process rate limiting middleware",
    )
    RATE_LIMIT_PER_MINUTE: int = Field(
        default=120,
        description="Max requests per IP per minute for general endpoints",
    )
    RATE_LIMIT_AUTH_PER_MINUTE: int = Field(
        default=10,
        description="Max requests per IP per minute for auth endpoints (login, refresh)",
    )

    # ── Alerting — Email (SMTP) ───────────────────────────────────────────────
    ALERT_EMAIL_ENABLED: bool = Field(default=False, description="Enable e-mail alerts")
    ALERT_EMAIL_FROM: str = Field(default="", description="Sender address for alerts")
    ALERT_EMAIL_TO: str = Field(
        default="",
        description="Comma-separated recipient addresses for alerts",
    )
    ALERT_EMAIL_SMTP_HOST: str = Field(default="smtp.gmail.com", description="SMTP server host")
    ALERT_EMAIL_SMTP_PORT: int = Field(default=587, description="SMTP server port (TLS)")
    ALERT_EMAIL_SMTP_USER: str = Field(default="", description="SMTP login username")
    ALERT_EMAIL_SMTP_PASSWORD: str = Field(default="", description="SMTP login password")

    # ── Alerting — Telegram ───────────────────────────────────────────────────
    ALERT_TELEGRAM_ENABLED: bool = Field(default=False, description="Enable Telegram alerts")
    ALERT_TELEGRAM_BOT_TOKEN: str = Field(
        default="",
        description="Telegram Bot API token (from @BotFather)",
    )
    ALERT_TELEGRAM_CHAT_ID: str = Field(
        default="",
        description="Telegram chat_id to send alerts to (user or group)",
    )

    # ── Notification category toggles ─────────────────────────────────────────
    # Master switch — when False, no external notifications are dispatched.
    NOTIFY_ENABLED: bool = Field(default=True, description="Master switch for all external notifications")
    # Trade alerts: paper/live trade entry, exit, and stop-loss events.
    NOTIFY_TRADE_ALERTS: bool = Field(
        default=True, description="Send alerts on trade entry, exit, and stop-loss events"
    )
    # Signal alerts: emitted each time the signal engine generates a breakout signal.
    NOTIFY_SIGNAL_ALERTS: bool = Field(
        default=True, description="Send alerts when a new trading signal is generated"
    )
    # System / infrastructure alerts: broker disconnect, WS disconnect, scheduler
    # failure, unhandled exceptions.
    NOTIFY_SYSTEM_ALERTS: bool = Field(
        default=True, description="Send alerts for system-level failures and infrastructure events"
    )
    # Daily P&L summary sent at 15:45 IST on trading days.
    NOTIFY_DAILY_SUMMARY: bool = Field(
        default=True, description="Send daily summary at 15:45 IST on trading days"
    )
    # Dedup / burst-suppression window: same event within this window is dropped.
    NOTIFY_THROTTLE_WINDOW_SECONDS: int = Field(
        default=300,
        description="Seconds before the same dedup_key can fire again (burst suppression)",
    )
    # Max send retries per notification attempt (each provider independently).
    NOTIFY_MAX_RETRIES: int = Field(
        default=3, description="Max send attempts per notification before giving up"
    )
    # Base back-off seconds between retries (exponential: BASE, BASE*2, BASE*4 …).
    NOTIFY_RETRY_BACKOFF_SECONDS: float = Field(
        default=2.0, description="Base back-off (seconds) for notification retry (exponential)"
    )

    # ── Portfolio & Capital Allocation Engine ─────────────────────────────────
    # Total portfolio capital (₹). The basis for all percentage calculations.
    PORTFOLIO_TOTAL_CAPITAL: float = Field(
        default=1_000_000.0,
        description="Total portfolio capital for allocation sizing (₹)",
    )
    # Capital allocation algorithm: EQUAL_WEIGHT | SCORE_WEIGHTED | FIXED_RISK
    PORTFOLIO_ALLOCATION_METHOD: str = Field(
        default="SCORE_WEIGHTED",
        description="Capital allocation algorithm used by the portfolio service",
    )
    # Max aggregate capital deployed across all open positions (% of total).
    PORTFOLIO_MAX_CAPITAL_EXPOSURE_PCT: float = Field(
        default=80.0,
        description="Max aggregate capital exposure as % of PORTFOLIO_TOTAL_CAPITAL",
    )
    # Daily loss limit — portfolio halts new allocations when breached (% of total).
    PORTFOLIO_MAX_DAILY_LOSS_PCT: float = Field(
        default=2.0,
        description="Max daily portfolio loss as % of total capital before auto-halt",
    )
    # Hard cap on simultaneously open portfolio positions.
    PORTFOLIO_MAX_OPEN_POSITIONS: int = Field(
        default=10,
        description="Maximum concurrent approved portfolio positions",
    )
    # Max capital deployed per single trade (% of total).
    PORTFOLIO_MAX_CAPITAL_PER_TRADE_PCT: float = Field(
        default=20.0,
        description="Max capital per trade as % of PORTFOLIO_TOTAL_CAPITAL",
    )
    # Max capital in a single strategy (% of total).
    PORTFOLIO_MAX_CAPITAL_PER_STRATEGY_PCT: float = Field(
        default=50.0,
        description="Max capital per strategy as % of PORTFOLIO_TOTAL_CAPITAL",
    )
    # Max capital in a single GICS sector (% of total).
    PORTFOLIO_MAX_CAPITAL_PER_SECTOR_PCT: float = Field(
        default=40.0,
        description="Max capital per sector as % of PORTFOLIO_TOTAL_CAPITAL",
    )
    # Max number of open positions in the same sector (correlation guard).
    PORTFOLIO_MAX_CORRELATED_POSITIONS: int = Field(
        default=3,
        description="Max open positions in the same GICS sector",
    )
    # FIXED_RISK method: % of total capital to risk per trade.
    PORTFOLIO_FIXED_RISK_PCT: float = Field(
        default=1.0,
        description="% of total capital risked per trade in FIXED_RISK mode",
    )
    # Minimum viable allocation — smaller sized trades are rejected.
    PORTFOLIO_MIN_CAPITAL_PER_TRADE: float = Field(
        default=5_000.0,
        description="Minimum capital required to open a trade (₹)",
    )

    # ── Security headers ──────────────────────────────────────────────────────
    SECURE_HEADERS_ENABLED: bool = Field(
        default=True,
        description="Inject HTTP security headers (HSTS, X-Frame-Options, etc.)",
    )

    @field_validator("APP_ENV", mode="before")
    @classmethod
    def normalise_env(cls, v: str) -> str:
        return v.lower()

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    Using lru_cache ensures the .env file is parsed only once per process,
    and the same object is reused across all dependency injections.
    """
    return Settings()


# Module-level singleton for convenient imports: `from app.config.settings import settings`
settings: Settings = get_settings()
