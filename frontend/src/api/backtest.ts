import { apiClient, extractData } from './client'
import type { PaginatedResponse } from '@/types/api'
import type {
  BacktestRunResponse,
  BacktestTradeResponse,
  BacktestMetrics,
  BacktestAnalytics,
} from '@/types/backtest'

export interface RunBacktestParams {
  from_date: string
  to_date: string
  symbols?: string[]
  probability_threshold?: number
  max_orb_range_pct?: number
  max_entry_time_ist?: string
  slippage_pct?: number
  brokerage_per_side?: number
  sl_buffer_pct?: number
  capital_per_trade?: number
}

export const backtestApi = {
  run: (params: RunBacktestParams) =>
    apiClient.post<BacktestRunResponse>('/api/v1/backtest/run', params).then(extractData),

  runs: (params: { page?: number; page_size?: number } = {}) =>
    apiClient
      .get<PaginatedResponse<BacktestRunResponse>>('/api/v1/backtest/runs', { params })
      .then(extractData),

  getRun: (runId: string) =>
    apiClient.get<BacktestRunResponse>(`/api/v1/backtest/runs/${runId}`).then(extractData),

  trades: (runId: string, params: { page?: number; page_size?: number } = {}) =>
    apiClient
      .get<PaginatedResponse<BacktestTradeResponse>>(`/api/v1/backtest/trades/${runId}`, {
        params,
      })
      .then(extractData),

  metrics: (runId: string) =>
    apiClient.get<BacktestMetrics>(`/api/v1/backtest/metrics/${runId}`).then(extractData),

  analytics: (runId: string) =>
    apiClient.get<BacktestAnalytics>(`/api/v1/backtest/analytics/${runId}`).then(extractData),
}
