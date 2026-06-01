// ── Backtesting & Research ────────────────────────────────────────────────────

export type BacktestRunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'

export interface BacktestConfig {
  from_date: string
  to_date: string
  symbols: string[] | null
  probability_threshold: number
  max_orb_range_pct: number
  max_entry_time_ist: string
  slippage_pct: number
  brokerage_per_side: number
  sl_buffer_pct: number
  capital_per_trade: number
}

export interface BacktestRunResponse {
  id: string
  status: BacktestRunStatus
  config: BacktestConfig
  total_trades: number | null
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  created_at: string
}

export interface BacktestTradeResponse {
  id: string
  run_id: string
  symbol: string
  direction: 'LONG' | 'SHORT'
  entry_price: number
  exit_price: number
  stop_loss: number
  quantity: number
  pnl: number
  pnl_pct: number
  exit_reason: string
  entry_time: string
  exit_time: string
  trading_date: string
  orb_range_pct: number
  probability_score: number
}

export interface BacktestMetrics {
  run_id: string
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl_per_trade: number
  avg_win: number
  avg_loss: number
  profit_factor: number
  max_drawdown: number
  max_drawdown_pct: number
  sharpe_ratio: number | null
  avg_r_multiple: number
  total_capital_deployed: number
  return_on_capital: number
}

export interface BacktestAnalytics {
  run_id: string
  per_symbol: Record<string, {
    total_trades: number
    win_rate: number
    total_pnl: number
    avg_pnl: number
  }>
  by_entry_hour: Record<string, {
    total_trades: number
    win_rate: number
    avg_pnl: number
  }>
  monthly_pnl: Record<string, number>
  long_vs_short: {
    long: { trades: number; win_rate: number; total_pnl: number }
    short: { trades: number; win_rate: number; total_pnl: number }
  }
}

// ── Research ──────────────────────────────────────────────────────────────────

export type ResearchRunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'

export interface ResearchRunResponse {
  id: string
  status: ResearchRunStatus
  config: Record<string, unknown>
  sweep_points: number | null
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  created_at: string
}

export interface OptimizationResultResponse {
  id: string
  run_id: string
  parameter_name: string
  parameter_value: number
  total_trades: number
  win_rate: number
  total_pnl: number
  sharpe_ratio: number | null
  max_drawdown: number
}

export interface StockAnalyticsResponse {
  symbol: string
  company_name: string | null
  total_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
  tradability_score: number
  bullish_win_rate: number | null
  bearish_win_rate: number | null
}
