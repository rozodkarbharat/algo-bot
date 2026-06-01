import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { UserResponse } from '@/types/auth'

const ACCESS_TOKEN_KEY = 'auth_access_token'
const REFRESH_TOKEN_KEY = 'auth_refresh_token'

interface AuthState {
  user: UserResponse | null
  isAuthenticated: boolean

  setAuth: (user: UserResponse, accessToken: string, refreshToken: string) => void
  clearAuth: () => void
  setUser: (user: UserResponse) => void
  getAccessToken: () => string | null
  getRefreshToken: () => string | null
  /** Full logout: revokes the refresh token on the server then clears local state. */
  logout: () => Promise<void>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,

      setAuth: (user, accessToken, refreshToken) => {
        localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
        localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
        set({ user, isAuthenticated: true })
      },

      clearAuth: () => {
        localStorage.removeItem(ACCESS_TOKEN_KEY)
        localStorage.removeItem(REFRESH_TOKEN_KEY)
        set({ user: null, isAuthenticated: false })
      },

      setUser: (user) => set({ user }),

      getAccessToken: () => localStorage.getItem(ACCESS_TOKEN_KEY),

      getRefreshToken: () => localStorage.getItem(REFRESH_TOKEN_KEY),

      logout: async () => {
        const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
        try {
          // Lazy import avoids circular dependency between store and api/auth
          const { authApi } = await import('@/api/auth')
          await authApi.logout(refreshToken ?? undefined)
        } catch {
          // Best-effort — clear local state regardless of server response
        } finally {
          localStorage.removeItem(ACCESS_TOKEN_KEY)
          localStorage.removeItem(REFRESH_TOKEN_KEY)
          set({ user: null, isAuthenticated: false })
        }
      },
    }),
    {
      name: 'trading-bot-auth',
      partialize: (state) => ({ user: state.user, isAuthenticated: state.isAuthenticated }),
    },
  ),
)
