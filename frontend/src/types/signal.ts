// ── Strategy / Shortlist / Live Signals ──────────────────────────────────────

export type DayDirection = 'BULLISH' | 'BEARISH' | 'CHOPPY' | 'INVALID'
export type LiveBreakoutSide = 'BUY' | 'SELL'
export type LiveSignalStatus = 'ACTIVE' | 'TRIGGERED' | 'EXPIRED' | 'CANCELLED'
export type LiveSignalType = 'ORB_BREAKOUT'

export interface ShortlistEntry {
  symbol: string
  direction: DayDirection
  orb_high: number
  orb_low: number
  entry_trigger: number
  stop_loss: number
  probability: number
  first_candle_range_pct: number
  tradable: boolean
  reason_skipped: string | null
}

export interface ShortlistResponse {
  trading_date: string
  entries: ShortlistEntry[]
  total_candidates: number
  total_tradable: number
  generated_at: string
}

export interface LiveSignalResponse {
  id: string
  symbol: string
  signal_type: LiveSignalType
  breakout_side: LiveBreakoutSide
  status: LiveSignalStatus
  trading_date: string
  breakout_time: string
  entry_price: number
  stop_loss: number
  orb_high: number
  orb_low: number
  probability_score: number | null
  created_at: string
}

export interface IntradayMarketStateResponse {
  symbol: string
  trading_date: string
  first_candle_captured: boolean
  orb_high: number | null
  orb_low: number | null
  orb_range_pct: number | null
  direction: DayDirection | null
  breakout_detected: boolean
  signal_emitted: boolean
  trade_locked: boolean
  last_candle_time: string | null
}

export interface LiveEngineStatusResponse {
  is_active: boolean
  trading_date: string | null
  candidates_loaded: number
  signals_emitted: number
  started_at: string | null
  stopped_at: string | null
}

export interface LiveHealthResponse {
  is_alive: boolean
  last_tick_age_seconds: number | null
  reconnect_count: number
  session_started_at: string | null
}
