# PROJECT_CONTEXT.md
> **Authoritative architecture document — generated 2026-05-29 from live codebase inspection.**
> Future Claude sessions and developers should use this as the single source of truth for continuing development.

---

## SECTION 1 — EXECUTIVE SUMMARY

### Project Purpose
A **production-grade intraday algorithmic trading system** targeting NSE/BSE equity markets (NIFTY50 universe). The system detects statistical patterns in opening-range breakouts, validates them against historical continuation probability, generates real-time entry signals, executes trades via the AngelOne SmartAPI broker, and tracks performance through a full analytics stack.

### Current Maturity Level
**Pre-production (validated, not yet live-trading).** All core subsystems are built and tested. The system is feature-complete up to and including paper trading and the live execution engine. The live execution master switch (`LIVE_EXEC_ENABLED`) is `False` by default and must be explicitly enabled for real-money trading. 540+ unit and integration tests pass.

### Major Capabilities
| Capability | Status |
|---|---|
| Historical data ingestion (AngelOne API) | Complete |
| Strategy framework (multi-strategy, pluggable) | Complete |
| One-Side ORB strategy | Complete |
| ORHV (Opening Range Historical Validation) strategy | Complete |
| Backtesting engine (historical replay) | Complete |
| Research & parameter optimization | Complete |
| Live signal engine (real-time ORB breakout detection) | Complete |
| Paper trading engine (full fill simulation) | Complete |
| Portfolio engine (signal ranking + capital allocation) | Complete |
| Live execution engine (AngelOne broker integration) | Complete — disabled by flag |
| Performance attribution (strategy / stock / portfolio) | Complete |
| Monitoring platform (health checks, incidents, alerting) | Complete |
| Notification system (Telegram + Email) | Complete |
| WebSocket real-time layer (9 rooms) | Complete |
| React dashboard frontend | Complete |
| Scheduler (23 cron jobs) | Complete |
| REST API (16 route groups, v1) | Complete |

### Current Development Stage
The system has completed its planned build phases (Foundation → Data → Backtesting → Research → Live Signals → Paper Trading → Portfolio Engine → Performance Attribution → Monitoring Platform). The next logical milestone is **live-trading cutover**: enabling `LIVE_EXEC_ENABLED=True`, connecting the AngelOne WebSocket for real-time tick feeds, and running production smoke tests.

---

## SECTION 2 — TECH STACK

### Backend
| Component | Technology | Version |
|---|---|---|
| Runtime | Python | 3.12 |
| Web framework | FastAPI | 0.115.5 |
| ASGI server | Uvicorn (standard) | 0.32.1 |
| Database ODM | Beanie (async Pydantic ODM over Motor) | 2.1.0 |
| MongoDB async driver | Motor | 3.7.1 |
| MongoDB driver | PyMongo | 4.17.0 |
| Configuration | Pydantic-Settings | 2.6.1 |
| Scheduler | APScheduler (AsyncIOScheduler) | 3.10.4 |
| Auth / JWT | python-jose[cryptography] | 3.3.0 |
| Password hashing | passlib[bcrypt] | 1.7.4 |
| HTTP client (broker) | httpx + aiohttp | 0.28.1 / 3.11.10 |
| TOTP (broker 2FA) | pyotp | 2.9.0 |
| Testing | pytest + pytest-asyncio + pytest-cov | 8.3.4 |
| Linting | ruff | 0.8.4 |
| Type checking | mypy | 1.13.0 |

### Frontend
| Component | Technology | Version |
|---|---|---|
| Framework | React | 18.3.1 |
| Language | TypeScript | 5.5.3 |
| Build tool | Vite | 5.4.8 |
| CSS | TailwindCSS | 3.4.13 |
| State management | Zustand | 5.0.0 |
| API client | TanStack React Query + Axios | 5.56.2 / 1.7.7 |
| Charts | Recharts | 2.13.0 |
| Routing | React Router v6 | 6.26.2 |
| Icons | Lucide-React | 0.446.0 |

### Database
- **MongoDB** (document store) — all persistent state
- **Motor** (async driver) + **Beanie** (ODM with Pydantic v2 model validation)
- Single database: `trading_bot`
- 30 Beanie document collections registered in `app/database/init_db.py`
- Bucket pattern for candle storage (one document = one symbol × one day × one interval)

### Infrastructure
- Docker / docker-compose (`docker-compose.yml` + `Dockerfile` + `docker/`)
- APScheduler in-process (MemoryJobStore, AsyncIOExecutor)
- Environment-based config via `.env` file (Pydantic BaseSettings)
- Structured JSON logging (`app/utils/logger.py`)

### Broker Integrations
- **AngelOne SmartAPI** (REST + planned WebSocket tick feed)
  - TOTP-based authentication (pyotp)
  - JWT session management with 24h expiry, 30-min proactive refresh
  - Supports: place_order, cancel_order, get_order_status, get_positions, historical data
  - Abstract `BaseBroker` interface at `app/brokers/base.py` — swappable to Zerodha, Upstox, etc.

---

## SECTION 3 — CURRENT FOLDER STRUCTURE

```
trading-bot/
├── app/                          # Backend application
│   ├── main.py                   # FastAPI app factory + lifespan
│   ├── config/
│   │   └── settings.py           # Pydantic BaseSettings — all env vars
│   ├── core/
│   │   ├── exceptions.py         # Domain-specific exception hierarchy
│   │   └── exception_handlers.py # FastAPI error handlers
│   ├── database/
│   │   ├── mongodb.py            # Motor connect/disconnect + Beanie init
│   │   └── init_db.py            # DOCUMENT_MODELS registry (30 models)
│   ├── models/                   # Beanie ODM documents (MongoDB collections)
│   ├── schemas/                  # Pydantic request/response schemas
│   ├── repositories/             # Data access layer (one per collection)
│   ├── services/                 # Business logic orchestrators
│   ├── strategy/                 # Strategy framework + implementations
│   │   ├── base_strategy.py      # Abstract BaseStrategy contract
│   │   ├── strategy_registry.py  # Global strategy registry singleton
│   │   ├── backtest_engine.py    # One-Side ORB historical replay engine
│   │   ├── trade_simulator.py    # Per-trade fill simulation
│   │   ├── one_side_detector.py  # One-side day classifier
│   │   ├── continuation_probability.py  # P(OSD_today | OSD_yesterday)
│   │   ├── metrics_engine.py     # Backtest metrics computation
│   │   ├── strategies/
│   │   │   ├── one_side_orb/     # One-Side ORB strategy (plug-in)
│   │   │   └── opening_range_historical_validation/  # ORHV strategy
│   │   └── templates/            # New strategy scaffold template
│   ├── analytics/                # Performance attribution engines
│   │   ├── math_helpers.py       # Pure math: Sharpe, drawdown, win rate...
│   │   ├── strategy_attribution.py
│   │   ├── stock_attribution.py
│   │   ├── portfolio_analytics.py
│   │   ├── capital_efficiency.py
│   │   └── strategy_comparison.py
│   ├── research/                 # Research & optimization engines
│   │   ├── parameter_optimizer.py
│   │   ├── stock_analytics.py
│   │   ├── time_analytics.py
│   │   ├── failure_analytics.py
│   │   ├── market_condition_analytics.py
│   │   └── report_generator.py
│   ├── live/                     # Live signal pipeline
│   │   ├── candle_builder.py     # Tick → OHLCV 15-min candle aggregator
│   │   ├── market_engine.py      # Tick routing hub (CandleBuilder → SignalEngine)
│   │   ├── market_session.py     # NSE session constants + market hours
│   │   ├── signal_engine.py      # ORB breakout detector → GeneratedSignal
│   │   └── health_monitor.py     # Live engine health monitoring
│   ├── paper_trading/            # Full paper trading simulation
│   │   ├── paper_execution_engine.py  # Slippage + brokerage fill simulation
│   │   ├── position_manager.py        # In-memory open position tracker
│   │   ├── risk_manager.py            # Paper-level risk rules
│   │   ├── pnl_engine.py              # MTM + realised P&L computation
│   │   └── session_manager.py         # Session lifecycle (warm-up, reset)
│   ├── live_execution/           # Real broker order execution
│   │   ├── execution_engine.py   # Signal → risk → broker pipeline
│   │   ├── order_state_machine.py # PENDING→OPEN→FILLED state transitions
│   │   ├── failsafe.py           # Kill switch, market-hours, staleness guards
│   │   ├── live_position_manager.py # Live position book
│   │   └── live_risk_manager.py  # Pre-order risk gate
│   ├── portfolio/                # Portfolio-level capital management
│   │   ├── signal_ranker.py      # 5-factor composite score [0,1]
│   │   ├── capital_allocator.py  # EQUAL_WEIGHT / SCORE_WEIGHTED / FIXED_RISK
│   │   └── portfolio_risk_manager.py # 8-rule portfolio risk gatekeeper
│   ├── monitoring/               # Platform health & ops
│   │   ├── health_checks/        # 9 component health checks
│   │   │   ├── base.py, mongodb_check.py, broker_check.py
│   │   │   ├── websocket_check.py, scheduler_check.py
│   │   │   ├── signal_engine_check.py, portfolio_check.py
│   │   │   ├── execution_check.py
│   │   │   ├── paper_trading_check.py  # Paper engine: session, positions, daily P&L
│   │   │   └── reconciliation_check.py # Reconciliation: staleness, open mismatches
│   │   ├── health_aggregator.py  # Orchestrates all 9 checks, manages incidents
│   │   ├── heartbeat.py          # Per-component heartbeat tracker (9 components)
│   │   ├── incident_manager.py   # Incident lifecycle (open/resolve/escalate)
│   │   ├── alert_router.py       # Routes alerts to notification channels
│   │   ├── daily_report.py       # EOD ops report generator
│   │   ├── risk_monitor.py       # Portfolio risk alerting
│   │   ├── market_data_monitor.py # Market data freshness monitoring
│   │   └── execution_monitor.py  # Live execution monitoring
│   ├── notifications/            # External alert dispatch
│   │   ├── telegram_notifier.py
│   │   ├── email_notifier.py
│   │   ├── notification_manager.py
│   │   ├── daily_summary.py
│   │   └── templates/            # Message templates (email + telegram)
│   ├── brokers/                  # Broker adapter layer
│   │   ├── base.py               # BaseBroker ABC + data types
│   │   └── angelone/
│   │       ├── auth.py           # TOTP login, JWT session management
│   │       ├── client.py         # AngelOneBroker (BaseBroker implementation)
│   │       ├── execution.py      # HTTP order placement client
│   │       └── historical_data.py # Historical OHLCV fetch client
│   ├── websocket/
│   │   └── manager.py            # ConnectionManager (rooms, broadcast)
│   ├── routes/
│   │   ├── health.py             # /health, /health/ready
│   │   ├── websocket_routes.py   # 9 WebSocket endpoints
│   │   └── v1/                   # 16 REST route groups under /api/v1
│   ├── scheduler/
│   │   ├── scheduler.py          # APScheduler singleton + lifecycle
│   │   └── jobs/                 # 7 job modules (~23 registered jobs)
│   │       ├── market_data_jobs.py
│   │       ├── strategy_jobs.py
│   │       ├── live_engine_jobs.py
│   │       ├── paper_trading_jobs.py
│   │       ├── live_execution_jobs.py
│   │       ├── notification_jobs.py
│   │       └── monitoring_jobs.py
│   ├── services/                 # Service layer (orchestrators)
│   │   ├── auth_service.py, audit_service.py, alert_service.py
│   │   ├── historical_data_service.py, stock_universe_service.py
│   │   ├── strategy_service.py, shortlist_service.py
│   │   ├── backtest_service.py, backtest_analytics_service.py
│   │   ├── research_service.py
│   │   ├── live_signal_service.py, live_execution_service.py
│   │   ├── paper_trading_service.py
│   │   ├── portfolio_service.py
│   │   ├── orhv_service.py
│   │   └── notification_service.py
│   ├── middleware/               # CORS, auth, rate-limit, security headers, logging
│   └── utils/                   # Market time, trading day, logger, analytics, candle intervals
├── frontend/                     # React 18 + TypeScript dashboard
│   └── src/
│       ├── pages/                # 8 pages (Dashboard, LiveSignals, LiveTrading, PaperTrading, Analytics, Shortlist, Settings, SystemMonitor, Login)
│       ├── api/                  # Typed API clients (Axios + TanStack Query)
│       ├── store/                # Zustand state stores (5)
│       ├── websocket/            # WebSocketManager (auto-reconnect, room-based)
│       ├── components/           # Shared UI components + ProtectedRoute
│       ├── layouts/              # AppLayout, Header, Sidebar
│       ├── hooks/                # useWebSocket.ts
│       ├── types/                # TypeScript type definitions
│       └── utils/                # cn.ts (clsx), formatters.ts
├── tests/                        # 43 test modules, 540+ passing tests
├── docker/                       # Docker support files
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example                  # Environment variable template (all keys documented)
└── README.md
```

