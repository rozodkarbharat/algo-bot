// ── ORHV (Opening Range Historical Validation) shortlist types ───────────────

export interface ORHVShortlistEntry {
  symbol: string
  candidate_date: string
  execution_date: string
  orh_d: number
  orl_d: number
  orb_range_pct: number
  win_rate: number
  win_rate_pct: number
  wins: number
  losses: number
  occurrences_used: number
  occurrences_available: number
  is_candidate: boolean
  tradable: boolean
  reason_skipped: string | null
}

export interface ORHVShortlistResponse {
  strategy_id: string
  strategy_name: string
  trading_date: string
  candidate_date: string
  total_candidates: number
  total_phase1_scanned: number
  total_tradable: number
  threshold_win_rate_pct: number
  generated_at: string
  entries: ORHVShortlistEntry[]
}

export interface ORHVShortlistRunParams {
  target_date?: string
  full_pipeline?: boolean
  win_rate_threshold?: number
}

export interface ORHVShortlistRunResponse {
  status: 'accepted' | 'success' | string
  target_date: string
  total_checked: number
  total_shortlisted: number
  duration_seconds: number
  full_pipeline?: boolean
  data_date?: string | null
  candles_synced?: number | null
  sync_failed_symbols?: string[] | null
  candidates_found?: number | null
  validation_tradable?: number | null
}

export interface ORHVShortlistStatusResponse {
  running: boolean
  last_status: 'idle' | 'running' | 'success' | 'error'
  last_started_at: string | null
  last_finished_at: string | null
  last_target_date: string | null
  last_total_checked: number
  last_total_shortlisted: number
  last_duration_seconds: number | null
  last_error: string | null
  last_trigger: 'manual' | 'scheduler' | null
}

// ── Single-symbol tester ─────────────────────────────────────────────────────

export type ORHVSymbolRunMode = 'full' | 'phase2'

export interface ORHVSymbolRunParams {
  symbol: string
  mode?: ORHVSymbolRunMode
  target_date?: string
}

export interface ORHVSymbolRunResponse {
  symbol: string
  mode: ORHVSymbolRunMode
  candidate_date: string
  execution_date: string | null
  has_phase1_setup: boolean
  is_candidate: boolean
  phase1_reason: string | null
  validated: boolean
  occurrences_available: number
  occurrences_used: number
  wins: number
  losses: number
  win_rate: number
  win_rate_pct: number
  tradable: boolean
  reason: string | null
  orh_d: number | null
  orl_d: number | null
  candles_synced: number
  history_candle_days: number
  history_detection_days: number
  duration_seconds: number
  message: string
}
