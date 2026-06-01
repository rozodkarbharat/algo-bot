"""
Research & analytics engine package.

Pure Python analytical modules — no database calls, no broker imports, no I/O.
Each module receives pre-fetched data structures and returns typed result objects.

Modules:
  parameter_optimizer      — sweeps BacktestConfig parameters to find the edge
  stock_analytics          — ranks symbols by profitability, reliability, expectancy
  time_analytics           — time-of-day performance by IST bucket
  market_condition_analytics — performance vs day type (gap/trend/volatile/choppy)
  failure_analytics        — SL hit patterns, fake breakouts, high-risk conditions
  report_generator         — aggregates all above into JSON-ready report structures

All engines are CPU-bound and designed to be called from a thread-pool executor
so they never block the async event loop.
"""
