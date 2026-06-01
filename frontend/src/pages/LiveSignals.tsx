import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Zap, Play, Square } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Table } from '@/components/ui/Table'
import { StatusDot } from '@/components/ui/StatusDot'
import { PageSpinner } from '@/components/ui/Spinner'
import { useWebSocketMessages } from '@/hooks/useWebSocket'
import { liveApi } from '@/api'
import { useSignalStore } from '@/store/useSignalStore'
import { fmtPrice, fmtPct, fmtTime, fmtDateTime } from '@/utils/formatters'
import type { LiveSignalResponse } from '@/types/signal'

export function LiveSignals() {
  const { liveSignals, pushSignal, setSignals, setEngineStatus } = useSignalStore()
  const [isStarting, setIsStarting] = useState(false)
  const [isStopping, setIsStopping] = useState(false)

  const { data: engineStatus, refetch: refetchStatus } = useQuery({
    queryKey: ['live', 'status'],
    queryFn: liveApi.status,
    refetchInterval: 10_000,
  })

  const {
    data: signalsData,
    isLoading,
    refetch: refetchSignals,
  } = useQuery({
    queryKey: ['live', 'signals'],
    queryFn: () => liveApi.signals({ page_size: 100 }),
    refetchInterval: 30_000,
  })

  const { data: marketState } = useQuery({
    queryKey: ['live', 'market-state'],
    queryFn: liveApi.marketState,
    refetchInterval: 15_000,
  })

  // WebSocket for real-time signal updates
  const { lastMessage, status: wsStatus } = useWebSocketMessages<LiveSignalResponse>(
    'signals',
    'live.signal',
  )

  useEffect(() => {
    if (signalsData) setSignals(signalsData.items)
  }, [signalsData, setSignals])

  useEffect(() => {
    if (engineStatus) setEngineStatus(engineStatus)
  }, [engineStatus, setEngineStatus])

  useEffect(() => {
    if (lastMessage?.data) {
      pushSignal(lastMessage.data as LiveSignalResponse)
    }
  }, [lastMessage, pushSignal])

  const handleStart = async () => {
    setIsStarting(true)
    try {
      await liveApi.start()
      await refetchStatus()
    } finally {
      setIsStarting(false)
    }
  }

  const handleStop = async () => {
    setIsStopping(true)
    try {
      await liveApi.stop()
      await refetchStatus()
    } finally {
      setIsStopping(false)
    }
  }

  if (isLoading) return <PageSpinner />

  const buySignals = liveSignals.filter((s) => s.breakout_side === 'BUY')
  const sellSignals = liveSignals.filter((s) => s.breakout_side === 'SELL')

  const signalColumns = [
    {
      key: 'symbol',
      header: 'Symbol',
      render: (row: LiveSignalResponse) => (
        <span className="font-semibold text-gray-100">{row.symbol}</span>
      ),
    },
    {
      key: 'side',
      header: 'Side',
      render: (row: LiveSignalResponse) => (
        <Badge variant={row.breakout_side === 'BUY' ? 'bull' : 'bear'}>
          {row.breakout_side === 'BUY' ? '▲ BUY' : '▼ SELL'}
        </Badge>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row: LiveSignalResponse) => (
        <Badge
          variant={
            row.status === 'ACTIVE'
              ? 'accent'
              : row.status === 'TRIGGERED'
                ? 'bull'
                : row.status === 'EXPIRED'
                  ? 'ghost'
                  : 'ghost'
          }
          dot={row.status === 'ACTIVE'}
        >
          {row.status}
        </Badge>
      ),
    },
    {
      key: 'breakout_time',
      header: 'Breakout',
      render: (row: LiveSignalResponse) => fmtTime(row.breakout_time),
    },
    {
      key: 'entry',
      header: 'Entry',
      align: 'right' as const,
      render: (row: LiveSignalResponse) => (
        <span className={row.breakout_side === 'BUY' ? 'text-bull' : 'text-bear'}>
          {fmtPrice(row.entry_price)}
        </span>
      ),
    },
    {
      key: 'sl',
      header: 'Stop Loss',
      align: 'right' as const,
      render: (row: LiveSignalResponse) => (
        <span className="text-bear">{fmtPrice(row.stop_loss)}</span>
      ),
    },
    {
      key: 'prob',
      header: 'Prob',
      align: 'right' as const,
      render: (row: LiveSignalResponse) => (
        <span className={row.probability_score != null && row.probability_score >= 0.65 ? 'text-bull' : 'text-gray-400'}>
          {fmtPct(row.probability_score)}
        </span>
      ),
    },
    {
      key: 'orb',
      header: 'ORB Range',
      align: 'right' as const,
      render: (row: LiveSignalResponse) => (
        <span className="text-gray-400">
          {fmtPrice(row.orb_low)} – {fmtPrice(row.orb_high)}
        </span>
      ),
    },
  ]

  return (
    <div className="flex flex-col">
      <Header
        title="Live Signals"
        subtitle="Real-time ORB breakout signals via WebSocket"
        onRefresh={() => { refetchSignals(); refetchStatus() }}
        actions={
          <div className="flex items-center gap-2">
            <StatusDot
              status={wsStatus}
              label={wsStatus === 'connected' ? 'WS live' : wsStatus}
              animate={wsStatus === 'connected'}
            />
            {engineStatus?.is_active ? (
              <Button
                variant="danger"
                size="sm"
                loading={isStopping}
                icon={<Square className="h-3.5 w-3.5" />}
                onClick={handleStop}
              >
                Stop Engine
              </Button>
            ) : (
              <Button
                variant="bull"
                size="sm"
                loading={isStarting}
                icon={<Play className="h-3.5 w-3.5" />}
                onClick={handleStart}
              >
                Start Engine
              </Button>
            )}
          </div>
        }
      />

      <div className="p-6 space-y-4">
        {/* Engine status bar */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <div className="rounded-lg border border-border bg-surface p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Engine</p>
            <p className={`mt-1 text-lg font-mono font-bold ${engineStatus?.is_active ? 'text-bull' : 'text-gray-500'}`}>
              {engineStatus?.is_active ? 'ACTIVE' : 'STOPPED'}
            </p>
          </div>
          <div className="rounded-lg border border-border bg-surface p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Candidates</p>
            <p className="mt-1 text-lg font-mono font-bold text-accent">
              {engineStatus?.candidates_loaded ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-border bg-surface p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Signals Today</p>
            <p className="mt-1 text-lg font-mono font-bold text-gray-100">
              {engineStatus?.signals_emitted ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-border bg-surface p-3">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Started At</p>
            <p className="mt-1 text-sm font-mono text-gray-400">
              {fmtTime(engineStatus?.started_at)}
            </p>
          </div>
        </div>

        {/* BUY / SELL signal tabs */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* BUY Signals */}
          <Card
            title={`BUY Signals (${buySignals.length})`}
            headerRight={<Badge variant="bull" dot>{buySignals.filter((s) => s.status === 'ACTIVE').length} Active</Badge>}
          >
            <Table
              columns={signalColumns.filter(c => !['side'].includes(c.key))}
              data={buySignals.slice(0, 20)}
              rowKey={(row) => row.id}
              emptyMessage="No BUY signals"
            />
          </Card>

          {/* SELL Signals */}
          <Card
            title={`SELL Signals (${sellSignals.length})`}
            headerRight={<Badge variant="bear" dot>{sellSignals.filter((s) => s.status === 'ACTIVE').length} Active</Badge>}
          >
            <Table
              columns={signalColumns.filter(c => !['side'].includes(c.key))}
              data={sellSignals.slice(0, 20)}
              rowKey={(row) => row.id}
              emptyMessage="No SELL signals"
            />
          </Card>
        </div>

        {/* Intraday market state */}
        {marketState && marketState.items.length > 0 && (
          <Card title="Intraday Market State" subtitle="Per-symbol engine tracking">
            <Table
              columns={[
                {
                  key: 'symbol',
                  header: 'Symbol',
                  render: (row) => (
                    <span className="font-semibold text-gray-100">{row.symbol}</span>
                  ),
                },
                {
                  key: 'direction',
                  header: 'Direction',
                  render: (row) => (
                    row.direction ? (
                      <Badge variant={row.direction === 'BULLISH' ? 'bull' : row.direction === 'BEARISH' ? 'bear' : 'ghost'}>
                        {row.direction}
                      </Badge>
                    ) : <span className="text-gray-600">—</span>
                  ),
                },
                {
                  key: 'orb',
                  header: 'ORB Range',
                  render: (row) => (
                    row.orb_high != null ? (
                      <span className="text-gray-400">
                        {fmtPrice(row.orb_low)} – {fmtPrice(row.orb_high)}
                      </span>
                    ) : <span className="text-gray-600">—</span>
                  ),
                },
                {
                  key: 'range_pct',
                  header: 'Range %',
                  align: 'right' as const,
                  render: (row) => (
                    row.orb_range_pct != null ? (
                      <span className={row.orb_range_pct >= 1 ? 'text-bull' : 'text-warn'}>
                        {row.orb_range_pct.toFixed(2)}%
                      </span>
                    ) : '—'
                  ),
                },
                {
                  key: 'breakout',
                  header: 'Breakout',
                  render: (row) => (
                    <Badge variant={row.breakout_detected ? 'bull' : 'ghost'} dot={row.breakout_detected}>
                      {row.breakout_detected ? 'Detected' : 'Watching'}
                    </Badge>
                  ),
                },
                {
                  key: 'locked',
                  header: 'Trade Lock',
                  render: (row) => (
                    row.trade_locked ? (
                      <Badge variant="warn">Locked</Badge>
                    ) : (
                      <Badge variant="ghost">Open</Badge>
                    )
                  ),
                },
                {
                  key: 'last_candle',
                  header: 'Last Candle',
                  render: (row) => fmtTime(row.last_candle_time),
                },
              ]}
              data={marketState.items}
              rowKey={(row) => row.symbol}
            />
          </Card>
        )}

        {/* All signals */}
        <Card
          title={`All Signals (${liveSignals.length})`}
          subtitle="Real-time feed — newest first"
          headerRight={
            <div className="flex items-center gap-2">
              <Zap className="h-3.5 w-3.5 text-accent" />
              <span className="text-xs text-gray-500">
                {wsStatus === 'connected' ? 'Live' : 'Polling'}
              </span>
            </div>
          }
        >
          <Table
            columns={signalColumns}
            data={liveSignals.slice(0, 50)}
            rowKey={(row) => row.id}
            emptyMessage="No signals yet"
          />
          {liveSignals.length === 0 && (
            <p className="mt-2 text-center text-xs text-gray-600">
              Start the engine and wait for ORB breakouts to appear here
            </p>
          )}
        </Card>
      </div>
    </div>
  )
}