---

## SECTION 4 — ARCHITECTURE OVERVIEW

### Request Flow (REST API)
```
HTTP Request
  → RequestLoggingMiddleware      (outermost — attaches request_id, logs every request)
  → RateLimitMiddleware           (IP-based: 120/min general, 10/min auth endpoints)
  → SecurityHeadersMiddleware     (HSTS, X-Frame-Options, CSP, X-Content-Type-Options)
  → CORSMiddleware                (allows React dashboard origins: localhost:3000, :5173)
  → FastAPI Router                (dispatches to /health, /api/v1/*, /ws/*)
  → auth_middleware.get_current_user()  (JWT validation when AUTH_REQUIRED=True)
  → Route handler
  → Service layer
  → Repository layer
  → MongoDB (via Motor + Beanie)
  ← JSON response
```

### Live Signal Flow (Daily cycle)
```
08:30 IST  → pre_market_sync_check: backfill missing yesterday data if needed
             live_broker_session_refresh: warm AngelOne JWT before market open

09:10 IST  → live_market_open_init: load shortlist, log readiness

09:14 IST  → paper_session_warmup: hydrate paper account + open positions
             live_session_warmup: hydrate live position book from DB

09:15 IST  → live_signal_engine_start:
               LiveSignalService.start()
               → ShortlistService.generate_shortlist(today)
               → LiveMarketEngine.activate(trading_date, shortlist)
               → SignalEngine.activate(trading_date, shortlist_candidates)
               → [Tick feed subscription — pending AngelOne WS integration]

During market hours (09:15–11:30 IST):
  AngelOne tick → LiveMarketEngine.feed_tick(tick)
    → CandleBuilder.ingest(tick)
        → Aggregates ticks into 15-min candle
        → On new interval: emit BuiltCandle via on_candle() callback
    → LiveMarketEngine._on_closed_candle(candle)
        → SignalEngine.on_candle(candle)
            If first candle (09:15–09:30): capture ORB high/low
            If subsequent candle & close > ORB_high: emit BUY GeneratedSignal
            If subsequent candle & close < ORB_low:  emit SELL GeneratedSignal
            (one signal per symbol per day — trade_locked flag)
        → GeneratedSignal dispatched to signal_callbacks

Signal callback chain (LiveSignalService.on_signal):
  1. Persist LiveSignal to MongoDB (unique index prevents duplicates)
  2. Broadcast via ws_manager (room: "signals")
  3. → PaperTradingService.on_signal(signal) [always active]
       → PaperRiskManager.evaluate() → PaperExecutionEngine.simulate_fill()
       → PaperPositionManager.open() → WS broadcast (paper:trades, paper:positions)
  4. → LiveExecutionService.execute_signal(signal) [only if LIVE_EXEC_ENABLED=True]
       → FailsafeCoordinator → LiveRiskManager → AngelOneBroker.place_order()
       → OrderStateMachine: PENDING → OPEN → WS broadcast (orders room)

11:30 IST  → live_signal_engine_stop: SignalEngine.deactivate() — no new signals
             (market engine continues for candle observability until 15:30)

15:15 IST  → paper_eod_close_all: force-close all open paper positions
             live_eod_close_all: force-close all open live positions via broker

15:30 IST  → live_session_cleanup: full engine reset, clear IntradayMarketState
15:35 IST  → paper_daily_reset: reset paper account daily counters, unpause
```

### EOD Strategy Pipeline (16:00–16:30 IST)
```
15:45 IST → eod_candle_sync
              HistoricalDataService.sync_eod()
              → AngelOne REST API: fetch today's 15-min candles for all active stocks
              → Upsert HistoricalCandle bucket documents

16:00 IST → daily_osd_detection
              StrategyService.run_detection_for_date(today)
              → For each active stock:
                  Fetch today's candles from HistoricalCandleRepository
                  OneSideDayDetector.detect(candles) → OneSideDetectionResult
                  Persist OneSideDay document

16:15 IST → daily_probability_update
              StrategyService.calculate_all_continuation_stats()
              → For each active stock:
                  ContinuationProbabilityEngine.compute(osd_history, lookback_days=252)
                  → P = count(OSD_today AND OSD_yesterday) / count(OSD_yesterday)
                  Persist ContinuationStatistic (tradable=True if P≥0.70 AND occurrences≥10)

16:30 IST → daily_shortlist_generation
              ShortlistService.generate_shortlist(target_date=next_trading_day)
              → Join OneSideDay (today) + ContinuationStatistic (tradable=True)
              → Returns ShortlistResult with entries sorted by probability desc
              → Available via GET /api/v1/shortlist/today from 16:30 onward
```

### Service → Repository → Model Flow
```
Route Handler
  ↓
Service Layer (app/services/)
  orchestrates pure-logic engines + repositories
  ↓
Repository Layer (app/repositories/)
  extends BaseRepository; owns all MongoDB I/O
  ↓
Beanie ODM Documents (app/models/)
  Pydantic v2 validated, Motor async driver
  ↓
MongoDB Collections
```

---

## SECTION 5 — DATABASE ARCHITECTURE

