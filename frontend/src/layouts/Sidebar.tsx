import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  ListChecks,
  Zap,
  FlaskConical,
  TrendingUp,
  BarChart3,
  Monitor,
  Settings,
  Activity,
  LogOut,
  ShieldCheck,
} from 'lucide-react'
import { cn } from '@/utils/cn'
import { StatusDot } from '@/components/ui/StatusDot'
import { useSystemStore } from '@/store/useSystemStore'
import { useSettingsStore } from '@/store/useSettingsStore'
import { useAuthStore } from '@/store/useAuthStore'
import { authApi } from '@/api/auth'
import { ROLE_LABELS } from '@/types/auth'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard', end: true },
  { to: '/shortlist', icon: ListChecks, label: 'Shortlist' },
  { to: '/live-signals', icon: Zap, label: 'Live Signals' },
  { to: '/paper-trading', icon: FlaskConical, label: 'Paper Trading' },
  { to: '/live-trading', icon: TrendingUp, label: 'Live Trading' },
  { to: '/analytics', icon: BarChart3, label: 'Analytics' },
  { to: '/system', icon: Monitor, label: 'System' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export function Sidebar() {
  const { backendOnline, dbConnected } = useSystemStore()
  const { tradingMode } = useSettingsStore()
  const { user, clearAuth } = useAuthStore()
  const navigate = useNavigate()

  const handleLogout = async () => {
    try { await authApi.logout() } catch { /* ignore */ }
    clearAuth()
    navigate('/login', { replace: true })
  }

  return (
    <aside className="flex h-screen w-56 flex-col border-r border-border bg-bg-secondary">
      {/* Logo */}
      <div className="flex h-14 items-center gap-3 border-b border-border px-4">
        <Activity className="h-5 w-5 text-accent" />
        <div>
          <p className="text-sm font-bold tracking-tight text-gray-100">TradingBot</p>
          <p className="text-[10px] text-gray-500">Operations Console</p>
        </div>
      </div>

      {/* Mode badge */}
      <div className="border-b border-border px-4 py-2.5">
        <div
          className={cn(
            'inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs font-semibold uppercase tracking-wider',
            tradingMode === 'paper'
              ? 'bg-warn-muted text-warn'
              : 'bg-bear-muted text-bear',
          )}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              tradingMode === 'paper' ? 'bg-warn' : 'bg-bear animate-pulse',
            )}
          />
          {tradingMode} mode
        </div>
      </div>

      {/* Nav items */}
      <nav className="flex-1 overflow-y-auto py-2">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-4 py-2.5 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-accent-muted text-accent border-r-2 border-accent'
                  : 'text-gray-500 hover:bg-surface hover:text-gray-300',
              )
            }
          >
            <item.icon className="h-4 w-4 flex-shrink-0" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* System status footer */}
      <div className="border-t border-border px-4 py-3 space-y-1.5">
        <StatusDot
          status={backendOnline ? 'online' : 'offline'}
          label={backendOnline ? 'Backend online' : 'Backend offline'}
          animate={backendOnline}
        />
        <StatusDot
          status={dbConnected ? 'online' : 'offline'}
          label={dbConnected ? 'MongoDB connected' : 'DB disconnected'}
        />
      </div>

      {/* User info + logout */}
      {user && (
        <div className="border-t border-border px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <ShieldCheck className="h-3 w-3 flex-shrink-0 text-accent" />
                <p className="truncate text-xs font-medium text-gray-300">{user.username}</p>
              </div>
              <p className="text-[10px] text-gray-600 uppercase tracking-wider">
                {ROLE_LABELS[user.role]}
              </p>
            </div>
            <button
              onClick={handleLogout}
              className="flex-shrink-0 rounded p-1.5 text-gray-600 hover:bg-surface-50 hover:text-gray-300 transition-colors"
              title="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}
