import { apiClient, extractData } from './client'
import type { HealthResponse, ReadinessResponse } from '@/types/api'

export const healthApi = {
  liveness: () =>
    apiClient.get<HealthResponse>('/health').then(extractData),

  readiness: () =>
    apiClient.get<ReadinessResponse>('/health/ready').then(extractData),
}