### Design Principles
- **Bucket pattern for candles**: One `HistoricalCandle` document = (symbol, trading_date, interval) with an embedded `candles: list[CandleData]`. Reduces ~2.3M documents to ~62,500 for 50 symbols × 5 years × 25 candles/day. Efficient full-day reads; the tradeoff is that individual candle updates require loading the full document (acceptable given strategy's day-by-day consumption pattern).
- **Beanie ODM**: All models extend `beanie.Document`. Beanie handles collection naming, index creation on first startup, and Pydantic v2 validation at the DB boundary.
- **UTC-only storage**: All `datetime` fields in MongoDB are UTC. IST conversion is done in the application layer via `app/utils/market_time.py`. Never store naive datetimes.
- **Upsert patterns**: Health status (`SystemHealthStatus`), intraday state (`IntradayMarketState`), and allocation state use upsert rather than insert to keep one active document per key.

### Collections (30 Documents)

| Collection | Purpose | Key Indexes |
|---|---|---|
| `stock` | Master stock universe. Fields: symbol, instrument_token, exchange, sector, active | `symbol` unique, `instrument_token` unique |
| `historical_candle` | Day-bucket OHLCV. Fields: symbol, trading_date, interval, candles[] | `(symbol, trading_date, interval)` unique compound |
| `market_data_sync_log` | Per-symbol data ingestion audit log | `(symbol, interval, sync_date)` |
| `one_side_day` | Daily OSD classification per symbol. Fields: is_one_side, direction (UP/DOWN), move_percent | `(symbol, trading_date)` unique; index on `is_one_side`, `direction` |
| `continuation_statistic` | P(OSD continuation) per symbol. Fields: probability, tradable, occurrences, lookback_days | `(symbol, lookback_days)` unique; index on `tradable`, `probability` |
| `backtest_run` | Backtest run metadata + config snapshot | `run_id` unique; index on `strategy_id`, `status`, `created_at` |
| `backtest_trade` | Individual simulated trades. Fields: run_id, symbol, trade_side, entry/exit prices, pnl | `run_id`; `(run_id, symbol)`; `(run_id, trading_date)` |
| `backtest_metrics` | Aggregate run metrics. Fields: win_rate, sharpe, max_drawdown, profit_factor, etc. | `run_id` unique |
| `research_run` | Research/optimization run metadata | `run_id` unique; index on `strategy_id`, `status` |
| `parameter_optimization_result` | Individual parameter set results from optimizer | `run_id`; `(run_id, parameter_set_id)` |
| `stock_performance_analytics` | Per-symbol historical stats (win_rate, expectancy, max_drawdown). Feeds SignalRanker | `(symbol, strategy_id)` unique |
| `live_signal` | Real-time ORB breakout signals. Fields: symbol, signal_type (BUY/SELL), entry_price, stop_loss, breakout_time | `(symbol, trading_date, signal_type)` unique; index on `strategy_id`, `trading_date` |
| `intraday_market_state` | Per-symbol intraday session state (ORB captured, signals emitted, candles seen) | `(symbol, trading_date)` unique |
| `paper_position` | Open/closed paper positions. Fields: status, entry_price, stop_loss, qty, unrealised_pnl | `position_id` unique; index on `status`, `trading_date` |
| `paper_trade` | Closed paper trade records with final P&L. Immutable after close | `trade_id` unique; index on `symbol`, `trading_date`, `strategy_id` |
| `paper_account` | Single paper account. Fields: starting_capital, available_capital, realized_pnl, daily counters | `account_id` unique |
| `live_order` | Real broker orders with full state history. Fields: order_id, broker_order_id, order_status, transitions[] | `order_id` unique; `(signal_id, broker_name)` unique; index on `broker_order_id`, `order_status` |
| `live_position` | Open/closed live positions with broker fill data | `position_id` unique; index on `status`, `trading_date` |
| `broker_session` | AngelOne JWT session persistence. Fields: jwt_token, refresh_token, feed_token, expiry | `broker_name` unique (upsert) |
| `portfolio_allocation` | Capital allocation decisions per signal per day | `allocation_id` unique; `(signal_id, trading_date)` |
| `portfolio_risk_state` | Daily portfolio risk snapshot (used_capital, daily_pnl, is_halted) | `(trading_date,)` |
| `system_health_status` | Per-component health check results. Upserted each minute | `component_name` unique |
| `system_incident` | Open/resolved infrastructure incidents. Fields: component, severity, status, description | `incident_id` unique; index on `component`, `status`, `severity` |
| `user` | API users. Fields: username, email, hashed_password, role | `username` unique; `email` unique |
| `audit_log` | Security audit trail for sensitive operations | `user_id`; index on `action`, `created_at` |
| `alert_event` | Alert dispatch records with dedup_key for burst suppression | `dedup_key`; index on `created_at` |
| `o_r_h_v_setup` | ORHV two-sided breakout detection results per symbol per day | `(symbol, detected_on)` unique |
| `o_r_h_v_validation_record` | ORHV historical validation simulation results | `(symbol, setup_id)` |
| `o_r_h_v_signal_record` | ORHV live signal records | `(symbol, trading_date)` unique |
| `o_r_h_v_statistics` | ORHV aggregate per-symbol statistics | `symbol` unique |

---

## SECTION 6 — STRATEGY FRAMEWORK

### BaseStrategy (`app/strategy/base_strategy.py`)
Abstract contract all strategies must implement. Enforces pure-logic separation — strategies contain ZERO database I/O, ZERO broker imports.

**Required interface:**
```python
class BaseStrategy(ABC):
    strategy_id: str (property)       # machine ID, e.g. "one_side_orb"
    strategy_name: str (property)     # display name
    strategy_version: str (property)  # semver string

    def get_default_config() -> dict
    def validate_configuration(config: dict) -> None  # raises ValueError on invalid
    def create_day_classifier(config) -> Any           # object with .classify(candles) -> DayClassificationResult
    def create_backtest_engine(config) -> Any          # object with .run(symbols, prob_scores, osd_history, candle_history) -> BacktestEngineResult
    def calculate_stop_loss(entry, orb_high, orb_low, side, config) -> float
    def calculate_targets(entry, orb_high, orb_low, side, config) -> list[float]
    def get_metadata() -> StrategyMetadata
```

**Key data types:**
- `StrategyMetadata`: immutable descriptor with strategy_id, name, version, description, category, parameters schema
- `DayClassificationResult`: framework-neutral result from classifiers (is_valid, strategy_signal, orb_high, orb_low, move_percent, etc.)

### Strategy Registry (`app/strategy/strategy_registry.py`)
Module-level singleton `registry = StrategyRegistry()`. Initialized once at startup via `_initialize_registry()` called from `app/strategy/__init__.py`.

**Adding a new strategy:**
1. Create `app/strategy/strategies/<strategy_id>/strategy.py` implementing `BaseStrategy`
2. Add `config.py` (dataclass with defaults), `constants.py` (STRATEGY_ID, STRATEGY_NAME, etc.)
3. Import and register in `strategy_registry._initialize_registry()`
4. If the strategy needs its own MongoDB models, add them to `app/database/init_db.py`

### Registered Strategies

---

#### Strategy 1: One-Side ORB (`one_side_orb`)
**Category:** Momentum | **Version:** 1.0.0  
**Files:** `app/strategy/strategies/one_side_orb/`

**Concept:**  
A stock that showed a strong, one-directional move on Day D (the "one-side day") tends to continue in that direction at the next day's opening range breakout. The strategy exploits this momentum continuation pattern.

**Day D — Detection (OneSideDayDetector):**
- First 15-min candle establishes the Opening Range (ORB high/low)
- A "one-side day" is confirmed when the close of any subsequent candle moves ≥`min_move_percent` (default 1%) from the ORB boundary in one direction WITHOUT crossing the opposite boundary
- `direction` = UP (close > ORB_high + threshold) or DOWN (close < ORB_low - threshold)
- Persisted as `OneSideDay` document

**Day D+1 — Execution:**
1. Prerequisite: Yesterday was an OSD (lookup `OneSideDay` for D-1)
2. Filter: `ContinuationStatistic.probability ≥ 0.70` (with ≥10 occurrences)
3. Filter: Day D+1 first candle range ≤ 1% (avoids wide-stop setups)
4. Entry window: 09:30–11:30 IST
5. **BUY** signal: candle close > ORB_high of Day D+1
6. **SELL** signal: candle close < ORB_low of Day D+1
7. Stop loss: ORB_low (LONG) or ORB_high (SHORT) ± sl_buffer_pct
8. Exit: Stop-loss hit OR EOD force-exit at 15:15 IST (no fixed targets)

**Key Parameters:**
| Parameter | Default | Description |
|---|---|---|
| `probability_threshold` | 0.70 | Min continuation probability |
| `min_move_percent` | 1.0 | Min % move from ORB boundary for OSD classification |
| `max_orb_range_pct` | 1.0 | Skip Day D+1 if ORB range > this % |
| `max_entry_time_ist` | "11:30" | Latest entry candle open time (IST) |
| `capital_per_trade` | ₹100,000 | Capital deployed per trade |
| `slippage_pct` | 0.05 | Fill slippage % applied both sides |
| `brokerage_per_side` | ₹20 | Flat brokerage per trade side |
| `sl_buffer_pct` | 0.0 | Extra SL buffer beyond ORB boundary |
| `lookback_days` | 252 | Trading-day window for probability (~1 year) |
| `min_occurrences` | 10 | Min OSD events before probability is reliable |

---

#### Strategy 2: ORHV (`orhv`)
**Category:** Momentum | **Version:** 1.0.0  
**Files:** `app/strategy/strategies/opening_range_historical_validation/`

**Concept:**  
A three-phase strategy. Day D must show a two-sided breakout (both above AND below the opening range). This setup is then historically validated (last 30 occurrences must have ≥70% win rate). Day D+1 trades whichever ORB side breaks first.

**Phase 1 — Setup Detection (Day D, ORHVSetupDetector):**
- `CH1`: First candle closing above Day D ORH (opening range high)
- `CL1`: First candle closing below Day D ORL (opening range low)
- Both must occur on Day D for a valid setup (two-sided breakout)
- Persisted as `ORHVSetup` document

**Phase 2 — Historical Validation (Day D, ORHVHistoricalValidator):**
- Look back at the last `lookback_occurrences` (default 30) ORHV setups for this symbol
- Simulate whether Day D+1 was profitable (did the ORB side that broke first end profitably by EOD?)
- Gate: `absolute_wins ≥ 21` OR `win_rate ≥ 70%`
- Persisted as `ORHVValidationRecord`

**Phase 3 — Execution (Day D+1):**
- Entry window: up to 12:00 IST (1 hour wider than One-Side ORB)
- Direction: whichever of ORB_high or ORB_low is breached first (not pre-determined unlike One-Side ORB)
- Stop loss: opposite ORB boundary (no buffer)
- Exit: SL hit or EOD

**Key Parameters:**
| Parameter | Default | Description |
|---|---|---|
| `lookback_occurrences` | 30 | Prior setups to simulate in Phase 2 |
| `qualification_min_wins` | 21 | Absolute wins threshold (with 30 lookback) |
| `qualification_min_win_rate` | 0.70 | Win-rate threshold (either/or with wins) |
| `min_occurrences_required` | 5 | Min prior setups before considering tradable |
| `max_orb_range_pct` | 1.0 | Skip if Day D+1 ORB range too wide |
| `max_entry_time_ist` | "12:00" | Latest entry (1h later than One-Side ORB) |

---

## SECTION 7 — BACKTESTING SYSTEM

### Architecture
**Pure-Python, no I/O.** `BacktestEngine` receives pre-fetched data dicts and replays the strategy. Services call `engine.run()` via `asyncio.run_in_executor(None, engine.run, ...)` to avoid blocking the event loop.

### Replay Engine (`app/strategy/backtest_engine.py`)
```
BacktestEngine.run(symbols, prob_scores, osd_history, candle_history)
  trading_days = get_trading_days(from_date, to_date)
  For each trading_date:
    prev_date = previous trading day discovered from osd_history keys
    For each symbol:
      Gate 1: osd_history[symbol][prev_date].is_one_side == True?
      Gate 2: prob_scores[symbol] >= probability_threshold?
      Gate 3: candle_history[symbol][date] has >= 2 candles?
      Gate 4: first_candle.range_pct <= max_orb_range_pct?
      → Build TradeSetup(symbol, side, orb_high, orb_low, prob, config)
      → TradeSimulator.simulate(setup, today_candles) → SimulatedTrade
  Return BacktestEngineResult(trades, candidate_days, no_data_days, symbols_processed)
```

**Previous-day discovery**: The engine finds `prev_date` by scanning all keys in `osd_history` that are before `current_date` — this avoids hardcoding weekend/holiday calendars and instead uses what data is actually present.

### Simulation Engine (`app/strategy/trade_simulator.py`)
`TradeSimulator.simulate(setup, candles)`:
1. Walk candles chronologically from index 1 (skip ORB candle)
2. Find first candle within entry window where close breaks ORB boundary
3. Apply entry slippage (LONG: price UP, SHORT: price DOWN)
4. Compute quantity: `floor(capital_per_trade / entry_price)` (minimum 1)
5. Walk remaining candles for SL hit
6. If no SL: exit at last candle close (EOD exit)
7. Apply exit slippage and compute full P&L including brokerage

**Exit reasons** (on SimulatedTrade): `SL_HIT`, `EOD_EXIT`, `NO_BREAKOUT`

### Metrics Engine (`app/strategy/metrics_engine.py`)
Computed from `list[SimulatedTrade]`:
- `win_rate`: wins / total trades
- `profit_factor`: gross_profit / gross_loss
- `expectancy`: (win_rate × avg_win) - (loss_rate × avg_loss)
- `max_drawdown`: largest peak-to-trough in cumulative P&L series
- `sharpe_ratio`: (mean_return - risk_free) / std_return × √252
- `calmar_ratio`: annualized_return / |max_drawdown|
- Total return, average win/loss, largest win/loss

### Look-Ahead Bias Protections
1. Gate 1 uses Day D-1 OSD, not Day D — the engine never uses the current day's result to decide the current day's trade
2. `_previous_trading_day_str()` discovers prior dates from actual data keys, not calendar arithmetic
3. Only CLOSED candles (with `end_time`) trigger signals in the live engine
4. Continuation probability is computed from data up to Day D-1 in live mode

---

## SECTION 8 — RESEARCH & OPTIMIZATION

### Parameter Optimizer (`app/research/parameter_optimizer.py`)
Grid search over configurable parameter ranges. Runs multiple backtests, ranks by Sharpe ratio or custom objective. Persists `ParameterOptimizationResult` documents linked to a parent `ResearchRun`.

### Analytics Modules (`app/research/`)
| Module | Analyzes |
|---|---|
| `stock_analytics.py` | Per-symbol win rate, expectancy, OSD frequency, best/worst months, hold time distribution |
| `time_analytics.py` | Performance by time-of-day bucket, day-of-week, month, quarter |
| `failure_analytics.py` | Why signals fail — SL hit %, no-breakout %, ORB too wide %, entry-window expired % |
| `market_condition_analytics.py` | Performance in trending vs. choppy vs. volatile market regimes |
| `report_generator.py` | Assembled research report combining all analytics dimensions |

### Attribution Engines (`app/analytics/`)
| Module | Computes |
|---|---|
| `math_helpers.py` | Pure math library: `sharpe_ratio`, `max_drawdown`, `profit_factor`, `expectancy`, `win_rate`, `volatility_annual`, `rolling_sharpe`, `contribution_pct`, `daily_pnl_series`, `cumulative_pnl_series`, `avg_win_avg_loss` |
| `strategy_attribution.py` | Per-strategy P&L, win rate, Sharpe, drawdown from paper/backtest/live trades |
| `stock_attribution.py` | Per-stock contribution to portfolio P&L |
| `portfolio_analytics.py` | Portfolio-level metrics composing strategy + stock engines |
| `capital_efficiency.py` | Capital utilization rate, deployment %, idle capital opportunity cost |
| `strategy_comparison.py` | Side-by-side performance comparison of multiple strategies |

Data sources for attribution: `TradingMode.PAPER` → PaperTrade, `TradingMode.BACKTEST` → BacktestTrade + BacktestMetrics, `TradingMode.LIVE` → LivePosition, `TradingMode.COMBINED` → all three merged.

---

## SECTION 9 — LIVE TRADING ARCHITECTURE

### Tick Feed → Signal Pipeline

**CandleBuilder (`app/live/candle_builder.py`)**
- Accepts raw `Tick(symbol, price, volume, timestamp)` via `ingest(tick)`
- Per-symbol in-memory candle accumulation (current open/high/low/close/volume)
- On first tick of a new 15-min interval: emits the completed `BuiltCandle` via registered `on_candle` callbacks
- Enforces `LIVE_RESPECT_MARKET_HOURS`: drops ticks outside NSE session (09:15–15:30 IST)

**MarketSessionEngine (`app/live/market_session.py`)**
NSE session constants (UTC):
- `FIRST_CANDLE_OPEN = time(3, 45)` (= 09:15 IST)
- `FIRST_CANDLE_CLOSE = time(4, 0)` (= 09:30 IST)
- `LATEST_ENTRY_TIME = time(6, 0)` (= 11:30 IST)

**LiveMarketEngine (`app/live/market_engine.py`)**
Hub that wires `CandleBuilder → SignalEngine`. Accepts ticks via `feed_tick(tick)`. Manages watchlist (set of subscribed symbols). Tracks stats (ticks_received, ticks_dropped, candles_emitted, reconnect_count).

> **IMPORTANT — Missing Integration:** The tick feed wiring is the **one remaining production integration gap**. `LiveMarketEngine.feed_tick()` is fully implemented. The AngelOne WebSocket client that pushes live ticks into it does not yet exist. See Priority 1 in Section 17.

**SignalEngine (`app/live/signal_engine.py`)**
- Activated each morning with today's shortlist
- `asyncio.Lock` guards all state mutations (thread-safety within asyncio)
- Captures ORB from first closed candle (09:15–09:30 IST)
- Breakout test: strict CLOSE comparison vs ORB boundaries (not intraday high/low)
- One signal per (symbol, trading_date): `trade_locked` flag + DB unique index enforce idempotency
- `lock_symbol(symbol)`: external call from service after persistence to prevent race conditions
- Emits `GeneratedSignal` to registered async callbacks

### Paper Trading Flow
```
GeneratedSignal
  → PaperRiskManager.evaluate(account_state, signal):
      is_paused? daily_trades_count >= max? open_positions >= max?
      daily_loss_pct >= limit? consecutive_losses >= cooldown?
      → If rejected: return PaperRiskResult(accepted=False, reason)
  → PaperExecutionEngine.simulate_fill(signal, available_capital):
      entry_price = trigger_price × (1 + slippage_pct/100) [LONG]
                  = trigger_price × (1 - slippage_pct/100) [SHORT]
      qty = floor(min(capital_per_trade, available_capital) / entry_price)
      → PaperFill(entry_price, qty, stop_loss, capital_used, brokerage)
  → PaperPositionManager.open_position(fill):
      Deduct capital_used from account.available_capital
      Insert PaperPosition document (status=OPEN)
  → PaperPnLEngine.update_position(position, current_price):
      unrealised_pnl = (current_price - entry_price) × qty × multiplier
  → On SL hit or EOD:
      PaperPositionManager.close_position(position, exit_price, reason)
      Insert PaperTrade document (immutable closed record with full P&L)
      Return capital to account
  → WebSocket broadcasts: paper:trades, paper:positions, paper:pnl, paper:account
```

**Paper Trading Risk Rules:**
- Starting capital: ₹10,00,000 (PAPER_STARTING_CAPITAL)
- Capital per trade: ₹1,00,000 (PAPER_CAPITAL_PER_TRADE)
- Max open positions: 5 (PAPER_MAX_OPEN_POSITIONS)
- Max trades per day: 10 (PAPER_MAX_TRADES_PER_DAY)
- Max daily loss: 2% of starting capital (PAPER_MAX_DAILY_LOSS_PCT)
- Consecutive loss cooldown: 3 (PAPER_CONSECUTIVE_LOSS_COOLDOWN)
- Max position size: 20% of starting capital (PAPER_MAX_POSITION_PCT)
- EOD exit: 15:15 IST (PAPER_EOD_EXIT_TIME_IST)

### Live Execution Flow
```
GeneratedSignal
  → LiveExecutionEngine.execute_signal(signal, risk_context):

  Step 1: Master switch check
    LIVE_EXEC_ENABLED == False → ExecutionOutcome(accepted=False, reason="live_execution_disabled")

  Step 2: Failsafe guards (FailsafeCoordinator.ensure_safe_to_trade):
    KillSwitch.engaged == True → raise TradingHaltedException
    is_market_open() == False → raise MarketClosedException  (if LIVE_EXEC_REQUIRE_MARKET_OPEN)
    (now - signal.breakout_time) > LIVE_EXEC_MAX_DATA_STALENESS_SECONDS → raise StaleMarketDataException
    Existing LiveOrder for (symbol, trading_date, signal_type) → raise DuplicateLiveOrderException

  Step 3: Risk gate (LiveRiskManager.evaluate):
    daily_loss_pct > LIVE_EXEC_MAX_DAILY_LOSS_PCT → reject
    drawdown_pct > LIVE_EXEC_MAX_DRAWDOWN_PCT → reject
    open_positions >= LIVE_EXEC_MAX_OPEN_POSITIONS → reject
    capital_exposure_pct > LIVE_EXEC_MAX_CAPITAL_EXPOSURE_PCT → reject
    trade_count >= LIVE_EXEC_MAX_TRADES_PER_DAY → reject

  Step 4: Resolve instrument
    StockRepository.get_by_symbol(symbol) → stock.instrument_token
    qty = floor(LIVE_EXEC_CAPITAL_PER_TRADE / signal.entry_price)

  Step 5: Persist PENDING LiveOrder ← idempotency boundary
    DB unique index on (signal_id, broker_name) catches concurrent duplicates

  Step 6: Submit to AngelOne broker
    tag = f"{order_id}|{instrument_token}"
    AngelOneAuth.get_session() → JWT (login if needed)
    POST /rest/secure/angelbroking/order/v1/placeOrder
    → OrderResponse(broker_order_id, status=OPEN)

  Step 7: State transition
    OrderStateMachine.transition(order, OPEN, broker_order_id)
    → Appends to transitions[] audit log
    → Persists via LiveOrderRepository

  → Return ExecutionOutcome(accepted=True, order, broker_order_id)
```

**Live Execution Risk Limits (defaults):**
- Total capital: ₹5,00,000
- Capital per trade: ₹50,000
- Max open positions: 3
- Max trades per day: 5
- Max position size: 15% of total capital
- Max aggregate exposure: 50% of total capital
- Max daily loss: 1.5% of total capital
- Max drawdown: 5% of total capital

**Order State Machine transitions:**
```
PENDING → OPEN | FILLED | PARTIALLY_FILLED | REJECTED | CANCELLED
OPEN → FILLED | PARTIALLY_FILLED | CANCELLED | REJECTED
PARTIALLY_FILLED → FILLED | CANCELLED
FILLED → (terminal)
REJECTED → (terminal)
CANCELLED → (terminal)
```

### AngelOne Broker Adapter (`app/brokers/angelone/`)
- `auth.py`: `AngelOneAuth.get_session()` — idempotent login (TOTP via pyotp), JWT caching (24h expiry, 30-min proactive refresh), `AngelOneSession(jwt_token, refresh_token, feed_token, expiry)`
- `client.py`: `AngelOneBroker` implements `BaseBroker` — translates `PlaceOrderRequest` to AngelOne payload, appends `-EQ` suffix for NSE cash equities, maps `OrderStatus` from AngelOne string responses
- `execution.py`: `AngelOneExecutionClient` — raw HTTP calls (place_order, cancel_order, fetch_order_book, fetch_positions)
- `historical_data.py`: Historical OHLCV data fetch with rate-limit delay (`INGESTION_API_DELAY_SECONDS=0.5s`)

---

## SECTION 10 — PORTFOLIO MANAGEMENT

### Signal Ranking (`app/portfolio/signal_ranker.py`)
Computes a composite `ranking_score ∈ [0.0, 1.0]` from 5 weighted factors:

| Factor | Weight | Source | Notes |
|---|---|---|---|
| `win_rate` | 25% | `StockPerformanceAnalytics.win_rate` | Historical win rate [0,1] |
| `expectancy` | 25% | `StockPerformanceAnalytics.expectancy` | ₹ per trade, normalised to [0,1] via ₹5k ceiling |
| `probability_score` | 25% | `ShortlistedCandidate.probability` | Continuation probability from shortlist |
| `stock_reliability` | 15% | `ContinuationStatistic.probability` | Raw continuation probability |
| `drawdown_penalty` | 10% | `StockPerformanceAnalytics.max_drawdown` | 1 - normalised_max_drawdown |

Missing data → neutral value 0.5. Score clamped to [0, 1] after weighted sum.

### Capital Allocation (`app/portfolio/capital_allocator.py`)
Three methods (set via `PORTFOLIO_ALLOCATION_METHOD`):
1. **EQUAL_WEIGHT**: `per_trade = min(available/n, max_per_trade)` — uniform distribution
2. **SCORE_WEIGHTED**: `per_trade_i = min(available × (score_i/Σscores), max_per_trade)` — proportional to ranking score; falls back to EQUAL_WEIGHT if all scores are zero
3. **FIXED_RISK**: `shares = (total_capital × PORTFOLIO_FIXED_RISK_PCT) / risk_per_share; per_trade = shares × entry_price` — sizes by stop-loss distance

All methods reject signals where `per_trade < PORTFOLIO_MIN_CAPITAL_PER_TRADE` (₹5,000).

### Portfolio Risk Manager (`app/portfolio/portfolio_risk_manager.py`)
8 rules, evaluated in priority order (short-circuit on first failure):

| Priority | Rule | Default Limit |
|---|---|---|
| 1 | Portfolio halt gate | `is_halted` flag OR daily_loss > 2% |
| 2 | Max open positions | 10 |
| 3 | Max capital exposure | 80% of total_capital |
| 4 | Max capital per trade | 20% of total_capital |
| 5 | Max capital per strategy | 50% of total_capital |
| 6 | Max capital per sector | 40% of total_capital |
| 7 | Max correlated positions (same GICS sector) | 3 |
| 8 | Sufficient available capital | available ≥ proposed_allocation |

The `PortfolioRiskContext` is a frozen dataclass — the service builds it before calling `evaluate()`. The risk manager never reads from DB.

---

## SECTION 11 — WEBSOCKET & REAL-TIME SYSTEMS

### Connection Manager (`app/websocket/manager.py`)
`ConnectionManager` singleton (`ws_manager`) at module level:
- `_connections: dict[str, WebSocket]` — client_id to WebSocket
- `_rooms: dict[str, set[str]]` — room name to client_id set
- `broadcast(data)`: serializes to JSON, sends to all connected clients
- `broadcast_to_room(data, room)`: sends only to room subscribers
- Dead connections auto-removed on send failure (WebSocketState check)
- `disconnect(client_id, room)`: removes from both maps; cleans empty rooms

### WebSocket Endpoints

| Path | Room | Published By | Payload |
|---|---|---|---|
| `/ws/market/{symbol}` | `market:{SYMBOL}` | LiveMarketEngine | Built candles, tick updates |
| `/ws/signals` | `signals` | LiveSignalService | GeneratedSignal events |
| `/ws/orders` | `orders` | LiveExecutionService | LiveOrder status transitions |
| `/ws/live/market-state` | `live:market-state` | LiveSignalService | Engine started/stopped events |
| `/ws/paper/trades` | `paper:trades` | PaperTradingService | Paper trade open/close events |
| `/ws/paper/positions` | `paper:positions` | PaperTradingService | Position MTM updates |
| `/ws/paper/pnl` | `paper:pnl` | PaperPnLEngine | Portfolio PnL snapshots |
| `/ws/paper/account` | `paper:account` | PaperTradingService | Account state changes |
| `/ws/notifications` | `notifications` | NotificationService | All alert events |

> **Security note:** All WebSocket endpoints are currently **unauthenticated**. JWT validation via `?token=<jwt>` query parameter is noted in code as "to be added" (see Section 17, Priority 2).

### Frontend WebSocketManager (`frontend/src/websocket/WebSocketManager.ts`)
- `wsManager` singleton manages all room connections
- `subscribe(room, handler)` → returns unsubscribe function (auto-disconnect when 0 subscribers)
- Auto-reconnect with exponential backoff: 1s → 2s → 4s → ... → 30s max
- `VITE_WS_BASE_URL` env var overrides base URL for non-localhost deploys
- Status lifecycle: `disconnected → connecting → connected | error → disconnected`

---

## SECTION 12 — SCHEDULER ARCHITECTURE

**APScheduler** `AsyncIOScheduler` runs in the FastAPI event loop (same process). All jobs are async coroutines. Global defaults: `coalesce=True` (collapse missed runs), `max_instances=1` (no overlapping), `misfire_grace_time=30s`. Timezone: `Asia/Kolkata` (IST). JobStore: `MemoryJobStore` (lost on restart — see Section 16 for upgrade path).

### Registered Jobs (23 total)

**Market Data Jobs:**
| Job ID | Trigger | Time (IST) | Description |
|---|---|---|---|
| `pre_market_sync_check` | cron Mon-Fri | 08:30 | Verify yesterday's data; backfill if missing |
| `eod_candle_sync` | cron Mon-Fri | 15:45 | Fetch today's 15-min candles for all stocks |

**Strategy Pipeline Jobs:**
| Job ID | Trigger | Time (IST) | Description |
|---|---|---|---|
| `daily_osd_detection` | cron Mon-Fri | 16:00 | Classify today as OSD/choppy/invalid |
| `daily_probability_update` | cron Mon-Fri | 16:15 | Recalculate continuation probabilities |
| `daily_shortlist_generation` | cron Mon-Fri | 16:30 | Generate tomorrow's tradable shortlist |

**Live Signal Engine Jobs:**
| Job ID | Trigger | Time (IST) | Description |
|---|---|---|---|
| `live_market_open_init` | cron Mon-Fri | 09:10 | Load shortlist, log readiness |
| `live_signal_engine_start` | cron Mon-Fri | 09:15 | Subscribe symbols, activate SignalEngine |
| `live_signal_engine_stop` | cron Mon-Fri | 11:30 | Deactivate signal generation (entry window close) |
| `live_session_cleanup` | cron Mon-Fri | 15:30 | Full engine reset, clear state |
| `live_health_heartbeat` | cron Mon-Fri, every 1m | 09:00–15:59 | Broadcast health snapshot |

**Paper Trading Jobs:**
| Job ID | Trigger | Time (IST) | Description |
|---|---|---|---|
| `paper_session_warmup` | cron Mon-Fri | 09:14 | Hydrate paper account + open positions |
| `paper_eod_close_all` | cron Mon-Fri | 15:15 | Force-close all open paper positions |
| `paper_daily_reset` | cron Mon-Fri | 15:35 | Reset daily counters, clear pause state |

**Live Execution Jobs:**
| Job ID | Trigger | Time (IST) | Description |
|---|---|---|---|
| `live_broker_session_refresh` | cron Mon-Fri | 08:30 | Force fresh AngelOne JWT |
| `live_session_warmup` | cron Mon-Fri | 09:14 | Hydrate live position book |
| `live_order_reconcile` | interval, every 5s | Market hours | Poll broker order status |
| `live_position_reconcile` | interval, every 5m | Market hours | Cross-check positions vs broker |
| `live_halt_monitor` | interval, every 1m | Market hours | Reassess halt criteria |
| `live_eod_close_all` | cron Mon-Fri | 15:15 | Force-close all live positions |

**Monitoring Jobs:**
| Job ID | Trigger | Time (IST) | Description |
|---|---|---|---|
| `health_check_market_hours` | cron Mon-Fri, every 1m | 09:00–16:00 | All 7 component health checks |
| `health_check_off_hours` | cron Mon-Fri, every 5m | Off-hours | Keep DB health checks warm |
| `daily_ops_report` | cron Mon-Fri | 15:50 | Generate + send EOD ops report |

**Notification Jobs (registered in notification_jobs.py):**
| Job ID | Trigger | Time (IST) | Description |
|---|---|---|---|
| `notification_daily_summary` | cron Mon-Fri | 15:45 | Send daily P&L summary |

---

## SECTION 13 — API ARCHITECTURE

### Versioning Strategy
All application routes: `/api/v1/`. Future `/api/v2/` can be added without breaking existing clients. Health endpoints at root: `/health`, `/health/ready`. WebSocket endpoints at root: `/ws/*`.

### Route Groups

| Prefix | Tags | Auth | Key Endpoints |
|---|---|---|---|
| `/health` | — | None | `GET /health`, `GET /health/ready` |
| `/api/v1/auth` | Authentication | Public | `POST /login`, `POST /refresh`, `GET /me`, `POST /change-password`, `GET /users` |
| `/api/v1/stocks` | Stocks | JWT | `GET /`, `POST /`, `PUT /{symbol}`, `DELETE /{symbol}` |
| `/api/v1/sync` | Data Sync | JWT | `POST /historical`, `POST /eod`, `GET /status` |
| `/api/v1/analysis` | Strategy Analysis | JWT | OSD detection, continuation probability endpoints |
| `/api/v1/shortlist` | Shortlist | JWT | `GET /today`, `GET /{date}`, `POST /generate` |
| `/api/v1/backtest` | Backtesting | JWT | `POST /run`, `GET /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/trades`, `GET /runs/{run_id}/metrics` |
| `/api/v1/research` | Research & Optimization | JWT | `POST /run`, `GET /runs`, parameter optimization endpoints |
| `/api/v1/live` | Live Signals | JWT | `GET /signals`, `GET /signals/today`, `GET /engine/status`, `POST /engine/start`, `POST /engine/stop` |
| `/api/v1/live` (execution) | Live Execution | JWT | `GET /orders`, `GET /positions`, `POST /kill-switch/engage`, `POST /kill-switch/release`, `POST /close-all` |
| `/api/v1/paper` | Paper Trading | JWT | `GET /account`, `GET /positions`, `GET /trades`, `GET /pnl`, `POST /reset` |
| `/api/v1/system` | System | JWT | `GET /settings`, `GET /strategies`, `GET /scheduler/jobs` |
| `/api/v1/notifications` | Notifications | JWT | `GET /alerts`, `POST /test` |
| `/api/v1/orhv` | ORHV Strategy | JWT | `GET /setups`, `GET /signals`, `GET /statistics`, `POST /detect` |
| `/api/v1/portfolio` | Portfolio | JWT | `GET /allocations`, `GET /risk-state`, `POST /allocate` |
| `/api/v1/analytics` | Performance Attribution | JWT | `GET /strategy`, `GET /stock`, `GET /portfolio`, `GET /capital-efficiency`, `GET /compare` |
| `/api/v1/health` | Health Monitoring | JWT | `GET /` (live check), `GET /components`, `GET /incidents`, `GET /summary`, `GET /heartbeats` |
| `/api/v1/ops` | Ops Dashboard | JWT | `GET /health`, `GET /incidents`, `POST /incidents/{id}/resolve`, `GET /heartbeats` |

### Authentication
- JWT Bearer tokens (HS256 algorithm)
- Access token: 30-minute expiry (`ACCESS_TOKEN_EXPIRE_MINUTES`)
- Refresh token: 30-day expiry (`REFRESH_TOKEN_EXPIRE_DAYS`)
- `AUTH_REQUIRED=False` by default (development mode — all routes pass without token)
- In production: set `AUTH_REQUIRED=True` and `JWT_SECRET` to a strong random value
- Admin user auto-seeded on first startup from `INITIAL_ADMIN_*` settings
- Docs (Swagger/ReDoc) disabled in production (`is_production=True`)

---

## SECTION 14 — FRONTEND ARCHITECTURE

### Pages (8 + Login)
| Page | Route | WebSocket Rooms | Purpose |
|---|---|---|---|
| `Login` | `/login` | None | JWT credential form, stores token in Zustand |
| `Dashboard` | `/` | `signals` | Portfolio overview: recent signals, PnL summary, health pill |
| `LiveSignals` | `/live-signals` | `signals`, `live:market-state` | Real-time signal stream with entry/SL details |
| `LiveTrading` | `/live-trading` | `orders` | Live execution: open positions, order book, kill switch toggle |
| `PaperTrading` | `/paper-trading` | `paper:trades`, `paper:positions`, `paper:pnl`, `paper:account` | Paper positions, closed trades, P&L chart, account balance |
| `Analytics` | `/analytics` | None | TanStack Query: strategy/stock/portfolio attribution charts |
| `Shortlist` | `/shortlist` | None | Tomorrow's shortlist table with probabilities and direction |
| `Settings` | `/settings` | None | System config display, strategy list |
| `SystemMonitor` | `/system-monitor` | None (polling) | Health checks status, open incidents, heartbeat timeline |

### State Management (Zustand)
| Store | File | Key State |
|---|---|---|
| `useAuthStore` | `store/useAuthStore.ts` | `user`, `token`, `isAuthenticated`, `login()`, `logout()` |
| `useSignalStore` | `store/useSignalStore.ts` | `signals[]`, `engineStatus`, `addSignal()` |
| `usePaperStore` | `store/usePaperStore.ts` | `positions[]`, `trades[]`, `account`, `pnl` |
| `useSettingsStore` | `store/useSettingsStore.ts` | `settings`, `strategies[]` |
| `useSystemStore` | `store/useSystemStore.ts` | `health`, `incidents[]`, `heartbeats` |

### Data Fetching
TanStack React Query: `staleTime: 10_000ms`, `retry: 2`, exponential retry delay capped at 10s. Typed responses via `frontend/src/types/`.

### Routing
React Router v6 with `RouterProvider`. All routes except `/login` are wrapped by `ProtectedRoute` (redirects to `/login` if not authenticated).

### Build & Dev
- `npm run dev` → Vite dev server (HMR)
- `npm run build` → TypeScript check + Vite production build → `dist/`
- `VITE_API_BASE_URL` and `VITE_WS_BASE_URL` env vars configure API and WebSocket hosts

---

## SECTION 15 — OPERATIONAL STATUS

### Completed Systems (18 major subsystems)
1. **Foundation** — FastAPI app factory, Pydantic settings, middleware stack, exception handlers, JWT auth, admin seeding, rate limiting, security headers
2. **Data Ingestion** — AngelOne historical data, HistoricalCandle bucket storage, MarketDataSyncLog, pre-market check, EOD sync, batch processor
3. **Strategy Framework** — BaseStrategy abstract contract, StrategyRegistry singleton, DayClassificationResult, new strategy template scaffold
4. **One-Side ORB Strategy** — OneSideDayDetector, ContinuationProbabilityEngine, BacktestEngine, TradeSimulator, MetricsEngine
5. **ORHV Strategy** — ORHVSetupDetector, ORHVHistoricalValidator, ORHVSignalGenerator, ORHVBacktestEngine, ORHV-specific MongoDB models
6. **Backtesting Service** — BacktestService orchestrating historical replay, BacktestRun/Trade/Metrics persistence, bulk insert batching, thread-pool execution
7. **Research & Optimization** — ParameterOptimizer, StockAnalytics, TimeAnalytics, FailureAnalytics, MarketConditionAnalytics, ReportGenerator
8. **Live Signal Engine** — CandleBuilder, MarketSessionEngine, LiveMarketEngine, SignalEngine (with asyncio lock), health monitor
9. **Paper Trading Engine** — PaperExecutionEngine, PaperPositionManager, PaperRiskManager, PaperPnLEngine, PaperSessionManager
10. **Live Execution Engine** — LiveExecutionEngine, OrderStateMachine, KillSwitch + FailsafeCoordinator, LivePositionManager, LiveRiskManager, AngelOneBroker
11. **Portfolio Engine** — SignalRanker (5-factor), CapitalAllocator (3 methods), PortfolioRiskManager (8 rules), PortfolioRiskState persistence
12. **Performance Attribution** — StrategyAttributionEngine, StockAttributionEngine, PortfolioAnalyticsEngine, CapitalEfficiencyEngine, StrategyComparisonEngine, math_helpers library
13. **Monitoring Platform** — 9 health checks (MongoDB, broker, WebSocket, scheduler, signal engine, portfolio, execution, paper trading engine, reconciliation engine), HealthAggregator, Heartbeat tracker (9 components), IncidentManager, AlertRouter, DailyReport, RiskMonitor, MarketDataMonitor, ExecutionMonitor. Dedicated `/api/v1/health/` route group with 5 endpoints (live check, components, incidents, summary, heartbeats). 46 new tests in test_monitoring_platform.py.
14. **Notification System** — TelegramNotifier, EmailNotifier, NotificationManager, DailySummary, burst dedup with `dedup_key`, message templates
15. **WebSocket Layer** — ConnectionManager, 9 endpoints, room-based targeted broadcast
16. **React Dashboard** — 8 pages, 5 Zustand stores, typed Axios API clients, WebSocketManager with exponential-backoff reconnect
17. **Scheduler** — 23 registered jobs across 7 modules covering full trading day lifecycle
18. **REST API** — 16 route groups under /api/v1, health endpoints, WS endpoints, OpenAPI docs (dev only)

### Partially Completed Systems
1. **AngelOne WebSocket Tick Feed** — `LiveMarketEngine.feed_tick()` is fully operational. The AngelOne WebSocket client that subscribes to live ticks and pushes them in is **not yet implemented**. Without this, live signals cannot be generated from real market data (only via API injection for testing).
2. **WebSocket Authentication** — JWT token validation for WS connections is explicitly noted in `websocket_routes.py` as "to be added". Currently all 9 WS endpoints are unauthenticated.
3. **Portfolio ↔ Live Execution Integration** — `PortfolioService` (ranking + allocation + risk) is built and tested independently. It is not yet wired into the live execution flow to enforce portfolio-level constraints before broker order placement.

### Missing Systems
1. **AngelOne WebSocket client** (`app/brokers/angelone/websocket.py`) — real-time tick feed delivery
2. **Multi-account / multi-user live trading** — single paper account, single live execution context
3. **Kelly criterion / dynamic position sizing** — only EQUAL_WEIGHT, SCORE_WEIGHTED, FIXED_RISK
4. **Options / derivatives support** — equity cash market only
5. **Portfolio-level backtesting with capital constraints** — individual symbol backtests only; no simultaneous multi-position simulation with shared capital pool
6. **Walk-forward / out-of-sample optimization** — grid search only; no rolling train/test windows
7. **Strategy hot-reload** — requires process restart to add new strategies

---

## SECTION 16 — TECHNICAL DEBT

### Code Smells
1. **Implicit scheduler job ordering via time offsets**: EOD jobs at 16:00/16:15/16:30 depend on sequential completion. If `daily_osd_detection` (16:00) runs slow, `daily_probability_update` (16:15) may find incomplete data. No retry/dependency checking exists.
2. **Private method called from scheduler**: `live_halt_monitor` in `live_execution_jobs.py` calls `live_execution_service._check_post_trade_halts()` — a name-mangled private method. Should be `check_halt_conditions()` public method.
3. **ORHV strategy hardcoded in BacktestService**: `BacktestService._load_candle_history()` contains `if strategy_id == "orhv": extend_lookback()`. Should be a `get_candle_history_lookback_days()` method on `BaseStrategy`.
4. **Services instantiated per-request**: Many route handlers do `HistoricalDataService()`, `StrategyService()`, etc. inline. FastAPI `Depends()` with singletons would be cleaner and more testable.

### Scalability Concerns
1. **Single-process WebSocket manager**: `ConnectionManager` is in-memory. Horizontal scaling (multiple uvicorn workers or K8s pods) requires external pub/sub (Redis Streams, RabbitMQ).
2. **In-memory live state**: `LiveMarketEngine._watchlist`, `SignalEngine._states`, `PaperPositionManager._positions` are all process-local. Multiple workers = split state.
3. **APScheduler MemoryJobStore**: All jobs lost on restart. During 09:15 IST restart, `live_signal_engine_start` job is silently dropped (`misfire_grace_time=30s`). Switch to `MongoDBJobStore` — 3-line change already documented in `scheduler.py`.
4. **No connection pooling for broker HTTP**: Each broker API call creates a new `httpx` connection. Should use a persistent `httpx.AsyncClient` with connection pooling in `AngelOneExecutionClient`.
5. **Notification dedup via MongoDB query on hot path**: The 5-minute dedup window queries `AlertEvent` by `dedup_key` on every alert. Under high volume, add an in-memory LRU cache as first-level dedup.

### Missing Tests
1. End-to-end integration tests spanning scheduler → service → repository → DB
2. ORHV signal generator in live context (currently only backtest tests)
3. `PortfolioService` integration tests (ranking + allocation + risk together)
4. AngelOne broker adapter tests beyond mocks (sandbox/staging integration)
5. Frontend unit tests (zero Vitest / React Testing Library coverage)
6. Failsafe system under concurrent signal load

### Architectural Risks
1. **No circuit breaker on AngelOne API**: Sustained broker outages exhaust 2 retries per order, potentially locking execution coroutines. Implement circuit breaker (e.g. `aiobreaker`) around broker HTTP calls.
2. **IST/UTC inconsistency risk**: Some models use UTC midnight `datetime`, others use plain `date`. Edge-case potential around midnight IST on trading day boundaries. Audit all `trading_date` fields.
3. **Job misfire at market open**: If the server is restarting at 09:15 IST, `live_signal_engine_start` (grace period 30s) will be dropped. Add a startup routine that checks and fires missed critical jobs.
4. **Process-local kill switch semantics**: On restart, kill switch resets to OFF. This is intentional (fail-safe), but operators must re-engage manually if there's a crash during a halt. Document this clearly in runbook.

---

## SECTION 17 — NEXT PRIORITY ROADMAP

### Priority 1: AngelOne WebSocket Tick Feed
**Why it's #1**: This is the last remaining piece between the current codebase and live signal generation from real market data. The entire pipeline (`CandleBuilder → SignalEngine → PaperTrade / LiveTrade`) is built and waiting for ticks.  
**What to build**: `app/brokers/angelone/websocket.py` — connects to AngelOne SmartAPI WebSocket, subscribes to `instrument_token` list for shortlisted symbols using the `feed_token` from `AngelOneAuth`, and calls `live_market_engine.feed_tick(Tick(symbol, price, volume, timestamp))` on each tick event. Handle reconnection and subscription management.

### Priority 2: WebSocket Authentication
**Why**: All 9 WebSocket endpoints are unauthenticated. This is a security boundary gap for any non-localhost deployment. Before exposing to the internet, validate a JWT token passed as `?token=<jwt>` at the WS handshake stage, before calling `ws_manager.connect()`.

### Priority 3: Portfolio ↔ Live Execution Integration
**Why**: `PortfolioService` exists but is disconnected from the live execution flow. `LiveExecutionService.execute_signal()` runs its own `LiveRiskManager` but does not call `PortfolioService.evaluate_signal()` for portfolio-level constraints (sector concentration, strategy caps, score-weighted sizing). Wire these together so live trades respect portfolio composition rules.

### Priority 4: Production Environment Hardening
**Why**: Several settings are development defaults unsafe for production.
- `AUTH_REQUIRED=False` → set `True`
- `JWT_SECRET="change-me-in-production"` → rotate with a cryptographically random value
- `LIVE_EXEC_ENABLED=False` → set `True` after tick feed integration and smoke tests
- APScheduler `MemoryJobStore` → `MongoDBJobStore` (3-line change in `scheduler.py`)
- Set `APP_ENV=production` to disable Swagger/ReDoc

### Priority 5: Live Trading Smoke Tests
**Why**: Before setting `LIVE_EXEC_ENABLED=True`, run a structured verification:
1. AngelOne TOTP login round-trip completes cleanly
2. Instrument token resolution works for all shortlisted symbols
3. Place a 1-share MARKET order on a liquid stock; verify PENDING → OPEN transition
4. Cancel the order; verify CANCELLED state; confirm no position opened
5. Verify EOD close-all fires and flattens positions

### Priority 6: APScheduler MongoDBJobStore
**Why**: The current MemoryJobStore loses all jobs on restart. During a crash/restart at 09:15 IST, the `live_signal_engine_start` job is silently dropped (misfire_grace_time=30s). The fix is already documented in `scheduler.py` — swap `MemoryJobStore()` with `MongoDBJobStore(database="trading_bot", collection="scheduler_jobs")`.

### Priority 7: Walk-Forward Parameter Optimization
**Why**: The parameter optimizer does grid search over the full historical period, which overfits to training data. Walk-forward validation (rolling 252-day train, 63-day test windows, re-optimize each fold) is essential before relying on optimized parameters for live trading.

### Priority 8: Circuit Breaker for AngelOne API
**Why**: Sustained broker outages or rate-limiting cause every order attempt to exhaust its 2 retries before giving up. Under 10+ open signals, this fills the asyncio queue with blocked coroutines. Implement a circuit breaker (CLOSED → OPEN → HALF-OPEN) around `AngelOneExecutionClient` HTTP calls.

### Priority 9: Persistent httpx.AsyncClient for Broker HTTP
**Why**: Each `AngelOneExecutionClient` call currently creates a new HTTP connection. For production throughput during signal bursts, use a module-level `httpx.AsyncClient` with a connection pool (set `limits=httpx.Limits(max_connections=10)`).

### Priority 10: Frontend Tests
**Why**: The dashboard has 8 pages and complex WebSocket state with zero automated test coverage. Add Vitest + React Testing Library for: login flow (token storage + redirect), signal display on WebSocket message, paper position P&L updates, WebSocket reconnect behavior, protected route redirect.

---

## SECTION 18 — FUTURE CLAUDE INSTRUCTIONS

### Architectural Rules (NEVER VIOLATE)

1. **Strategies are pure logic.** `app/strategy/` and `app/strategy/strategies/` must never contain database I/O, HTTP calls, or broker imports. Services pre-fetch all data and pass it in as arguments. If a strategy needs data from DB, the service fetches it; the strategy computes with it.

2. **Repositories own all MongoDB access.** Services never call `Model.find()`, `Model.insert()`, `Model.save()` directly. All MongoDB operations go through the repository layer (`app/repositories/`). This makes services testable with mock repos and keeps query logic centralized.

3. **BaseBroker is the broker boundary.** `live_execution/execution_engine.py` depends on `BaseBroker` only, never on `AngelOneBroker` directly. All broker-specific code lives in `app/brokers/<broker_name>/`. New brokers implement `BaseBroker`.

4. **No circular imports.** Dependency direction: `config → models → repositories → services → routes`. Strategy, analytics, monitoring, and notification modules are lateral — they depend on repositories but not on each other's services.

5. **UTC in DB, IST at presentation.** All `datetime` values stored in MongoDB are UTC. Use `app/utils/market_time.py` for IST↔UTC conversion. Never store naive `datetime` objects. Convert to IST only in API responses, logs, and scheduler job definitions.

6. **One strategy = one package.** Each strategy lives in `app/strategy/strategies/<id>/` with `strategy.py`, `config.py`, `constants.py`. Registration is in `strategy_registry._initialize_registry()`. Services access strategies via `registry.get(strategy_id)` — never instantiate strategy classes directly in services.

7. **APScheduler timezone is IST.** All `cron` triggers in job files express times in IST (`Asia/Kolkata`). Never express scheduler times in UTC.

8. **WebSocket broadcasts are fire-and-forget.** All `ws_manager.broadcast*()` calls must be wrapped in try/except or use the existing safe-send pattern. A failed WS send must never interrupt a trading operation.

9. **Settings are the single source of truth.** Never hardcode thresholds, limits, or API keys outside `app/config/settings.py`. New configurable values must be added as `Field(default=..., description=...)` with a descriptive docstring.

10. **The kill switch is process-local and non-persistent by design.** The system fails safe on restart (trading off by default). Do not add DB persistence to the kill switch without explicit design review.

### Coding Standards

- **Python 3.12** — use modern type hints (`list[str]` not `List[str]`), `match` statements, `|` union types
- **Pydantic v2** — use `model_construct()` for performance-critical internal paths, `model_validate()` for untrusted external data
- **Beanie v2** — every Document subclass needs `class Settings` with `name` (collection) and `indexes` list. Register in `app/database/init_db.py`
- **Async-first** — all I/O is `async def`. CPU-bound work (backtest replay) uses `asyncio.run_in_executor(None, sync_fn, ...)`
- **No docstrings on trivial methods.** Only document WHY (non-obvious constraints, workarounds). Don't explain WHAT — well-named code does that
- **Structured logging** — use `get_logger(__name__)` from `app/utils/logger.py`. Log level: INFO for normal operations, WARNING for recoverable anomalies, ERROR for failures
- **Frozen dataclasses for engine results** — `@dataclass(frozen=True)` for `GeneratedSignal`, `PaperFill`, `ExecutionOutcome`, `AllocationResult`, etc. Mutable state lives in model documents
- **Test isolation** — inject mock repositories rather than patching Beanie directly. Use `pytest-asyncio` with `@pytest.mark.asyncio`
- **Error handling in jobs** — all scheduler jobs wrap their body in `try/except Exception as exc: logger.error(..., exc_info=True)`. Never let a job raise — it would kill the APScheduler thread

### Patterns to Preserve

1. **Bucket pattern in HistoricalCandle** — do not convert to per-candle documents. The compound unique index `(symbol, trading_date, interval)` is the backbone of all data access patterns.
2. **Strategy engine factories** — services call `strategy.create_backtest_engine(config)` to get an engine; they never instantiate `BacktestEngine` directly. This decoupling lets strategies be swapped without service changes.
3. **`create_app()` factory pattern** in `main.py` — allows test fixtures to create isolated app instances without module-level side effects.
4. **`BaseRepository` as superclass** — all repositories extend it for consistent CRUD and to centralise Motor/Beanie access patterns.
5. **`lru_cache` on `get_settings()`** — the Settings singleton is parsed once. Always import `settings` from `app.config.settings`. Never call `Settings()` directly.
6. **Seed admin on startup** — `seed_admin_if_missing()` in lifespan. Essential for bootstrapping fresh deployments. Do not remove.
7. **`_initialize_registry()` idempotency guard** — `_registry_initialized` flag prevents double-registration. Preserve this pattern.
8. **Signal idempotency via DB unique index + in-process lock** — the combination of `asyncio.Lock` (fast path) and `(signal_id, broker_name)` unique index (durable path) prevents duplicate orders under all concurrency conditions.
9. **`@dataclass(frozen=True)` for signal/fill/outcome types** — immutability prevents accidental state mutation in multi-step pipelines.

### Things That Should NEVER Be Rewritten

1. **`app/strategy/trade_simulator.py`** — the core of the backtest engine. Touched by all strategy backtest tests. Only extend via subclassing; never rewrite the simulation loop.
2. **`app/live/signal_engine.py`** — the `asyncio.Lock` around state mutations is critical. The `trade_locked` flag + lock combination prevents duplicate signals under concurrent candle delivery. Do not simplify or remove locking.
3. **`app/live_execution/order_state_machine.py`** — the `_ALLOWED` transitions dict is the formal model of the order lifecycle. Changes here have production correctness implications. Any new states require corresponding DB migration and broker adapter updates.
4. **`app/live_execution/failsafe.py`** — the kill switch must remain process-local and non-persistent (fail-safe design). Never add DB persistence without full design review.
5. **`app/database/init_db.py`** — the `DOCUMENT_MODELS` list drives Beanie initialization and index creation. Always add new models here. Never remove a model without also dropping the collection and all references.
6. **`app/config/settings.py`** — the contract between the application and its environment. All configuration must flow through here with explicit type annotations and `description` fields for documentation.
7. **`app/brokers/base.py`** — the `BaseBroker` ABC. Changes require updating all broker adapters simultaneously. Extend with new methods only when all adapters can implement them.

### How to Add a New Strategy

```
1. mkdir -p app/strategy/strategies/<strategy_id>/
2. touch app/strategy/strategies/<strategy_id>/__init__.py
3. Create constants.py: STRATEGY_ID, STRATEGY_NAME, STRATEGY_VERSION, STRATEGY_CATEGORY, STRATEGY_DESCRIPTION
4. Create config.py: @dataclass StrategyConfig with defaults + to_dict() + from_dict()
5. Create strategy.py: class YourStrategy(BaseStrategy) — implement ALL abstract methods
6. (Optional) Create app/models/<strategy_id>_model.py for strategy-specific collections
7. (Optional) Register models in app/database/init_db.py
8. In strategy_registry._initialize_registry():
       from app.strategy.strategies.<strategy_id>.strategy import YourStrategy
       registry.register(YourStrategy())
9. Create tests/test_<strategy_id>*.py with unit tests for detector + backtest engine
10. Copy from app/strategy/templates/new_strategy_template/ as a starting scaffold
```

### How to Add a New Scheduler Job

```
1. Add async job function to the appropriate app/scheduler/jobs/<category>_jobs.py
   - Wrap entire body in try/except Exception
   - Lazy-import services inside the function to avoid circular imports at module load
2. Add to the register_<category>_jobs(scheduler) function:
       scheduler.add_job(
           your_job,
           trigger="cron",
           day_of_week="mon-fri",
           hour=HH, minute=MM,
           id="job_id",
           name="Human Readable Name",
           replace_existing=True,
       )
3. All times are IST — document why the time was chosen
4. Add a logger.info("Registered job: ...") line after scheduler.add_job()
```

### How to Add a New API Route Group

```
1. Create app/routes/v1/<feature>.py:
       router = APIRouter()
       @router.get("/")
       async def list_things(): ...
2. Create app/schemas/<feature>.py for request/response Pydantic models
3. Create app/services/<feature>_service.py for business logic
4. Register in app/routes/v1/__init__.py:
       from app.routes.v1.<feature> import router as <feature>_router
       router.include_router(<feature>_router, prefix="/<feature>", tags=["..."], dependencies=_auth_dep)
5. Add corresponding typed API client in frontend/src/api/<feature>.ts
```

---

*This document was generated by exhaustive static analysis of the live codebase (2026-05-29). Accurate as of that date. Update whenever significant architectural changes are made.*
