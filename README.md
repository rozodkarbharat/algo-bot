# TradingBot — Intraday Algo Trading Backend

Production-grade Python backend for an intraday algorithmic trading system built with FastAPI, MongoDB (Motor + Beanie), APScheduler, and WebSockets.

---

## Architecture Overview

```
trading-bot/
├── app/
│   ├── main.py                        ← FastAPI app factory + lifespan
│   ├── config/settings.py             ← Pydantic BaseSettings (all env vars)
│   ├── database/
│   │   ├── mongodb.py                 ← Motor client, connect/disconnect, retry
│   │   └── init_db.py                 ← Beanie document model registry
│   ├── core/
│   │   ├── exceptions.py              ← Application exception hierarchy
│   │   └── exception_handlers.py     ← FastAPI → consistent JSON error responses
│   ├── models/
│   │   ├── stock.py                   ← Stock instrument master (stocks collection)
│   │   ├── historical_candle.py       ← OHLCV day-buckets (historical_candles collection)
│   │   └── market_data_sync_log.py   ← Ingestion audit log (market_data_sync_logs)
│   ├── schemas/
│   │   ├── common.py                  ← PaginatedResponse, MessageResponse
│   │   ├── stock.py                   ← StockCreate, StockResponse, StockListItem
│   │   ├── candle.py                  ← CandleDataResponse, CandleBucketResponse
│   │   └── sync.py                    ← HistoricalSyncRequest, SyncResultResponse
│   ├── repositories/
│   │   ├── base_repository.py         ← Generic async CRUD base
│   │   ├── stock_repository.py        ← Stock collection data access
│   │   ├── historical_candle_repository.py ← Candle bucket data access
│   │   └── market_data_sync_log_repository.py ← Sync log data access
│   ├── brokers/
│   │   ├── base.py                    ← Abstract broker interface
│   │   └── angelone/
│   │       ├── auth.py                ← AngelOne auth + session management
│   │       └── historical_data.py    ← Historical candle API client
│   ├── services/
│   │   ├── stock_universe_service.py  ← NIFTY50 universe management
│   │   └── historical_data_service.py ← Ingestion orchestration
│   ├── scheduler/
│   │   ├── scheduler.py               ← APScheduler setup + job registration
│   │   └── jobs/
│   │       └── market_data_jobs.py   ← EOD sync + pre-market check jobs
│   ├── routes/
│   │   ├── health.py                  ← GET /health, GET /health/ready
│   │   ├── websocket_routes.py        ← WS /ws/market/{symbol}, /ws/signals
│   │   └── v1/
│   │       ├── stocks.py              ← Stock management endpoints
│   │       └── sync.py                ← Data sync trigger + audit log endpoints
│   ├── utils/
│   │   ├── logger.py                  ← Rotating file + console logging
│   │   ├── candle_intervals.py        ← CandleInterval enum + API limits map
│   │   ├── market_time.py             ← IST timezone helpers
│   │   ├── trading_day.py             ← Trading day calendar utilities
│   │   └── batch_processor.py        ← Concurrent async batch processing
│   └── middleware/
│       └── logging_middleware.py      ← Per-request logging + X-Request-ID
├── logs/                              ← app.log, error.log (volume-mounted)
├── tests/
├── docker/
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

---

## Folder Responsibilities

| Folder | Responsibility |
|--------|---------------|
| `config/` | Single truth for all environment config — Pydantic validates types at startup |
| `database/` | Motor async client lifecycle + Beanie ODM boot with retry logic |
| `core/` | Exception hierarchy + FastAPI handlers → every error has a `status_code` and consistent JSON shape |
| `models/` | Beanie Document classes — one file = one MongoDB collection with index definitions |
| `schemas/` | Pydantic API shapes decoupled from DB models |
| `repositories/` | Async CRUD wrappers using raw MongoDB filter dicts (Beanie 2.x compatible) |
| `services/` | Business logic — orchestrates repos + broker clients, no HTTP concerns |
| `brokers/angelone/` | Angel One SmartAPI: auth session management + historical candle fetching |
| `scheduler/` | APScheduler AsyncIO — EOD sync at 15:45 IST, pre-market check at 08:30 IST |
| `routes/v1/` | HTTP REST API — stocks management + data sync trigger + audit log |
| `utils/` | Pure utilities — IST timezone, trading day calendar, batch processor, logger |

---

## Angel One Setup

### 1. Create an Angel One SmartAPI app

1. Log in at https://smartapi.angelbroking.com
2. Create a new app → copy your `API Key`
3. Enable TOTP on your trading account (Google Authenticator)
4. Copy the TOTP secret shown during setup

### 2. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and set:

```env
ANGELONE_API_KEY=your_api_key
ANGELONE_CLIENT_ID=your_client_id       # your demat account ID
ANGELONE_PASSWORD=your_login_password
ANGELONE_TOTP_SECRET=your_totp_secret   # base32 TOTP secret
```

### 3. Verify instrument tokens

The NIFTY50 instrument tokens in `app/services/stock_universe_service.py` must match
Angel One's live scrip master. Download the latest master before your first ingestion:

```bash
# Download Angel One scrip master
curl -o /tmp/scrip_master.json \
  "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# Find a symbol's token (example: RELIANCE)
