import { apiClient, extractData } from './client'
import type { ShortlistResponse } from '@/types/signal'

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
}
