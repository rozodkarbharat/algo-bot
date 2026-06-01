import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, AlertTriangle, RefreshCw } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { useSettingsStore, type TradingMode } from '@/store/useSettingsStore'
import { stocksApi } from '@/api'

interface FieldProps {
  label: string
  description?: string
  children: React.ReactNode
}

function Field({ label, description, children }: FieldProps) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b border-border/50 last:border-0">
      <div className="flex-1">
        <p className="text-xs font-medium text-gray-300">{label}</p>
        {description && <p className="mt-0.5 text-[11px] text-gray-600">{description}</p>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  )
}

export function Settings() {
  const qc = useQueryClient()
  const {
    tradingMode,
    probabilityThreshold,
    maxDailyTrades,
    capitalPerTrade,
    refreshIntervalMs,
    autoRefresh,
    setTradingMode,
    setProbabilityThreshold,
    setMaxDailyTrades,
    setCapitalPerTrade,
    setRefreshIntervalMs,
    setAutoRefresh,
  } = useSettingsStore()

  const [liveModeModal, setLiveModeModal] = useState(false)
  const [pendingMode, setPendingMode] = useState<TradingMode | null>(null)

  const initUniverseMutation = useMutation({
    mutationFn: stocksApi.initialize,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['stocks'] }),
  })

  const handleModeChange = (mode: TradingMode) => {
    if (mode === 'live') {
      setPendingMode('live')
      setLiveModeModal(true)
    } else {
      setTradingMode('paper')
    }
  }

  const confirmLiveMode = () => {
    if (pendingMode) setTradingMode(pendingMode)
    setLiveModeModal(false)
    setPendingMode(null)
  }

  return (
    <div className="flex flex-col">
      <Header title="Settings" subtitle="System configuration and risk parameters" />

      <div className="p-6 space-y-4 max-w-2xl">
        {/* Trading Mode */}
        <Card title="Trading Mode">
          <Field
            label="Active Mode"
            description="Paper mode simulates trades. Live mode places real orders with Angel One."
          >
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={tradingMode === 'paper' ? 'warn' : 'ghost'}
                onClick={() => handleModeChange('paper')}
              >
                Paper
              </Button>
              <Button
                size="sm"
                variant={tradingMode === 'live' ? 'danger' : 'ghost'}
                onClick={() => handleModeChange('live')}
              >
                Live
              </Button>
            </div>
          </Field>
          <div className="mt-2">
            {tradingMode === 'paper' ? (
              <Badge variant="warn" dot>Paper mode — no real money at risk</Badge>
            ) : (
              <Badge variant="bear" dot>LIVE MODE — real money at risk</Badge>
            )}
          </div>
        </Card>

        {/* Risk Settings */}
        <Card title="Risk Parameters">
          <Field
            label="Probability Threshold"
            description="Minimum historical continuation probability to include a stock in the shortlist"
          >
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0.5}
                max={0.9}
                step={0.05}
                value={probabilityThreshold}
                onChange={(e) => setProbabilityThreshold(parseFloat(e.target.value))}
                className="w-28 accent-accent"
              />
              <span className="w-12 text-right font-mono text-sm text-gray-200">
                {(probabilityThreshold * 100).toFixed(0)}%
              </span>
            </div>
          </Field>

          <Field
            label="Max Daily Trades"
            description="Maximum number of trades to execute in a single trading session"
          >
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setMaxDailyTrades(Math.max(1, maxDailyTrades - 1))}
              >
                −
              </Button>
              <span className="w-8 text-center font-mono text-gray-200">{maxDailyTrades}</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setMaxDailyTrades(Math.min(20, maxDailyTrades + 1))}
              >
                +
              </Button>
            </div>
          </Field>

          <Field
            label="Capital Per Trade (₹)"
            description="Notional capital allocated to each individual trade"
          >
            <input
              type="number"
              value={capitalPerTrade}
              onChange={(e) => setCapitalPerTrade(Number(e.target.value))}
              step={10000}
              min={10000}
              max={500000}
              className="w-32 rounded border border-border bg-bg px-2 py-1 text-right text-xs font-mono text-gray-200 focus:border-accent focus:outline-none"
            />
          </Field>
        </Card>

        {/* Display Settings */}
        <Card title="Display">
          <Field
            label="Auto Refresh"
            description="Automatically refresh data at the configured interval"
          >
            <button
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${autoRefresh ? 'bg-accent' : 'bg-surface-100'}`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${autoRefresh ? 'translate-x-4' : 'translate-x-1'}`}
              />
            </button>
          </Field>

          <Field
            label="Refresh Interval"
            description="How often to auto-refresh data from the API"
          >
            <select
              value={refreshIntervalMs}
              onChange={(e) => setRefreshIntervalMs(Number(e.target.value))}
              disabled={!autoRefresh}
              className="rounded border border-border bg-bg px-2 py-1 text-xs text-gray-200 focus:border-accent focus:outline-none disabled:opacity-50"
            >
              <option value={10_000}>10 seconds</option>
              <option value={30_000}>30 seconds</option>
              <option value={60_000}>1 minute</option>
              <option value={300_000}>5 minutes</option>
            </select>
          </Field>
        </Card>

        {/* Data Management */}
        <Card title="Data Management">
          <Field
            label="Initialize Stock Universe"
            description="Seed the NIFTY50 stock universe in the database (idempotent — safe to run again)"
          >
            <Button
              size="sm"
              variant="secondary"
              loading={initUniverseMutation.isPending}
              icon={<RefreshCw className="h-3.5 w-3.5" />}
              onClick={() => initUniverseMutation.mutate()}
            >
              Initialize
            </Button>
          </Field>
          {initUniverseMutation.isSuccess && (
            <p className="text-xs text-bull mt-2">{initUniverseMutation.data?.message}</p>
          )}
          {initUniverseMutation.isError && (
            <p className="text-xs text-bear mt-2">Failed to initialize universe</p>
          )}
        </Card>

        {/* API Connection */}
        <Card title="API Connection">
          <Field
            label="Backend URL"
            description="FastAPI backend server address (configured via Vite proxy)"
          >
            <span className="font-mono text-xs text-gray-500">localhost:8000 (proxy)</span>
          </Field>
          <Field
            label="WebSocket URL"
            description="WebSocket server address for real-time updates"
          >
            <span className="font-mono text-xs text-gray-500">ws://localhost:8000</span>
          </Field>
        </Card>

        <div className="flex justify-end">
          <Button
            variant="primary"
            size="md"
            icon={<Save className="h-4 w-4" />}
          >
            Settings Saved Automatically
          </Button>
        </div>
      </div>

      {/* Live mode confirmation modal */}
      <Modal
        open={liveModeModal}
        onClose={() => { setLiveModeModal(false); setPendingMode(null) }}
        title="Enable Live Trading Mode"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => { setLiveModeModal(false); setPendingMode(null) }}>
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              icon={<AlertTriangle className="h-3.5 w-3.5" />}
              onClick={confirmLiveMode}
            >
              Enable Live Mode
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="rounded border border-bear/30 bg-bear-muted p-3">
            <p className="text-sm font-semibold text-bear">Real money will be at risk</p>
          </div>
          <p className="text-xs text-gray-400">
            Switching to Live Mode will enable real order placement through Angel One SmartAPI.
            All signals generated by the live engine will be submitted as real orders.
          </p>
          <p className="text-xs text-gray-500">
            Ensure your broker credentials are configured and tested before continuing.
          </p>
        </div>
      </Modal>
    </div>
  )
}
