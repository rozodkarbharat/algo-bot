import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Activity, Lock, User, AlertCircle } from 'lucide-react'
import { authApi } from '@/api/auth'
import { useAuthStore } from '@/store/useAuthStore'
import { Button } from '@/components/ui/Button'

export function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const { setAuth } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()

  const from = (location.state as { from?: string })?.from ?? '/'

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) return

    setLoading(true)
    setError(null)

    try {
      const data = await authApi.login({ username: username.trim(), password })
      setAuth(data.user, data.access_token, data.refresh_token)
      navigate(from, { replace: true })
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { message?: string } } })?.response?.data?.message ??
        'Invalid username or password.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <div className="w-full max-w-sm space-y-8">
        {/* Brand */}
        <div className="flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-accent-muted">
            <Activity className="h-7 w-7 text-accent" />
          </div>
          <div className="text-center">
            <h1 className="text-xl font-bold text-gray-100">TradingBot</h1>
            <p className="text-sm text-gray-500">Operations Console</p>
          </div>
        </div>

        {/* Form */}
        <form
          onSubmit={handleSubmit}
          className="rounded-xl border border-border bg-surface p-8 shadow-2xl space-y-5"
        >
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">
            Sign in to continue
          </h2>

          {/* Error */}
          {error && (
            <div className="flex items-start gap-2 rounded border border-bear/30 bg-bear-muted px-3 py-2">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-bear" />
              <p className="text-xs text-bear">{error}</p>
            </div>
          )}

          {/* Username */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">
              Username
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                autoComplete="username"
                required
                className="w-full rounded border border-border bg-bg py-2.5 pl-9 pr-3 text-sm text-gray-200 placeholder:text-gray-600 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              />
            </div>
          </div>

          {/* Password */}
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1.5">
              Password
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-600" />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
                required
                className="w-full rounded border border-border bg-bg py-2.5 pl-9 pr-3 text-sm text-gray-200 placeholder:text-gray-600 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
              />
            </div>
          </div>

          <Button
            type="submit"
            variant="primary"
            size="lg"
            loading={loading}
            className="w-full"
          >
            Sign In
          </Button>

          <p className="text-center text-xs text-gray-600">
            Default credentials: <span className="text-gray-500">admin / change-me-on-first-login</span>
          </p>
        </form>
      </div>
    </div>
  )
}
