"""
Beanie document model registry.

Every Beanie Document subclass must be listed in DOCUMENT_MODELS.
Beanie's init_beanie() call uses this list to:
  - Map Python classes to MongoDB collections
  - Create declared indexes on first startup
  - Validate the schema against the collection

Adding a new model:
  1. Create the class in app/models/
  2. Import it here and add it to DOCUMENT_MODELS
  3. Beanie will handle collection creation and index setup automatically
"""

from typing import Type

from beanie import Document

# ── Model imports ─────────────────────────────────────────────────────────────
from app.models.stock import Stock
from app.models.historical_candle import HistoricalCandle
from app.models.market_data_sync_log import MarketDataSyncLog
from app.models.one_side_day import OneSideDay
from app.models.continuation_statistic import ContinuationStatistic
from app.models.backtest_run import BacktestRun
from app.models.backtest_trade import BacktestTrade
from app.models.backtest_metrics import BacktestMetrics
from app.models.research_run import ResearchRun
from app.models.parameter_optimization_result import ParameterOptimizationResult
from app.models.stock_performance_analytics import StockPerformanceAnalytics
from app.models.live_signal import LiveSignal
from app.models.intraday_market_state import IntradayMarketState
from app.models.paper_position import PaperPosition
from app.models.paper_trade import PaperTrade
from app.models.paper_account import PaperAccount
from app.models.live_order import LiveOrder
from app.models.live_position import LivePosition
from app.models.broker_session import BrokerSession
from app.models.user import User
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.alert_event import AlertEvent
from app.models.notification_event import NotificationEvent
# ORHV strategy-specific models
from app.strategy.strategies.opening_range_historical_validation.models import (
    ORHVSetup,
    ORHVValidationRecord,
    ORHVSignalRecord,
    ORHVStatistics,
)
# Portfolio & Capital Allocation Engine models
from app.models.portfolio_allocation import PortfolioAllocation
from app.models.portfolio_risk_state import PortfolioRiskState
# Monitoring & Reliability Platform models
from app.models.system_health_status import SystemHealthStatus
from app.models.system_incident import SystemIncident
# Broker Reconciliation Engine models
from app.models.broker_reconciliation import BrokerReconciliationRun, BrokerDiscrepancy
# Walk-Forward Validation Engine models
from app.models.walk_forward_run import WalkForwardRun
from app.models.walk_forward_segment import WalkForwardSegment
# Monte Carlo Risk Analysis Engine models
from app.models.monte_carlo_run import MonteCarloRun
from app.models.monte_carlo_result import MonteCarloResult
# Live Validation & Reality Gap Analysis models
from app.models.validation_run import ValidationRun
from app.models.signal_validation import SignalValidation
# Strategy Research Lab models
from app.models.strategy_catalog import StrategyCatalog, StrategyVersion, StrategyDeployment
from app.models.strategy_experiment import StrategyExperiment, ABTest
from app.models.strategy_scorecard import StrategyScorecard

# ── Registry ──────────────────────────────────────────────────────────────────
DOCUMENT_MODELS: list[Type[Document]] = [
    Stock,
    HistoricalCandle,
    MarketDataSyncLog,
    OneSideDay,
    ContinuationStatistic,
    BacktestRun,
    BacktestTrade,
    BacktestMetrics,
    # Research & optimization models
    ResearchRun,
    ParameterOptimizationResult,
    StockPerformanceAnalytics,
    # Live trading engine models
    LiveSignal,
    IntradayMarketState,
    # Paper trading engine models
    PaperPosition,
    PaperTrade,
    PaperAccount,
    # Live execution (REAL broker orders) models
    LiveOrder,
    LivePosition,
    BrokerSession,
    # Auth + audit + alerting models
    User,
    AuditLog,
    RefreshToken,
    AlertEvent,
    NotificationEvent,
    # ORHV strategy-specific models
    ORHVSetup,
    ORHVValidationRecord,
    ORHVSignalRecord,
    ORHVStatistics,
    # Portfolio & Capital Allocation Engine models
    PortfolioAllocation,
    PortfolioRiskState,
    # Monitoring & Reliability Platform models
    SystemHealthStatus,
    SystemIncident,
    # Broker Reconciliation Engine models
    BrokerReconciliationRun,
    BrokerDiscrepancy,
    # Walk-Forward Validation Engine models
    WalkForwardRun,
    WalkForwardSegment,
    # Monte Carlo Risk Analysis Engine models
    MonteCarloRun,
    MonteCarloResult,
    # Live Validation & Reality Gap Analysis models
    ValidationRun,
    SignalValidation,
    # Strategy Research Lab models
    StrategyCatalog,
    StrategyVersion,
    StrategyDeployment,
    StrategyExperiment,
    ABTest,
    StrategyScorecard,
]


def get_document_models() -> list[Type[Document]]:
    """
    Return the list of Beanie document models to register with init_beanie().

    Kept as a function so tests can patch the list without mutating
    the module-level constant.
    """
    return DOCUMENT_MODELS
