import { apiClient, extractData } from './client'
import type { TokenResponse, RefreshTokenResponse, UserResponse } from '@/types/auth'

export interface LoginPayload {
  username: string
  password: string
}

export interface CreateUserPayload {
  username: string
  email: string
  password: string
  role: string
}

export const authApi = {
  login: (payload: LoginPayload) =>
    apiClient.post<TokenResponse>('/api/v1/auth/login', payload).then(extractData),

  logout: (refresh_token?: string) =>
    apiClient
      .post('/api/v1/auth/logout', refresh_token ? { refresh_token } : {})
      .then(extractData),

  refresh: (refresh_token: string) =>
    apiClient
      .post<RefreshTokenResponse>('/api/v1/auth/refresh', { refresh_token })
      .then(extractData),

  me: () =>
    apiClient.get<UserResponse>('/api/v1/auth/me').then(extractData),

  changePassword: (current_password: string, new_password: string) =>
    apiClient
      .post<UserResponse>('/api/v1/auth/change-password', { current_password, new_password })
      .then(extractData),

  createUser: (payload: CreateUserPayload) =>
    apiClient.post<UserResponse>('/api/v1/auth/users', payload).then(extractData),

  listUsers: () =>
    apiClient.get('/api/v1/auth/users').then(extractData),

  deactivateUser: (username: string) =>
    apiClient.delete(`/api/v1/auth/users/${username}`).then(extractData),
}
