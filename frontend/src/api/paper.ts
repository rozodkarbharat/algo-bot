import { apiClient, extractData } from './client'
import type { PaginatedResponse } from '@/types/api'
import type {
  PaperAccountResponse,
  PaperPositionResponse,
  PaperTradeResponse,
  PaperPnLResponse,
  PaperResetResponse,
  PaperPauseResponse,
} from '@/types/paper'

export interface ListPaperPositionsParams {
  page?: number
  page_size?: number
  open_only?: boolean
}

export interface ListPaperTradesParams {
  page?: number
  page_size?: number
  trading_date?: string
}

export const paperApi = {
  account: () =>
    apiClient.get<PaperAccountResponse>('/api/v1/paper/account').then(extractData),

  positions: (params: ListPaperPositionsParams = {}) =>
    apiClient
      .get<PaginatedResponse<PaperPositionResponse>>('/api/v1/paper/positions', { params })
      .then(extractData),

  trades: (params: ListPaperTradesParams = {}) =>
    apiClient
      .get<PaginatedResponse<PaperTradeResponse>>('/api/v1/paper/trades', { params })
      .then(extractData),

  pnl: () =>
    apiClient.get<PaperPnLResponse>('/api/v1/paper/pnl').then(extractData),

  reset: () =>
    apiClient.post<PaperResetResponse>('/api/v1/paper/reset').then(extractData),

  hardReset: () =>
    apiClient.post<PaperResetResponse>('/api/v1/paper/hard-reset').then(extractData),

  pause: () =>
    apiClient.post<PaperPauseResponse>('/api/v1/paper/pause').then(extractData),

  resume: () =>
    apiClient.post<PaperPauseResponse>('/api/v1/paper/resume').then(extractData),

  closeAll: () =>
    apiClient.post('/api/v1/paper/close-all').then(extractData),
}
