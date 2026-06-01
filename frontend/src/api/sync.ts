import { apiClient, extractData } from './client'
import type { PaginatedResponse, SyncLogResponse, SyncStatusResponse, SyncResultResponse, SyncStatus } from '@/types/api'

export interface TriggerSyncParams {
  from_date: string
  to_date: string
  interval?: string
  symbols?: string[]
  force_refetch?: boolean
}

export interface ListSyncLogsParams {
  page?: number
  page_size?: number
  status?: SyncStatus
}

export const syncApi = {
  trigger: (params: TriggerSyncParams) =>
    apiClient.post<SyncResultResponse>('/api/v1/sync/historical-data', params).then(extractData),

  logs: (params: ListSyncLogsParams = {}) =>
    apiClient
      .get<PaginatedResponse<SyncLogResponse>>('/api/v1/sync/logs', { params })
      .then(extractData),

  symbolLog: (symbol: string) =>
    apiClient.get<SyncLogResponse>(`/api/v1/sync/logs/${symbol}`).then(extractData),

  status: () =>
    apiClient.get<SyncStatusResponse>('/api/v1/sync/status').then(extractData),
}
