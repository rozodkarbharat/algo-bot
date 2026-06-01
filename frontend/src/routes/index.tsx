import { createBrowserRouter, Navigate } from 'react-router-dom'
import { AppLayout } from '@/layouts/AppLayout'
import { ProtectedRoute } from '@/components/auth/ProtectedRoute'
import { Login } from '@/pages/Login'
import { Dashboard } from '@/pages/Dashboard'
import { Shortlist } from '@/pages/Shortlist'
import { LiveSignals } from '@/pages/LiveSignals'
import { PaperTrading } from '@/pages/PaperTrading'
import { LiveTrading } from '@/pages/LiveTrading'
import { Analytics } from '@/pages/Analytics'
import { SystemMonitor } from '@/pages/SystemMonitor'
import { Settings } from '@/pages/Settings'

export const router = createBrowserRouter([
  // ── Public routes ────────────────────────────────────────────────────────
  {
    path: '/login',
    element: <Login />,
  },

  // ── Protected app shell ──────────────────────────────────────────────────
  {
    path: '/',
    element: (
      <ProtectedRoute>
        <AppLayout />
      </ProtectedRoute>
    ),
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'shortlist', element: <Shortlist /> },
      { path: 'live-signals', element: <LiveSignals /> },
      {
        path: 'paper-trading',
        element: (
          <ProtectedRoute requiredRole="trader">
            <PaperTrading />
          </ProtectedRoute>
        ),
      },
      {
        path: 'live-trading',
        element: (
          <ProtectedRoute requiredRole="trader">
            <LiveTrading />
          </ProtectedRoute>
        ),
      },
      { path: 'analytics', element: <Analytics /> },
      { path: 'system', element: <SystemMonitor /> },
      {
        path: 'settings',
        element: (
          <ProtectedRoute requiredRole="admin">
            <Settings />
          </ProtectedRoute>
        ),
      },
    ],
  },

  // ── Catch-all ────────────────────────────────────────────────────────────
  { path: '*', element: <Navigate to="/" replace /> },
])
