import axios, { AxiosError, type AxiosInstance, type AxiosResponse } from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

const ACCESS_TOKEN_KEY = 'auth_access_token'
const REFRESH_TOKEN_KEY = 'auth_refresh_token'

export const apiClient: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ── Request interceptor — attach access token ─────────────────────────────────

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(ACCESS_TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── Response interceptor — auto-refresh on 401 ────────────────────────────────

let _isRefreshing = false
let _failedQueue: Array<{ resolve: (t: string) => void; reject: (e: unknown) => void }> = []

function _processQueue(error: unknown, token: string | null) {
  _failedQueue.forEach((p) => (error ? p.reject(error) : p.resolve(token!)))
  _failedQueue = []
}

function _clearAuthAndRedirect() {
  // Import lazily to avoid circular module dependency at init time
  import('@/store/useAuthStore').then(({ useAuthStore }) => {
    useAuthStore.getState().clearAuth()
  })
  window.location.href = '/login'
}

apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const original = error.config as typeof error.config & { _retry?: boolean }

    // Only attempt refresh for 401s on non-auth endpoints
    if (
      error.response?.status === 401 &&
      original &&
      !original._retry &&
      !original.url?.includes('/auth/login') &&
      !original.url?.includes('/auth/refresh')
    ) {
      if (_isRefreshing) {
        // Queue this request until the in-flight refresh completes
        return new Promise((resolve, reject) => {
          _failedQueue.push({
            resolve: (token) => {
              original.headers!.Authorization = `Bearer ${token}`
              resolve(apiClient(original))
            },
            reject,
          })
        })
      }

      original._retry = true
      _isRefreshing = true

      const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
      if (!refreshToken) {
        _isRefreshing = false
        _clearAuthAndRedirect()
        return Promise.reject(error)
      }

      try {
        const resp = await axios.post<{ access_token: string; refresh_token: string }>(
          `${BASE_URL}/api/v1/auth/refresh`,
          { refresh_token: refreshToken },
        )
        const { access_token: newAccess, refresh_token: newRefresh } = resp.data

        // Store both rotated tokens
        localStorage.setItem(ACCESS_TOKEN_KEY, newAccess)
        localStorage.setItem(REFRESH_TOKEN_KEY, newRefresh)
        apiClient.defaults.headers.common.Authorization = `Bearer ${newAccess}`

        _processQueue(null, newAccess)
        original.headers!.Authorization = `Bearer ${newAccess}`
        return apiClient(original)
      } catch (refreshError) {
        _processQueue(refreshError, null)
        _clearAuthAndRedirect()
        return Promise.reject(refreshError)
      } finally {
        _isRefreshing = false
      }
    }

    return Promise.reject(error)
  },
)

export function extractData<T>(response: AxiosResponse<T>): T {
  return response.data
}
