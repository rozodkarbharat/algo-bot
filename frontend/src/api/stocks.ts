import { apiClient, extractData } from './client'
import type { PaginatedResponse, StockResponse, StockListItem, CandleData, MessageResponse } from '@/types/api'

export interface ListStocksParams {
  page?: number
  page_size?: number
  index?: string
  active_only?: boolean
}

export interface GetCandlesParams {
  from_date?: string
  to_date?: string
  interval?: string
  limit?: number
}

export const stocksApi = {
  list: (params: ListStocksParams = {}) =>
    apiClient
      .get<PaginatedResponse<StockListItem>>('/api/v1/stocks', { params })
      .then(extractData),

  get: (symbol: string) =>
    apiClient.get<StockResponse>(`/api/v1/stocks/${symbol}`).then(extractData),

  candles: (symbol: string, params: GetCandlesParams = {}) =>
    apiClient
      .get<CandleData[]>(`/api/v1/stocks/${symbol}/candles`, { params })
      .then(extractData),

  initialize: () =>
    apiClient.post<MessageResponse>('/api/v1/stocks/initialize').then(extractData),
}
