import { useQuery } from '@tanstack/react-query'
import { Server, Database, Wifi, Clock, AlertCircle } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Table } from '@/components/ui/Table'
import { StatusDot } from '@/components/ui/StatusDot'
import { Pagination } from '@/components/ui/Pagination'
import { useWebSocket } from '@/hooks/useWebSocket'
import { healthApi, syncApi, liveApi } from '@/api'
import { useSystemStore } from '@/store/useSystemStore'
import { fmtDateTime, fmtDate } from '@/utils/formatters'
import { useState } from 'react'
import type { SyncLogResponse } from '@/types/api'

const WS_ROOMS = [
  { room: 'signals' as const, label: 'Signals' },
  { room: 'orders' as const, label: 'Orders' },
  { room: 'live:market-state' as const, label: 'Live Engine' },
  { room: 'paper:trades' as const, label: 'Paper Trades' },
  { room: 'paper:positions' as const, label: 'Paper Positions' },
  { room: 'paper:pnl' as const, label: 'Paper PnL' },
]

function WsStatusRow({
  room,
  label,
}: {
  room: 'signals' | 'orders' | 'live:market-state' | 'paper:trades' | 'paper:positions' | 'paper:pnl'
  label: string
}) {
  const { status } = useWebSocket(room)
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
      <span className="text-xs text-gray-400">{label}</span>
      <StatusDot status={status} label={status} animate={status === 'connected'} />
    </div>
  )
}

