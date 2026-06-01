// ── Paper Trading ─────────────────────────────────────────────────────────────

export type PaperPositionSide = 'LONG' | 'SHORT'
export type PaperPositionStatus = 'OPEN' | 'CLOSED'
export type PaperTradeExitReason = 'STOP_LOSS' | 'EOD_EXIT' | 'MANUAL' | 'TARGET'

export interface PaperPositionResponse {
  id: string
  symbol: string
  side: PaperPositionSide
  status: PaperPositionStatus
  quantity: number
  entry_price: number
  stop_loss: number
  current_price: number | null
  unrealized_pnl: number | null
  realized_pnl: number | null
  entry_time: string
  exit_time: string | null
  exit_price: number | null
  exit_reason: PaperTradeExitReason | null
  trading_date: string
}

export interface PaperTradeResponse {
  id: string
  symbol: string
  side: PaperPositionSide
  quantity: number
  entry_price: number
  exit_price: number
  stop_loss: number
  realized_pnl: number
  brokerage: number
  slippage: number
  net_pnl: number
  exit_reason: PaperTradeExitReason
  entry_time: string
  exit_time: string
  trading_date: string
}

export interface PaperPnLResponse {
  trading_date: string
  open_positions: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number | null
  snapshot_time: string
}

export interface PaperAccountResponse {
  id: string
  starting_capital: number
  current_capital: number
  total_realized_pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number | null
  max_drawdown: number
  is_paused: boolean
  trading_date: string | null
  updated_at: string
}

export interface PaperResetResponse {
  message: string
  trading_date: string
}

export interface PaperPauseResponse {
  message: string
  is_paused: boolean
}
