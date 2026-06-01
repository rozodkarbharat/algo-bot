// ── Shared API contracts ──────────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface MessageResponse {
  message: string
}

export interface ErrorResponse {
  error: string
  message: string
  detail?: unknown
  status_code: number
}

// ── Health ────────────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string
  timestamp: string
  service: string
}

export interface ReadinessResponse {
  status: string
  database: string
  timestamp: string
}

// ── Stocks ───────────────────────────────────────────────────────────────────

export interface StockResponse {
  id: string
  symbol: string
  exchange: string
  instrument_token: string
  company_name: string
  indices: string[]
  sector: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface StockListItem {
  symbol: string
  exchange: string
  company_name: string
  indices: string[]
  is_active: boolean
}

export interface CandleData {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// ── Sync ─────────────────────────────────────────────────────────────────────

export type SyncStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'PARTIAL' | 'FAILED' | 'SKIPPED'

export interface SyncLogResponse {
  id: string
  symbol: string
  exchange: string
  interval: string
  sync_from: string
  sync_to: string
  sync_end: string | null
  records_inserted: number
  records_skipped: number
  status: SyncStatus
  error_message: string | null
  created_at: string
}

export interface SyncStatusResponse {
  PENDING: number
  RUNNING: number
  SUCCESS: number
  PARTIAL: number
  FAILED: number
  SKIPPED: number
}

export interface SyncResultResponse {
  symbols_processed: number
  symbols_success: number
  symbols_failed: number
  symbols_skipped: number
  total_records_inserted: number
  elapsed_seconds: number
}
