"""
Application-wide exception hierarchy.

All custom exceptions inherit from TradingBotException so callers can
catch the base class when they don't care about the specific error type,
or catch the specific subclass for fine-grained handling.

FastAPI exception handlers are registered in app/main.py.
"""

from typing import Any


class TradingBotException(Exception):
    """Base exception for all application errors."""

    def __init__(
        self,
        message: str = "An unexpected error occurred.",
        status_code: int = 500,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, status_code={self.status_code})"


# ── Database ──────────────────────────────────────────────────────────────────

class DatabaseException(TradingBotException):
    """Raised when a database operation fails."""

    def __init__(self, message: str = "Database error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)


class DocumentNotFoundException(TradingBotException):
    """Raised when a requested MongoDB document does not exist."""

    def __init__(self, resource: str = "Document", identifier: Any = None) -> None:
        msg = f"{resource} not found"
        if identifier is not None:
            msg += f": {identifier}"
        super().__init__(message=msg, status_code=404)


# ── Authentication / Authorisation ────────────────────────────────────────────

class AuthenticationException(TradingBotException):
    """Raised when authentication credentials are invalid or missing."""

    def __init__(self, message: str = "Authentication failed.") -> None:
        super().__init__(message=message, status_code=401)


class AuthorizationException(TradingBotException):
    """Raised when the caller lacks permission for the requested action."""

    def __init__(self, message: str = "Permission denied.") -> None:
        super().__init__(message=message, status_code=403)


# ── Broker ────────────────────────────────────────────────────────────────────

class BrokerException(TradingBotException):
    """Raised on broker API errors (login failure, order rejection, etc.)."""

    def __init__(self, broker: str, message: str, detail: Any = None) -> None:
        super().__init__(
            message=f"[{broker}] {message}",
            status_code=502,
            detail=detail,
        )


class OrderException(BrokerException):
    """Raised when an order cannot be placed or modified."""


# ── Strategy ──────────────────────────────────────────────────────────────────

class StrategyException(TradingBotException):
    """Raised when a strategy encounters an unrecoverable error."""

    def __init__(self, strategy: str, message: str) -> None:
        super().__init__(message=f"[{strategy}] {message}", status_code=500)


class InvalidStrategyConfigException(StrategyException):
    """Raised when a strategy's configuration is invalid."""


# ── Validation ────────────────────────────────────────────────────────────────

class ValidationException(TradingBotException):
    """Raised when incoming data fails business-rule validation."""

    def __init__(self, message: str = "Validation error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=422, detail=detail)


# ── Conflict (duplicate operation, in-progress job, etc.) ─────────────────────

class ConflictException(TradingBotException):
    """Raised when a request conflicts with current state (e.g. duplicate run)."""

    def __init__(self, message: str = "Conflict with current state.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=409, detail=detail)


# ── Scheduler ────────────────────────────────────────────────────────────────

class SchedulerException(TradingBotException):
    """Raised when the scheduler fails to start, stop, or add a job."""

    def __init__(self, message: str = "Scheduler error.") -> None:
        super().__init__(message=message, status_code=500)


# ── Market data / ingestion ───────────────────────────────────────────────────

class MarketDataException(TradingBotException):
    """Base class for market data related errors."""

    def __init__(self, message: str = "Market data error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)


class IngestionException(MarketDataException):
    """Raised when the historical data ingestion pipeline fails for a symbol."""

    def __init__(self, symbol: str, message: str, detail: Any = None) -> None:
        super().__init__(message=f"[{symbol}] {message}", detail=detail)


class AngelOneAPIException(BrokerException):
    """Raised when the Angel One SmartAPI returns an error response."""

    def __init__(self, message: str, error_code: str = "", detail: Any = None) -> None:
        super().__init__(
            broker="AngelOne",
            message=f"{message} (errorcode={error_code})" if error_code else message,
            detail=detail,
        )


class AngelOneAuthException(TradingBotException):
    """Raised when Angel One authentication or token refresh fails."""

    def __init__(self, message: str = "Angel One authentication failed.") -> None:
        super().__init__(message=f"[AngelOne] {message}", status_code=401)


class RateLimitException(TradingBotException):
    """Raised when an external API rate limit is hit."""

    def __init__(self, source: str = "API", retry_after: int = 60) -> None:
        super().__init__(
            message=f"{source} rate limit exceeded. Retry after {retry_after}s.",
            status_code=429,
            detail={"retry_after": retry_after},
        )


# ── Backtesting ───────────────────────────────────────────────────────────────

class BacktestException(TradingBotException):
    """Base class for backtesting errors."""

    def __init__(self, message: str = "Backtest error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)


class BacktestNotFoundException(BacktestException):
    """Raised when a requested backtest run does not exist."""

    def __init__(self, run_id: str) -> None:
        super().__init__(
            message=f"Backtest run not found: {run_id}",
            detail={"run_id": run_id},
        )
        self.status_code = 404


class BacktestConfigException(BacktestException):
    """Raised when the backtest configuration is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message=f"Invalid backtest configuration: {message}")
        self.status_code = 422


# ── Research / Optimization ───────────────────────────────────────────────────

class ResearchException(TradingBotException):
    """Base class for research and optimization errors."""

    def __init__(self, message: str = "Research error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)


class ResearchNotFoundException(ResearchException):
    """Raised when a requested research run does not exist."""

    def __init__(self, run_id: str) -> None:
        super().__init__(
            message=f"Research run not found: {run_id}",
            detail={"run_id": run_id},
        )
        self.status_code = 404


class ResearchConfigException(ResearchException):
    """Raised when the research configuration is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message=f"Invalid research configuration: {message}")
        self.status_code = 422


# ── Paper Trading ─────────────────────────────────────────────────────────────

class PaperTradingException(TradingBotException):
    """Base class for paper-trading errors. Distinct from BrokerException
    because paper trading never touches a broker."""

    def __init__(self, message: str = "Paper trading error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)


class RiskRejectedException(PaperTradingException):
    """Raised when the risk manager refuses to open a new paper position."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(message=f"Risk check failed: {reason}", detail=detail)
        self.status_code = 409


class DuplicatePaperPositionException(PaperTradingException):
    """Raised when a paper position already exists for (symbol, trading_date)."""

    def __init__(self, symbol: str, trading_date: Any) -> None:
        super().__init__(
            message=f"Paper position already exists for {symbol} on {trading_date}.",
            detail={"symbol": symbol, "trading_date": str(trading_date)},
        )
        self.status_code = 409


class PaperAccountNotFoundException(PaperTradingException):
    """Raised when the paper-trading account row has not been initialised."""

    def __init__(self, account_id: str = "default") -> None:
        super().__init__(
            message=f"Paper trading account not found: {account_id}",
            detail={"account_id": account_id},
        )
        self.status_code = 404


# ── Live Execution (REAL broker orders) ───────────────────────────────────────

class LiveExecutionException(TradingBotException):
    """
    Base class for live (real-money) execution errors.

    Distinct from BrokerException — BrokerException wraps a low-level
    broker API failure, whereas LiveExecutionException covers the entire
    live execution pipeline (risk gate, state machine, position manager,
    failsafe). A BrokerException raised by the Angel One client may be
    caught and re-raised as a LiveExecutionException at the service edge
    so callers see a consistent vocabulary for live-trading failures.
    """

    def __init__(self, message: str = "Live execution error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)


class LiveRiskRejectedException(LiveExecutionException):
    """Raised when the live risk manager refuses to place a real order."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(message=f"Live risk check failed: {reason}", detail=detail)
        self.status_code = 409


class DuplicateLiveOrderException(LiveExecutionException):
    """Raised when a live order already exists for (signal_id) or (symbol, trading_date)."""

    def __init__(self, identifier: str, detail: Any = None) -> None:
        super().__init__(
            message=f"Duplicate live order rejected: {identifier}",
            detail=detail or {"identifier": identifier},
        )
        self.status_code = 409


class InvalidOrderStateTransitionException(LiveExecutionException):
    """Raised when the order state machine is asked to perform an illegal transition."""

    def __init__(self, from_state: str, to_state: str, order_id: str = "") -> None:
        super().__init__(
            message=f"Invalid order state transition {from_state} → {to_state}",
            detail={
                "order_id": order_id,
                "from_state": from_state,
                "to_state": to_state,
            },
        )
        self.status_code = 422


class TradingHaltedException(LiveExecutionException):
    """Raised when the kill switch / global trading halt is active."""

    def __init__(self, reason: str = "kill_switch_engaged") -> None:
        super().__init__(
            message=f"Live trading is halted: {reason}",
            detail={"reason": reason},
        )
        self.status_code = 423  # Locked


class MarketClosedException(LiveExecutionException):
    """Raised when an order is attempted outside the regular NSE session."""

    def __init__(self, message: str = "Market is closed.") -> None:
        super().__init__(message=message)
        self.status_code = 423


class StaleMarketDataException(LiveExecutionException):
    """Raised when the most recent tick is older than the freshness threshold."""

    def __init__(self, symbol: str, age_seconds: float, threshold_seconds: float) -> None:
        super().__init__(
            message=f"Stale market data for {symbol}: {age_seconds:.1f}s > {threshold_seconds:.1f}s",
            detail={
                "symbol": symbol,
                "age_seconds": age_seconds,
                "threshold_seconds": threshold_seconds,
            },
        )
        self.status_code = 503


class BrokerSessionExpiredException(LiveExecutionException):
    """Raised when the cached broker session is no longer valid for placing orders."""

    def __init__(self, broker: str) -> None:
        super().__init__(
            message=f"[{broker}] broker session expired; re-authentication required.",
            detail={"broker": broker},
        )
        self.status_code = 401


class LiveOrderNotFoundException(LiveExecutionException):
    """Raised when a requested LiveOrder document does not exist."""

    def __init__(self, order_id: str) -> None:
        super().__init__(
            message=f"Live order not found: {order_id}",
            detail={"order_id": order_id},
        )
        self.status_code = 404


class LivePositionNotFoundException(LiveExecutionException):
    """Raised when a requested LivePosition document does not exist."""

    def __init__(self, position_id: str) -> None:
        super().__init__(
            message=f"Live position not found: {position_id}",
            detail={"position_id": position_id},
        )
        self.status_code = 404


# ── Portfolio ─────────────────────────────────────────────────────────────────

class PortfolioException(TradingBotException):
    """Base class for portfolio & capital allocation errors."""

    def __init__(self, message: str = "Portfolio error.", detail=None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)


class PortfolioRiskRejectedException(PortfolioException):
    """Raised when the portfolio risk manager rejects a signal."""

    def __init__(self, reason: str, detail=None) -> None:
        super().__init__(message=f"Portfolio risk check failed: {reason}", detail=detail)
        self.status_code = 409


class PortfolioHaltedException(PortfolioException):
    """Raised when the portfolio is halted (daily loss limit hit)."""

    def __init__(self, reason: str = "daily_loss_limit_breached") -> None:
        super().__init__(message=f"Portfolio halted: {reason}")
        self.status_code = 423


class PortfolioAllocationNotFoundException(PortfolioException):
    """Raised when a requested PortfolioAllocation document does not exist."""

    def __init__(self, allocation_id: str) -> None:
        super().__init__(
            message=f"Portfolio allocation not found: {allocation_id}",
            detail={"allocation_id": allocation_id},
        )
        self.status_code = 404


# ── Walk-Forward Validation ───────────────────────────────────────────────────

class WalkForwardException(TradingBotException):
    def __init__(self, message: str = "Walk-forward validation error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)

class WalkForwardConfigException(TradingBotException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=422)

class WalkForwardNotFoundException(TradingBotException):
    def __init__(self, run_id: str) -> None:
        super().__init__(message=f"Walk-forward run not found: {run_id}", status_code=404)


# ── Monte Carlo Risk Analysis ─────────────────────────────────────────────────

class MonteCarloException(TradingBotException):
    def __init__(self, message: str = "Monte Carlo simulation error.", detail: Any = None) -> None:
        super().__init__(message=message, status_code=500, detail=detail)

class MonteCarloConfigException(TradingBotException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, status_code=422)

class MonteCarloNotFoundException(TradingBotException):
    def __init__(self, run_id: str) -> None:
        super().__init__(message=f"Monte Carlo run not found: {run_id}", status_code=404)
