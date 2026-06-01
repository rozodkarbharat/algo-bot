import { apiClient, extractData } from './client'
import type { PaginatedResponse } from '@/types/api'
import type {
  LiveSignalResponse,
  IntradayMarketStateResponse,
  LiveEngineStatusResponse,
  LiveHealthResponse,
} from '@/types/signal'

export interface ListLiveSignalsParams {
  page?: number
  page_size?: number
  trading_date?: string
}

export const liveApi = {
  signals: (params: ListLiveSignalsParams = {}) =>
    apiClient
      .get<PaginatedResponse<LiveSignalResponse>>('/api/v1/live/signals', { params })
      .then(extractData),

  signalsForSymbol: (symbol: string, params: { page?: number; page_size?: number } = {}) =>
    apiClient
      .get<PaginatedResponse<LiveSignalResponse>>(`/api/v1/live/signals/${symbol}`, { params })
      .then(extractData),

  marketState: () =>
    apiClient
      .get<IntradayMarketStateResponse[]>('/api/v1/live/market-state')
      .then(extractData),

  status: () =>
    apiClient.get<LiveEngineStatusResponse>('/api/v1/live/status').then(extractData),

  health: () =>
    apiClient.get<LiveHealthResponse>('/api/v1/live/health').then(extractData),

  start: (trading_date?: string) =>
    apiClient
      .post('/api/v1/live/start', trading_date ? { trading_date } : {})
      .then(extractData),

  stop: () =>
    apiClient.post('/api/v1/live/stop').then(extractData),
}