export function SystemMonitor() {
  const [logsPage, setLogsPage] = useState(1)
  const { wsStatus } = useSystemStore()

  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ['health'],
    queryFn: healthApi.liveness,
    refetchInterval: 15_000,
  })

  const { data: readiness } = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: healthApi.readiness,
    refetchInterval: 15_000,
  })

  const { data: syncStatus } = useQuery({
    queryKey: ['sync', 'status'],
    queryFn: syncApi.status,
    refetchInterval: 30_000,
  })

  const { data: syncLogs } = useQuery({
    queryKey: ['sync', 'logs', logsPage],
    queryFn: () => syncApi.logs({ page: logsPage, page_size: 20 }),
    refetchInterval: 30_000,
  })

  const { data: liveStatus } = useQuery({
    queryKey: ['live', 'status'],
    queryFn: liveApi.status,
    refetchInterval: 10_000,
  })

  const { data: liveHealth } = useQuery({
    queryKey: ['live', 'health'],
    queryFn: liveApi.health,
    refetchInterval: 10_000,
  })

  const handleRefresh = () => { refetchHealth() }

  const syncStatusVariant = (status: string) => {
    if (status === 'SUCCESS') return 'bull'
    if (status === 'FAILED') return 'bear'
    if (status === 'RUNNING') return 'accent'
    if (status === 'PARTIAL') return 'warn'
    return 'ghost'
  }

  return (
    <div className="flex flex-col">
      <Header
        title="System Monitor"
        subtitle="Backend health, WebSocket status, ingestion logs"
        onRefresh={handleRefresh}
      />

      <div className="p-6 space-y-4">
        {/* System health row */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* Backend */}
          <Card
            title="Backend"
            headerRight={<Server className="h-4 w-4 text-gray-600" />}
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">HTTP API</span>
                <StatusDot
                  status={health ? 'online' : 'offline'}
                  label={health ? 'Online' : 'Offline'}
                  animate={!!health}
                />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">Service</span>
                <span className="text-xs font-mono text-gray-400">{health?.service ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">Last checked</span>
                <span className="text-xs font-mono text-gray-500">
                  {fmtDateTime(health?.timestamp)}
                </span>
              </div>
            </div>
          </Card>

          {/* Database */}
          <Card
            title="MongoDB"
            headerRight={<Database className="h-4 w-4 text-gray-600" />}
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">Connection</span>
                <StatusDot
                  status={readiness?.database === 'connected' ? 'online' : 'offline'}
                  label={readiness?.database ?? 'Unknown'}
                  animate={readiness?.database === 'connected'}
                />
              </div>
              {syncStatus && (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Sync: Success</span>
                    <span className="font-mono text-xs text-bull">{syncStatus.SUCCESS}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Sync: Failed</span>
                    <span className="font-mono text-xs text-bear">{syncStatus.FAILED}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">Sync: Running</span>
                    <span className={`font-mono text-xs ${syncStatus.RUNNING > 0 ? 'text-accent animate-pulse' : 'text-gray-500'}`}>
                      {syncStatus.RUNNING}
                    </span>
                  </div>
                </>
              )}
            </div>
          </Card>

          {/* Live Engine */}
          <Card
            title="Live Engine"
            headerRight={<Wifi className="h-4 w-4 text-gray-600" />}
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">Status</span>
                <StatusDot
                  status={liveStatus?.is_active ? 'online' : 'offline'}
                  label={liveStatus?.is_active ? 'Running' : 'Stopped'}
                  animate={liveStatus?.is_active}
                />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">Feed alive</span>
                <StatusDot
                  status={liveHealth?.is_alive ? 'online' : 'offline'}
                  label={liveHealth?.is_alive ? 'Yes' : 'No'}
                />
              </div>
              {liveHealth?.last_tick_age_seconds != null && (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-500">Last tick</span>
                  <span className="text-xs font-mono text-gray-400">
                    {liveHealth.last_tick_age_seconds.toFixed(0)}s ago
                  </span>
                </div>
              )}
              {liveHealth?.reconnect_count != null && liveHealth.reconnect_count > 0 && (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-500">Reconnects</span>
                  <span className={`text-xs font-mono ${liveHealth.reconnect_count > 5 ? 'text-bear' : 'text-warn'}`}>
                    {liveHealth.reconnect_count}
                  </span>
                </div>
              )}
            </div>
          </Card>
        </div>

        {/* WebSocket connections */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="WebSocket Channels" headerRight={<Wifi className="h-4 w-4 text-gray-600" />}>
            {WS_ROOMS.map((r) => (
              <WsStatusRow key={r.room} room={r.room} label={r.label} />
            ))}
          </Card>

          {/* Scheduler jobs (static from config, no API yet) */}
          <Card title="Scheduler Jobs" headerRight={<Clock className="h-4 w-4 text-gray-600" />}>
            <div className="space-y-2">
              {[
                { id: 'eod_candle_sync', schedule: 'Mon–Fri 15:45 IST', purpose: 'EOD 15-min candle sync' },
                { id: 'pre_market_sync_check', schedule: 'Mon–Fri 08:30 IST', purpose: 'Pre-market backfill check' },
                { id: 'live_market_open_init', schedule: 'Mon–Fri 09:10 IST', purpose: 'Live engine warm-up' },
                { id: 'live_signal_engine_start', schedule: 'Mon–Fri 09:15 IST', purpose: 'Signal engine start' },
                { id: 'live_signal_engine_stop', schedule: 'Mon–Fri 11:30 IST', purpose: 'Signal gen stop' },
                { id: 'live_session_cleanup', schedule: 'Mon–Fri 15:30 IST', purpose: 'Full session cleanup' },
                { id: 'paper_warmup', schedule: 'Mon–Fri 09:14 IST', purpose: 'Paper trading warmup' },
                { id: 'paper_eod_close', schedule: 'Mon–Fri 15:15 IST', purpose: 'EOD position close' },
                { id: 'paper_daily_reset', schedule: 'Mon–Fri 15:35 IST', purpose: 'Daily counters reset' },
              ].map((job) => (
                <div key={job.id} className="flex items-start justify-between border-b border-border/50 pb-2 last:border-0 last:pb-0">
                  <div>
                    <p className="text-xs font-medium text-gray-300">{job.id}</p>
                    <p className="text-[10px] text-gray-600">{job.purpose}</p>
                  </div>
                  <span className="text-[10px] font-mono text-gray-500">{job.schedule}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Sync logs */}
        <Card
          title="Data Ingestion Logs"
          subtitle="Historical candle sync audit trail"
          headerRight={
            syncStatus?.RUNNING ? (
              <Badge variant="accent" dot>Running</Badge>
            ) : syncStatus?.FAILED ? (
              <div className="flex items-center gap-1 text-xs text-bear">
                <AlertCircle className="h-3.5 w-3.5" />
                {syncStatus.FAILED} failed
              </div>
            ) : null
          }
        >
          <Table
            columns={[
              {
                key: 'symbol',
                header: 'Symbol',
                render: (row: SyncLogResponse) => (
                  <span className="font-semibold text-gray-100">{row.symbol}</span>
                ),
              },
              {
                key: 'status',
                header: 'Status',
                render: (row: SyncLogResponse) => (
                  <Badge variant={syncStatusVariant(row.status) as 'bull' | 'bear' | 'accent' | 'warn' | 'ghost'} dot={row.status === 'RUNNING'}>
                    {row.status}
                  </Badge>
                ),
              },
              {
                key: 'from',
                header: 'From',
                render: (row: SyncLogResponse) => fmtDate(row.sync_from),
              },
              {
                key: 'to',
                header: 'To',
                render: (row: SyncLogResponse) => fmtDate(row.sync_to),
              },
              {
                key: 'inserted',
                header: 'Inserted',
                align: 'right' as const,
                render: (row: SyncLogResponse) => (
                  <span className="text-bull">{row.records_inserted}</span>
                ),
              },
              {
                key: 'skipped',
                header: 'Skipped',
                align: 'right' as const,
                render: (row: SyncLogResponse) => (
                  <span className="text-gray-500">{row.records_skipped}</span>
                ),
              },
              {
                key: 'error',
                header: 'Error',
                render: (row: SyncLogResponse) => (
                  row.error_message ? (
                    <span className="text-bear text-[10px]" title={row.error_message}>
                      {row.error_message.slice(0, 30)}…
                    </span>
                  ) : (
                    <span className="text-gray-600">—</span>
                  )
                ),
              },
              {
                key: 'created',
                header: 'At',
                render: (row: SyncLogResponse) => (
                  <span className="text-gray-500">{fmtDateTime(row.created_at)}</span>
                ),
              },
            ]}
            data={syncLogs?.items ?? []}
            rowKey={(row) => row.id}
            emptyMessage="No sync logs"
          />
          {syncLogs && (
            <Pagination
              page={logsPage}
              pages={syncLogs.pages}
              total={syncLogs.total}
              pageSize={syncLogs.page_size}
              onPageChange={setLogsPage}
            />
          )}
        </Card>
      </div>
    </div>
  )
}
