import { apiClient, extractData } from './client'
import type {
  ShortlistResponse,
  ShortlistRunResponse,
  ShortlistStatusResponse,
} from '@/types/signal'

export interface ShortlistRunParams {
  target_date?: string
  probability_threshold?: number
}

export const shortlistApi = {
  today: (probability_threshold?: number) =>
    apiClient
      .get<ShortlistResponse>('/api/v1/shortlist/today', {
        params: probability_threshold != null ? { probability_threshold } : {},
      })
      .then(extractData),

  forDate: (date: string, probability_threshold?: number) =>
    apiClient
      .get<ShortlistResponse>(`/api/v1/shortlist/${date}`, {
        params: probability_threshold != null ? { probability_threshold } : {},
      })
      .then(extractData),

  run: (params: ShortlistRunParams = {}) =>
    apiClient
      .post<ShortlistRunResponse>('/api/v1/shortlist/run', params)
      .then(extractData),

  status: () =>
    apiClient.get<ShortlistStatusResponse>('/api/v1/shortlist/status').then(extractData),
}
