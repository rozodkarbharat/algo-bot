"""
API v1 router aggregator.

All sub-routers are registered here. This module is mounted in app/main.py
under prefix="/api/v1".

Add new feature routers by importing and calling router.include_router() below.
"""

from fastapi import APIRouter, Depends

from app.routes.v1.auth import router as auth_router
from app.routes.v1.stocks import router as stocks_router
from app.routes.v1.sync import router as sync_router
from app.routes.v1.analysis import router as analysis_router
from app.routes.v1.shortlist import router as shortlist_router
from app.routes.v1.backtest import router as backtest_router
from app.routes.v1.research import router as research_router
from app.routes.v1.live import router as live_router
from app.routes.v1.live_execution import router as live_execution_router
from app.routes.v1.paper import router as paper_router
from app.routes.v1.system import router as system_router
from app.routes.v1.notifications import router as notifications_router
from app.routes.v1.orhv import router as orhv_router
from app.routes.v1.portfolio import router as portfolio_router
from app.routes.v1.performance_attribution import router as performance_attribution_router
from app.routes.v1.ops import router as ops_router
from app.routes.v1.reconciliation import router as reconciliation_router
from app.routes.v1.walk_forward import router as walk_forward_router
from app.routes.v1.monte_carlo import router as monte_carlo_router
from app.routes.v1.incidents import router as incidents_router
from app.routes.v1.health import router as health_router
from app.routes.v1.validation import router as validation_router
from app.routes.v1.strategy_lab import router as strategy_lab_router

router = APIRouter()

# ── Auth routes (PUBLIC — no auth dependency) ─────────────────────────────────
# /me, /change-password, /users use their own Depends() guards internally.
router.include_router(auth_router, prefix="/auth", tags=["Authentication"])

# ── Protected application routes ──────────────────────────────────────────────
# Each router may additionally use require_trader / require_admin on specific
# write/control endpoints internally. The router-level Depends here ensures
# every request carries a valid token when AUTH_REQUIRED=True.
from app.middleware.auth_middleware import get_current_user

_auth_dep = [Depends(get_current_user)]

router.include_router(stocks_router, prefix="/stocks", tags=["Stocks"], dependencies=_auth_dep)
router.include_router(sync_router, prefix="/sync", tags=["Data Sync"], dependencies=_auth_dep)
router.include_router(analysis_router, tags=["Strategy Analysis"], dependencies=_auth_dep)
router.include_router(shortlist_router, prefix="/shortlist", tags=["Shortlist"], dependencies=_auth_dep)
router.include_router(backtest_router, prefix="/backtest", tags=["Backtesting"], dependencies=_auth_dep)
router.include_router(research_router, prefix="/research", tags=["Research & Optimization"], dependencies=_auth_dep)
router.include_router(live_router, prefix="/live", tags=["Live Signals"], dependencies=_auth_dep)
# Live execution routes share the /live prefix but are tagged separately
# in OpenAPI so the dashboard can distinguish signal vs. broker endpoints.
router.include_router(live_execution_router, prefix="/live", tags=["Live Execution"], dependencies=_auth_dep)
router.include_router(paper_router, prefix="/paper", tags=["Paper Trading"], dependencies=_auth_dep)
router.include_router(system_router, prefix="/system", tags=["System"], dependencies=_auth_dep)
router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"], dependencies=_auth_dep)
router.include_router(orhv_router, prefix="/orhv", tags=["ORHV Strategy"], dependencies=_auth_dep)
router.include_router(portfolio_router, prefix="/portfolio", tags=["Portfolio"], dependencies=_auth_dep)
router.include_router(performance_attribution_router, prefix="/analytics", tags=["Performance Attribution"], dependencies=_auth_dep)
router.include_router(ops_router, prefix="/ops", tags=["Ops Dashboard"], dependencies=_auth_dep)
router.include_router(reconciliation_router, prefix="/reconciliation", tags=["Broker Reconciliation"], dependencies=_auth_dep)
router.include_router(walk_forward_router, prefix="/walk-forward", tags=["Walk-Forward Validation"], dependencies=_auth_dep)
router.include_router(monte_carlo_router, prefix="/risk/monte-carlo", tags=["Monte Carlo Risk Analysis"], dependencies=_auth_dep)
router.include_router(incidents_router, prefix="/incidents", tags=["Incident Management"], dependencies=_auth_dep)
router.include_router(health_router, prefix="/health", tags=["Monitoring & Health"], dependencies=_auth_dep)
router.include_router(validation_router, prefix="/validation", tags=["Live Validation"], dependencies=_auth_dep)
router.include_router(strategy_lab_router, prefix="/strategies", tags=["Strategy Research Lab"], dependencies=_auth_dep)