python3 -c "
import json
data = json.load(open('/tmp/scrip_master.json'))
matches = [x for x in data if x.get('name')=='RELIANCE' and x.get('exch_seg')=='NSE']
print(matches[:3])
"
```

Update `_NIFTY50_STOCKS` in `app/services/stock_universe_service.py` with the verified tokens.

---

## Local Setup

```bash
cd trading-bot
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in credentials
uvicorn app.main:app --reload
```

The server starts at **http://localhost:8000**

---

## Docker Setup

```bash
cp .env.example .env
docker compose up --build        # start backend + MongoDB
docker compose logs -f backend   # tail logs
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/health/ready` | Readiness probe (checks MongoDB) |
| `GET` | `/docs` | Swagger UI (dev only) |
| `GET` | `/api/v1/stocks` | Paginated stock list |
| `GET` | `/api/v1/stocks/{symbol}` | Single stock detail |
| `GET` | `/api/v1/stocks/{symbol}/candles?from_date=&to_date=&interval=` | Historical candles |
| `POST` | `/api/v1/stocks/initialize?index=NIFTY50` | Seed stock universe |
| `POST` | `/api/v1/sync/historical-data` | Trigger historical ingestion |
| `GET` | `/api/v1/sync/logs` | Sync audit logs (paginated) |
| `GET` | `/api/v1/sync/logs/{symbol}` | Latest log for a symbol |
| `GET` | `/api/v1/sync/status` | Sync health summary |
| `WS` | `/ws/market/{symbol}` | Live market tick feed |
| `WS` | `/ws/signals` | Strategy signal stream |
| `WS` | `/ws/orders` | Order status updates |

---

## Data Ingestion — How to Run

### Step 1: Seed the NIFTY50 stock universe

```bash
curl -X POST "http://localhost:8000/api/v1/stocks/initialize?index=NIFTY50"
```

### Step 2: Trigger historical ingestion (API)

```bash
curl -X POST http://localhost:8000/api/v1/sync/historical-data \
  -H "Content-Type: application/json" \
  -d '{
    "from_date": "2024-01-01",
    "to_date": "2024-03-31",
    "interval": "FIFTEEN_MINUTE"
  }'
```

Sync specific symbols only:
```bash
curl -X POST http://localhost:8000/api/v1/sync/historical-data \
  -H "Content-Type: application/json" \
  -d '{
    "from_date": "2024-01-01",
    "to_date": "2024-01-31",
    "interval": "FIFTEEN_MINUTE",
    "symbols": ["RELIANCE", "TCS", "INFY"]
  }'
```

Force re-fetch (overwrite existing data):
```bash
curl -X POST http://localhost:8000/api/v1/sync/historical-data \
  -H "Content-Type: application/json" \
  -d '{"from_date": "2024-01-15", "to_date": "2024-01-15", "force_refetch": true}'
```

### Step 3: Verify candles were stored

```bash
# Check sync logs
curl "http://localhost:8000/api/v1/sync/logs?page=1&page_size=10"

# Query candles for a symbol
curl "http://localhost:8000/api/v1/stocks/RELIANCE/candles?from_date=2024-01-01&to_date=2024-01-31&interval=FIFTEEN_MINUTE"
```

---

## Scheduler Details

| Job | Schedule (IST) | Description |
|-----|---------------|-------------|
| `eod_candle_sync` | Mon–Fri 15:45 | Sync today's 15-min candles for all active stocks |
| `pre_market_sync_check` | Mon–Fri 08:30 | Check yesterday's data and backfill if missing |

Jobs run inside the asyncio event loop — no threads. APScheduler uses `MemoryJobStore`
(restarts clear jobs; swap to `MongoDBJobStore` for persistence).

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `TradingBot` | Application name |
| `APP_ENV` | `development` | `development` / `staging` / `production` |
| `DEBUG` | `false` | Enable debug logging |
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection URI |
| `DATABASE_NAME` | `trading_bot` | MongoDB database name |
| `JWT_SECRET` | *(required in prod)* | JWT signing secret |
| `ANGELONE_API_KEY` | `""` | Angel One SmartAPI key |
| `ANGELONE_CLIENT_ID` | `""` | Angel One client ID |
| `ANGELONE_PASSWORD` | `""` | Angel One login password |
| `ANGELONE_TOTP_SECRET` | `""` | TOTP secret for 2FA |
| `ANGELONE_BASE_URL` | `https://apiconnect.angelone.in` | SmartAPI base URL |
| `INGESTION_API_DELAY_SECONDS` | `0.5` | Delay between API calls (rate-limit guard) |
| `INGESTION_CONCURRENCY` | `3` | Max parallel symbol fetches |
| `INGESTION_DEFAULT_START_DATE` | `2020-01-01` | Default historical start date |
| `LOG_LEVEL` | `INFO` | Root log level |
| `SCHEDULER_TIMEZONE` | `Asia/Kolkata` | APScheduler timezone (IST) |

---

## Running Tests

```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

---

## Future Scalability

### Adding a New Broker
1. Create `app/brokers/zerodha/client.py`
2. Implement `BaseBroker` interface
3. Register via a factory or DI container

### Adding a New Strategy
1. Create `app/strategy/strategies/my_strategy.py`
2. Extend `BaseStrategy` (to be built in Step 2)
3. Register in `strategy/registry.py`
4. Wire scheduler job in `scheduler/jobs/strategy_jobs.py`

### Horizontal Scaling
- Replace `MemoryJobStore` → `MongoDBJobStore` for distributed scheduling
- Replace in-memory WebSocket manager → Redis Pub/Sub for multi-instance broadcast
- Add Gunicorn with multiple Uvicorn workers

### Backtesting
- `get_candles_for_strategy()` in `HistoricalDataService` already returns flat candle lists
- Strategy engine receives `list[CandleData]` — no DB dependency during backtests
- Historical probability engine will aggregate from the same `historical_candles` collection
