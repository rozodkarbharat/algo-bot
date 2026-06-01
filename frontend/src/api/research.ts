import { apiClient, extractData } from './client'
import type { PaginatedResponse } from '@/types/api'
import type {
  ResearchRunResponse,
  OptimizationResultResponse,
  StockAnalyticsResponse,
} from '@/types/backtest'

export interface RunResearchParams {
  from_date: string
  to_date: string
  symbols?: string[]
  probability_threshold_base?: number
  max_orb_range_pct_base?: number
}

export const researchApi = {
  run: (params: RunResearchParams) =>
    apiClient.post<ResearchRunResponse>('/api/v1/research/run', params).then(extractData),

  runs: (params: { page?: number; page_size?: number } = {}) =>
    apiClient
      .get<PaginatedResponse<ResearchRunResponse>>('/api/v1/research/runs', { params })
      .then(extractData),

  getRun: (runId: string) =>
    apiClient.get<ResearchRunResponse>(`/api/v1/research/runs/${runId}`).then(extractData),

  optimizationResults: (params: { run_id: string; parameter_name?: string }) =>
    apiClient
      .get<OptimizationResultResponse[]>('/api/v1/research/optimization-results', { params })
      .then(extractData),

  stockAnalytics: (params: { metric?: string; limit?: number; min_trades?: number } = {}) =>
    apiClient
      .get<StockAnalyticsResponse[]>('/api/v1/research/stock-analytics', { params })
      .then(extractData),

  timeAnalytics: (runId: string) =>
    apiClient.get(`/api/v1/research/time-analytics/${runId}`).then(extractData),

  failureAnalysis: (runId: string) =>
    apiClient.get(`/api/v1/research/failure-analysis/${runId}`).then(extractData),

  report: (runId: string) =>
    apiClient.get(`/api/v1/research/reports/${runId}`).then(extractData),
}
