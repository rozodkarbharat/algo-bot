import { apiClient, extractData } from './client'
import type {
  ORHVShortlistResponse,
  ORHVShortlistRunParams,
  ORHVShortlistRunResponse,
  ORHVShortlistStatusResponse,
  ORHVSymbolRunParams,
  ORHVSymbolRunResponse,
} from '@/types/orhv'

export const orhvApi = {
  today: (win_rate_threshold?: number) =>
    apiClient
      .get<ORHVShortlistResponse>('/api/v1/orhv/today', {
        params: win_rate_threshold != null ? { win_rate_threshold } : {},
      })
      .then(extractData),

  forDate: (date: string, win_rate_threshold?: number) =>
    apiClient
      .get<ORHVShortlistResponse>(`/api/v1/orhv/${date}`, {
        params: win_rate_threshold != null ? { win_rate_threshold } : {},
      })
      .then(extractData),

  run: (params: ORHVShortlistRunParams = {}) =>
    apiClient
      .post<ORHVShortlistRunResponse>('/api/v1/orhv/run', params, { timeout: 600_000 })
      .then(extractData),

  status: () =>
    apiClient.get<ORHVShortlistStatusResponse>('/api/v1/orhv/status').then(extractData),

  runSymbol: (params: ORHVSymbolRunParams) =>
    apiClient
      .post<ORHVSymbolRunResponse>('/api/v1/orhv/run-symbol', params, { timeout: 600_000 })
      .then(extractData),
}
