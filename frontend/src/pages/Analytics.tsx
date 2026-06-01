import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  AreaChart,
  Area,
  Cell,
  PieChart,
  Pie,
  Legend,
} from 'recharts'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { PageSpinner } from '@/components/ui/Spinner'
import { backtestApi, researchApi } from '@/api'
import { fmtCurrency, fmtPctRaw } from '@/utils/formatters'
import type { BacktestRunResponse } from '@/types/backtest'

const CHART_COLORS = {
  bull: '#10b981',
  bear: '#ef4444',
  accent: '#3b82f6',
  warn: '#f59e0b',
  grid: '#1f2937',
  text: '#6b7280',
}

function CustomTooltip({ active, payload, label, prefix = '' }: {
  active?: boolean
  payload?: Array<{ value: number; name: string; color: string }>
  label?: string
  prefix?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded border border-border bg-surface px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 text-gray-500">{label}</p>
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.color }}>
          {p.name}: {prefix}{typeof p.value === 'number' ? p.value.toFixed(2) : p.value}
        </p>
      ))}
    </div>
  )
}

export function Analytics() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const { data: runsData, isLoading: loadingRuns } = useQuery({
    queryKey: ['backtest', 'runs'],
    queryFn: () => backtestApi.runs({ page_size: 20 }),
  })

  const completedRuns = runsData?.items.filter((r) => r.status === 'COMPLETED') ?? []
  const runId = selectedRunId ?? completedRuns[0]?.id

  const { data: metrics, isLoading: loadingMetrics } = useQuery({
    queryKey: ['backtest', 'metrics', runId],
    queryFn: () => backtestApi.metrics(runId!),
    enabled: !!runId,
  })

  const { data: analytics } = useQuery({
    queryKey: ['backtest', 'analytics', runId],
    queryFn: () => backtestApi.analytics(runId!),
    enabled: !!runId,
  })

  const { data: trades } = useQuery({
    queryKey: ['backtest', 'trades', runId],
    queryFn: () => backtestApi.trades(runId!, { page_size: 500 }),
    enabled: !!runId,
  })

  const { data: stockAnalytics } = useQuery({
    queryKey: ['research', 'stock-analytics'],
    queryFn: () => researchApi.stockAnalytics({ limit: 20, min_trades: 5 }),
  })

  if (loadingRuns) return <PageSpinner />

  if (completedRuns.length === 0) {
    return (
      <div className="flex flex-col">
        <Header title="Analytics" subtitle="Backtest & strategy performance" />
        <div className="flex flex-1 items-center justify-center p-12">
          <div className="text-center">
            <p className="text-sm text-gray-500">No completed backtest runs found.</p>
            <p className="mt-1 text-xs text-gray-600">Run a backtest first to see analytics.</p>
          </div>
        </div>
      </div>
    )
  }

  // Build cumulative PnL curve from trades
  const cumulativePnl = (() => {
    if (!trades?.items) return []
    let running = 0
    return trades.items
      .slice()
      .sort((a, b) => a.entry_time.localeCompare(b.entry_time))
      .map((t, i) => {
        running += t.pnl
        return { trade: i + 1, pnl: running, date: t.trading_date.slice(5) }
      })
  })()

  // Monthly PnL bar chart
  const monthlyPnlData = analytics?.monthly_pnl
    ? Object.entries(analytics.monthly_pnl)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([month, pnl]) => ({ month: month.slice(0, 7), pnl }))
    : []

  // Per-symbol performance
  const symbolData = analytics?.per_symbol
    ? Object.entries(analytics.per_symbol)
        .sort(([, a], [, b]) => b.total_pnl - a.total_pnl)
        .slice(0, 15)
        .map(([symbol, d]) => ({ symbol, ...d }))
    : []

  // Long vs Short pie
  const longShortData = analytics?.long_vs_short
    ? [
        { name: 'Long', value: analytics.long_vs_short.long.trades, fill: CHART_COLORS.bull },
        { name: 'Short', value: analytics.long_vs_short.short.trades, fill: CHART_COLORS.bear },
      ]
    : []

  return (
    <div className="flex flex-col">
      <Header
        title="Analytics"
        subtitle="Backtest performance & strategy metrics"
      />

      <div className="p-6 space-y-4">
        {/* Run selector */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-500">Backtest Run:</span>
          {completedRuns.map((run: BacktestRunResponse) => (
            <Button
              key={run.id}
              size="sm"
              variant={run.id === runId ? 'primary' : 'ghost'}
              onClick={() => setSelectedRunId(run.id)}
            >
              {run.config.from_date} → {run.config.to_date}
            </Button>
          ))}
        </div>

        {/* Key metrics */}
        {metrics && !loadingMetrics && (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <div className="rounded-lg border border-border bg-surface p-3">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Total PnL</p>
              <p className={`mt-1 text-xl font-mono font-bold ${metrics.total_pnl >= 0 ? 'text-bull' : 'text-bear'}`}>
                {metrics.total_pnl >= 0 ? '+' : ''}{fmtCurrency(metrics.total_pnl)}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-surface p-3">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Win Rate</p>
              <p className={`mt-1 text-xl font-mono font-bold ${metrics.win_rate >= 60 ? 'text-bull' : metrics.win_rate >= 45 ? 'text-warn' : 'text-bear'}`}>
                {metrics.win_rate.toFixed(1)}%
              </p>
              <p className="text-xs text-gray-600">{metrics.total_trades} trades</p>
            </div>
            <div className="rounded-lg border border-border bg-surface p-3">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Profit Factor</p>
              <p className={`mt-1 text-xl font-mono font-bold ${metrics.profit_factor >= 1.5 ? 'text-bull' : metrics.profit_factor >= 1 ? 'text-warn' : 'text-bear'}`}>
                {metrics.profit_factor.toFixed(2)}x
              </p>
            </div>
            <div className="rounded-lg border border-border bg-surface p-3">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Max Drawdown</p>
              <p className="mt-1 text-xl font-mono font-bold text-bear">
                {fmtCurrency(Math.abs(metrics.max_drawdown))}
              </p>
              <p className="text-xs text-gray-600">{Math.abs(metrics.max_drawdown_pct).toFixed(1)}%</p>
            </div>
          </div>
        )}

        {/* Cumulative PnL chart */}
        {cumulativePnl.length > 0 && (
          <Card title="Cumulative PnL" subtitle="Trade-by-trade equity curve">
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={cumulativePnl}>
                <defs>
                  <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={CHART_COLORS.accent} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={CHART_COLORS.accent} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                <XAxis dataKey="trade" tick={{ fill: CHART_COLORS.text, fontSize: 10 }} />
                <YAxis
                  tick={{ fill: CHART_COLORS.text, fontSize: 10 }}
                  tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}K`}
                />
                <Tooltip content={<CustomTooltip prefix="₹" />} />
                <ReferenceLine y={0} stroke={CHART_COLORS.grid} />
                <Area
                  type="monotone"
                  dataKey="pnl"
                  name="PnL"
                  stroke={CHART_COLORS.accent}
                  fill="url(#pnlGrad)"
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        )}

        {/* Monthly PnL + Long/Short side by side */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {monthlyPnlData.length > 0 && (
            <Card title="Monthly PnL">
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={monthlyPnlData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                  <XAxis dataKey="month" tick={{ fill: CHART_COLORS.text, fontSize: 9 }} />
                  <YAxis
                    tick={{ fill: CHART_COLORS.text, fontSize: 10 }}
                    tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}K`}
                  />
                  <Tooltip content={<CustomTooltip prefix="₹" />} />
                  <ReferenceLine y={0} stroke={CHART_COLORS.bear} strokeDasharray="2 2" />
                  <Bar dataKey="pnl" name="PnL" radius={[3, 3, 0, 0]}>
                    {monthlyPnlData.map((entry, i) => (
                      <Cell
                        key={i}
                        fill={entry.pnl >= 0 ? CHART_COLORS.bull : CHART_COLORS.bear}
                        fillOpacity={0.8}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Card>
          )}

          {longShortData.length > 0 && (
            <Card title="Long vs Short">
              <div className="flex items-center justify-between">
                <ResponsiveContainer width="50%" height={200}>
                  <PieChart>
                    <Pie
                      data={longShortData}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      innerRadius={55}
                      outerRadius={80}
                    >
                      {longShortData.map((entry, i) => (
                        <Cell key={i} fill={entry.fill} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex flex-col gap-3 pr-4">
                  {analytics?.long_vs_short && (
                    <>
                      <div className="text-right">
                        <p className="text-xs text-bull font-semibold">LONG</p>
                        <p className="text-sm font-mono text-gray-100">{analytics.long_vs_short.long.trades} trades</p>
                        <p className={`text-xs font-mono ${analytics.long_vs_short.long.total_pnl >= 0 ? 'text-bull' : 'text-bear'}`}>
                          {fmtCurrency(analytics.long_vs_short.long.total_pnl)}
                        </p>
                        <p className="text-xs text-gray-500">{fmtPctRaw(analytics.long_vs_short.long.win_rate)} WR</p>
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-bear font-semibold">SHORT</p>
                        <p className="text-sm font-mono text-gray-100">{analytics.long_vs_short.short.trades} trades</p>
                        <p className={`text-xs font-mono ${analytics.long_vs_short.short.total_pnl >= 0 ? 'text-bull' : 'text-bear'}`}>
                          {fmtCurrency(analytics.long_vs_short.short.total_pnl)}
                        </p>
                        <p className="text-xs text-gray-500">{fmtPctRaw(analytics.long_vs_short.short.win_rate)} WR</p>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </Card>
          )}
        </div>

        {/* Per-symbol performance */}
        {symbolData.length > 0 && (
          <Card title="Per-Symbol Performance">
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={symbolData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                <XAxis
                  type="number"
                  tick={{ fill: CHART_COLORS.text, fontSize: 10 }}
                  tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}K`}
                />
                <YAxis
                  dataKey="symbol"
                  type="category"
                  tick={{ fill: CHART_COLORS.text, fontSize: 10 }}
                  width={70}
                />
                <Tooltip content={<CustomTooltip prefix="₹" />} />
                <ReferenceLine x={0} stroke={CHART_COLORS.grid} />
                <Bar dataKey="total_pnl" name="Total PnL" radius={[0, 3, 3, 0]}>
                  {symbolData.map((entry, i) => (
                    <Cell key={i} fill={entry.total_pnl >= 0 ? CHART_COLORS.bull : CHART_COLORS.bear} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>
        )}

        {/* Stock rankings */}
        {stockAnalytics && stockAnalytics.length > 0 && (
          <Card title="Stock Tradability Rankings" subtitle="Research engine scores">
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={stockAnalytics.slice(0, 15)}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
                <XAxis dataKey="symbol" tick={{ fill: CHART_COLORS.text, fontSize: 9 }} />
                <YAxis tick={{ fill: CHART_COLORS.text, fontSize: 10 }} />
                <Tooltip />
                <Legend />
                <Bar dataKey="tradability_score" name="Score" fill={CHART_COLORS.accent} opacity={0.8} radius={[3, 3, 0, 0]} />
                <Bar dataKey="win_rate" name="Win Rate" fill={CHART_COLORS.bull} opacity={0.8} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        )}

        {/* Additional metrics */}
        {metrics && (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <div className="rounded border border-border bg-surface p-3">
              <p className="text-xs text-gray-500">Avg Win</p>
              <p className="text-base font-mono font-bold text-bull">{fmtCurrency(metrics.avg_win)}</p>
            </div>
            <div className="rounded border border-border bg-surface p-3">
              <p className="text-xs text-gray-500">Avg Loss</p>
              <p className="text-base font-mono font-bold text-bear">{fmtCurrency(Math.abs(metrics.avg_loss))}</p>
            </div>
            <div className="rounded border border-border bg-surface p-3">
              <p className="text-xs text-gray-500">Avg R-Multiple</p>
              <p className={`text-base font-mono font-bold ${metrics.avg_r_multiple >= 0 ? 'text-bull' : 'text-bear'}`}>
                {metrics.avg_r_multiple.toFixed(2)}R
              </p>
            </div>
            <div className="rounded border border-border bg-surface p-3">
              <p className="text-xs text-gray-500">Sharpe Ratio</p>
              <p className={`text-base font-mono font-bold ${(metrics.sharpe_ratio ?? 0) >= 1 ? 'text-bull' : 'text-warn'}`}>
                {metrics.sharpe_ratio?.toFixed(2) ?? '—'}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
