import { useQuery } from '@tanstack/react-query'
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Server,
  Zap,
  Briefcase,
  Target,
  DollarSign,
} from 'lucide-react'
import { Header } from '@/layouts/Header'
import { StatCard, Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { PageSpinner } from '@/components/ui/Spinner'
import { Table } from '@/components/ui/Table'
import { StatusDot } from '@/components/ui/StatusDot'
import { liveApi, paperApi, shortlistApi, syncApi } from '@/api'
import { fmtCurrency, fmtPct, fmtDateTime, pnlClass, pnlSign } from '@/utils/formatters'
import { useSettingsStore } from '@/store/useSettingsStore'

export function Dashboard() {
  const { probabilityThreshold, autoRefresh, refreshIntervalMs } = useSettingsStore()
  const interval = autoRefresh ? refreshIntervalMs : false

  const { data: engineStatus, isLoading: loadingEngine, refetch: refetchEngine } =
    useQuery({
      queryKey: ['live', 'status'],
      queryFn: liveApi.status,
      refetchInterval: interval,
    })

  const { data: paperAccount } = useQuery({
    queryKey: ['paper', 'account'],
    queryFn: paperApi.account,
    refetchInterval: interval,
  })

  const { data: paperPnl } = useQuery({
    queryKey: ['paper', 'pnl'],
    queryFn: paperApi.pnl,
    refetchInterval: interval,
  })

  const { data: shortlist } = useQuery({
    queryKey: ['shortlist', 'today', probabilityThreshold],
    queryFn: () => shortlistApi.today(probabilityThreshold),
    refetchInterval: interval,
  })

  const { data: syncStatus } = useQuery({
    queryKey: ['sync', 'status'],
    queryFn: syncApi.status,
    refetchInterval: interval,
  })

  const { data: openPositions } = useQuery({
    queryKey: ['paper', 'positions', 'open'],
    queryFn: () => paperApi.positions({ open_only: true, page_size: 20 }),
    refetchInterval: interval,
  })

  const handleRefresh = () => {
    refetchEngine()
  }

  if (loadingEngine) return <PageSpinner />

  const todaySignals = engineStatus?.signals_emitted ?? 0
  const winRate = paperAccount?.win_rate != null ? paperAccount.win_rate * 100 : null
  const totalPnl = paperPnl?.total_pnl ?? 0
  const activePositions = openPositions?.total ?? 0

  return (
    <div className="flex flex-col">
      <Header
        title="Dashboard"
        subtitle="System overview and live metrics"
        onRefresh={handleRefresh}
      />

      <div className="p-6 space-y-6">
        {/* Stat cards row 1 */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            label="Engine Status"
            value={engineStatus?.is_active ? 'ACTIVE' : 'STOPPED'}
            valueClass={engineStatus?.is_active ? 'text-bull' : 'text-gray-500'}
            icon={<Activity className="h-4 w-4" />}
            sub={
              engineStatus?.trading_date
                ? `Date: ${engineStatus.trading_date}`
                : 'Not started'
            }
          />
          <StatCard
            label="Today's Signals"
            value={todaySignals}
            valueClass="text-accent"
            icon={<Zap className="h-4 w-4" />}
            sub={`${shortlist?.total_tradable ?? 0} tradable setups`}
          />
          <StatCard
            label="Active Positions"
            value={activePositions}
            valueClass={activePositions > 0 ? 'text-warn' : 'text-gray-400'}
            icon={<Briefcase className="h-4 w-4" />}
            sub="Paper trading"
          />
          <StatCard
            label="Today's PnL"
            value={`${pnlSign(totalPnl)}${fmtCurrency(totalPnl)}`}
            valueClass={pnlClass(totalPnl)}
            icon={totalPnl >= 0 ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
            sub={`${paperPnl?.total_trades ?? 0} trades`}
          />
        </div>

        {/* Stat cards row 2 */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            label="Account Capital"
            value={fmtCurrency(paperAccount?.current_capital)}
            valueClass="text-gray-100"
            icon={<DollarSign className="h-4 w-4" />}
            sub={`Started: ${fmtCurrency(paperAccount?.starting_capital)}`}
          />
          <StatCard
            label="Win Rate"
            value={winRate != null ? `${winRate.toFixed(1)}%` : '—'}
            valueClass={
              winRate != null
                ? winRate >= 60
                  ? 'text-bull'
                  : winRate >= 45
                    ? 'text-warn'
                    : 'text-bear'
                : 'text-gray-500'
            }
            icon={<Target className="h-4 w-4" />}
            sub={`${paperAccount?.total_trades ?? 0} total trades`}
          />
          <StatCard
            label="Sync Status"
            value={syncStatus?.SUCCESS ?? 0}
            valueClass="text-bull"
            icon={<Server className="h-4 w-4" />}
            sub={`${syncStatus?.FAILED ?? 0} failed, ${syncStatus?.PENDING ?? 0} pending`}
          />
          <StatCard
            label="Shortlist Size"
            value={shortlist?.total_tradable ?? 0}
            valueClass="text-accent"
            icon={<Zap className="h-4 w-4" />}
            sub={`of ${shortlist?.total_candidates ?? 0} candidates`}
          />
        </div>

        {/* Two-column grid */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Today's shortlist */}
          <Card title="Today's Shortlist" subtitle={shortlist?.trading_date}>
            {!shortlist ? (
              <p className="text-xs text-gray-600">No shortlist data</p>
            ) : shortlist.entries.filter((e) => e.tradable).length === 0 ? (
              <p className="text-xs text-gray-600">No tradable setups today</p>
            ) : (
              <Table
                columns={[
                  {
                    key: 'symbol',
                    header: 'Symbol',
                    render: (row) => (
                      <span className="font-semibold text-gray-200">{row.symbol}</span>
                    ),
                  },
                  {
                    key: 'direction',
                    header: 'Dir',
                    render: (row) => (
                      <Badge variant={row.direction === 'BULLISH' ? 'bull' : 'bear'}>
                        {row.direction === 'BULLISH' ? '▲ LONG' : '▼ SHORT'}
                      </Badge>
                    ),
                  },
                  {
                    key: 'prob',
                    header: 'Prob',
                    align: 'right',
                    render: (row) => (
                      <span className={row.probability >= 0.65 ? 'text-bull' : 'text-warn'}>
                        {fmtPct(row.probability)}
                      </span>
                    ),
                  },
                  {
                    key: 'entry',
                    header: 'Entry',
                    align: 'right',
                    render: (row) => `₹${row.entry_trigger.toFixed(2)}`,
                  },
                  {
                    key: 'sl',
                    header: 'SL',
                    align: 'right',
                    render: (row) => `₹${row.stop_loss.toFixed(2)}`,
                  },
                ]}
                data={shortlist.entries.filter((e) => e.tradable)}
                rowKey={(row) => row.symbol}
                emptyMessage="No tradable setups"
              />
            )}
          </Card>

          {/* Open positions */}
          <Card
            title="Open Paper Positions"
            headerRight={
              paperAccount?.is_paused ? (
                <Badge variant="warn" dot>
                  Paused
                </Badge>
              ) : (
                <StatusDot status="online" animate label="Active" />
              )
            }
          >
            <Table
              columns={[
                {
                  key: 'symbol',
                  header: 'Symbol',
                  render: (row) => (
                    <span className="font-semibold text-gray-200">{row.symbol}</span>
                  ),
                },
                {
                  key: 'side',
                  header: 'Side',
                  render: (row) => (
                    <Badge variant={row.side === 'LONG' ? 'bull' : 'bear'}>{row.side}</Badge>
                  ),
                },
                {
                  key: 'entry',
                  header: 'Entry',
                  align: 'right',
                  render: (row) => `₹${row.entry_price.toFixed(2)}`,
                },
                {
                  key: 'sl',
                  header: 'SL',
                  align: 'right',
                  render: (row) => (
                    <span className="text-bear">`₹${row.stop_loss.toFixed(2)}`</span>
                  ),
                },
                {
                  key: 'pnl',
                  header: 'Unreal. PnL',
                  align: 'right',
                  render: (row) => (
                    <span className={pnlClass(row.unrealized_pnl)}>
                      {pnlSign(row.unrealized_pnl)}
                      {fmtCurrency(row.unrealized_pnl)}
                    </span>
                  ),
                },
              ]}
              data={openPositions?.items ?? []}
              rowKey={(row) => row.id}
              emptyMessage="No open positions"
            />
          </Card>
        </div>

        {/* Data sync summary */}
        <Card
          title="Data Sync Health"
          headerRight={
            <span className="text-xs text-gray-500">
              Last: {fmtDateTime(null)}
            </span>
          }
        >
          <div className="flex flex-wrap gap-4">
            {syncStatus &&
              Object.entries(syncStatus).map(([status, count]) => (
                <div key={status} className="flex flex-col items-center gap-1">
                  <span
                    className={`text-xl font-mono font-bold ${
                      status === 'SUCCESS'
                        ? 'text-bull'
                        : status === 'FAILED'
                          ? 'text-bear'
                          : status === 'RUNNING'
                            ? 'text-accent animate-pulse'
                            : 'text-gray-500'
                    }`}
                  >
                    {count}
                  </span>
                  <span className="text-xs uppercase tracking-wider text-gray-600">{status}</span>
                </div>
              ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
