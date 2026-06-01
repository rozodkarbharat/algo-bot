import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Play, Search, SlidersHorizontal, XCircle } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Table } from '@/components/ui/Table'
import { PageSpinner } from '@/components/ui/Spinner'
import { shortlistApi } from '@/api'
import { fmtPct, fmtPrice, fmtDateTime } from '@/utils/formatters'
import type { ShortlistEntry } from '@/types/signal'
import type { AxiosError } from 'axios'

type ToastVariant = 'success' | 'error'
interface Toast {
  id: number
  variant: ToastVariant
  message: string
}

type SortKey = 'probability' | 'symbol' | 'first_candle_range_pct'
type SortDir = 'asc' | 'desc'
type FilterDir = 'ALL' | 'BULLISH' | 'BEARISH'

export function Shortlist() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterDir, setFilterDir] = useState<FilterDir>('ALL')
  const [filterTradable, setFilterTradable] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('probability')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [threshold, setThreshold] = useState<number>(0.6)
  const [toast, setToast] = useState<Toast | null>(null)

  const showToast = (variant: ToastVariant, message: string) => {
    setToast({ id: Date.now(), variant, message })
  }

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4_000)
    return () => clearTimeout(t)
  }, [toast])

  const {
    data: shortlist,
    isLoading,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['shortlist', 'today', threshold],
    queryFn: () => shortlistApi.today(threshold),
    refetchInterval: 60_000,
  })

  // Poll run-manager status: enables/disables the Run button consistently
  // when the scheduler triggers a run, not just from this browser tab.
  const { data: runStatus } = useQuery({
    queryKey: ['shortlist', 'status'],
    queryFn: shortlistApi.status,
    refetchInterval: 5_000,
  })

  const runMutation = useMutation({
    mutationFn: () => shortlistApi.run({ probability_threshold: threshold }),
    onSuccess: (data) => {
      showToast(
        'success',
        `Shortlist run finished — ${data.total_shortlisted} of ${data.total_checked} stocks shortlisted (${data.duration_seconds.toFixed(2)}s)`,
      )
      queryClient.invalidateQueries({ queryKey: ['shortlist', 'today'] })
      queryClient.invalidateQueries({ queryKey: ['shortlist', 'status'] })
    },
    onError: (err: AxiosError<{ message?: string }>) => {
      const apiMsg = err.response?.data?.message
      const status = err.response?.status
      if (status === 409) {
        showToast('error', apiMsg || 'A shortlist run is already in progress.')
      } else {
        showToast('error', apiMsg || err.message || 'Shortlist run failed.')
      }
      queryClient.invalidateQueries({ queryKey: ['shortlist', 'status'] })
    },
  })

  const isRunning = runMutation.isPending || (runStatus?.running ?? false)

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const filtered: ShortlistEntry[] = (shortlist?.entries ?? [])
    .filter((e) => {
      if (search && !e.symbol.toLowerCase().includes(search.toLowerCase())) return false
      if (filterDir !== 'ALL' && e.direction !== filterDir) return false
      if (filterTradable && !e.tradable) return false
      return true
    })
    .sort((a, b) => {
      let cmp = 0
      if (sortKey === 'probability') cmp = a.probability - b.probability
      else if (sortKey === 'symbol') cmp = a.symbol.localeCompare(b.symbol)
      else if (sortKey === 'first_candle_range_pct') cmp = a.first_candle_range_pct - b.first_candle_range_pct
      return sortDir === 'desc' ? -cmp : cmp
    })

  if (isLoading) return <PageSpinner />

  return (
    <div className="flex flex-col">
      <Header
        title="Daily Shortlist"
        subtitle={shortlist ? `${shortlist.trading_date} · ${shortlist.total_tradable} tradable of ${shortlist.total_candidates} candidates` : ''}
        onRefresh={() => refetch()}
        isRefreshing={isFetching}
        actions={
          <Button
            variant="primary"
            size="sm"
            loading={isRunning}
            disabled={isRunning}
            icon={<Play className="h-3.5 w-3.5" />}
            onClick={() => runMutation.mutate()}
            title={
              runStatus?.running
                ? 'A shortlist run is already in progress'
                : 'Run the same shortlist generation the scheduler runs at 16:30 IST'
            }
          >
            {isRunning ? 'Running…' : 'Run Shortlist'}
          </Button>
        }
      />

      {/* Toast — minimal inline implementation (no toast library installed) */}
      {toast && (
        <div className="fixed right-6 top-6 z-50">
          <div
            role="status"
            className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs shadow-lg ${
              toast.variant === 'success'
                ? 'border-bull/40 bg-bull-muted text-bull'
                : 'border-bear/40 bg-bear-muted text-bear'
            }`}
          >
            {toast.variant === 'success' ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            ) : (
              <XCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            )}
            <span className="max-w-xs">{toast.message}</span>
          </div>
        </div>
      )}

      <div className="p-6 space-y-4">
        {/* Summary row */}
        <div className="flex flex-wrap gap-3">
          <div className="rounded-lg border border-border bg-surface px-4 py-2 text-center">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Total</p>
            <p className="text-xl font-mono font-bold text-gray-100">{shortlist?.total_candidates ?? 0}</p>
          </div>
          <div className="rounded-lg border border-bull/30 bg-bull-muted px-4 py-2 text-center">
            <p className="text-xs text-bull uppercase tracking-wider">Bullish</p>
            <p className="text-xl font-mono font-bold text-bull">
              {shortlist?.entries.filter((e) => e.direction === 'BULLISH').length ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-bear/30 bg-bear-muted px-4 py-2 text-center">
            <p className="text-xs text-bear uppercase tracking-wider">Bearish</p>
            <p className="text-xl font-mono font-bold text-bear">
              {shortlist?.entries.filter((e) => e.direction === 'BEARISH').length ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-accent/30 bg-accent-muted px-4 py-2 text-center">
            <p className="text-xs text-accent uppercase tracking-wider">Tradable</p>
            <p className="text-xl font-mono font-bold text-accent">{shortlist?.total_tradable ?? 0}</p>
          </div>
        </div>

        {/* Filters */}
        <Card
          title="Filters"
          headerRight={
            <span className="text-xs text-gray-500">
              Generated: {fmtDateTime(shortlist?.generated_at)}
            </span>
          }
        >
          <div className="flex flex-wrap items-center gap-3">
            {/* Search */}
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-600" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search symbol..."
                className="h-8 rounded border border-border bg-bg pl-8 pr-3 text-xs text-gray-200 placeholder:text-gray-600 focus:border-accent focus:outline-none"
              />
            </div>

            {/* Direction filter */}
            <div className="flex gap-1">
              {(['ALL', 'BULLISH', 'BEARISH'] as FilterDir[]).map((d) => (
                <Button
                  key={d}
                  size="sm"
                  variant={filterDir === d ? (d === 'BULLISH' ? 'bull' : d === 'BEARISH' ? 'bear' : 'primary') : 'ghost'}
                  onClick={() => setFilterDir(d)}
                >
                  {d}
                </Button>
              ))}
            </div>

            {/* Tradable only */}
            <Button
              size="sm"
              variant={filterTradable ? 'primary' : 'ghost'}
              icon={<SlidersHorizontal className="h-3.5 w-3.5" />}
              onClick={() => setFilterTradable((v) => !v)}
            >
              Tradable only
            </Button>

            {/* Prob threshold */}
            <label className="flex items-center gap-2 text-xs text-gray-500">
              Min Prob:
              <input
                type="range"
                min={0.5}
                max={0.9}
                step={0.05}
                value={threshold}
                onChange={(e) => setThreshold(parseFloat(e.target.value))}
                className="w-24 accent-accent"
              />
              <span className="w-10 text-right font-mono text-gray-300">
                {(threshold * 100).toFixed(0)}%
              </span>
            </label>
          </div>
        </Card>

        {/* Table */}
        <Card noPadding>
          <Table
            stickyHeader
            columns={[
              {
                key: 'symbol',
                header: 'Symbol',
                render: (row) => (
                  <div>
                    <p className="font-semibold text-gray-100">{row.symbol}</p>
                    {!row.tradable && row.reason_skipped && (
                      <p className="text-[10px] text-gray-600">{row.reason_skipped}</p>
                    )}
                  </div>
                ),
              },
              {
                key: 'direction',
                header: 'Direction',
                render: (row) => (
                  <Badge
                    variant={
                      row.direction === 'BULLISH'
                        ? 'bull'
                        : row.direction === 'BEARISH'
                          ? 'bear'
                          : 'default'
                    }
                  >
                    {row.direction}
                  </Badge>
                ),
              },
              {
                key: 'tradable',
                header: 'Status',
                render: (row) => (
                  <Badge variant={row.tradable ? 'bull' : 'ghost'} dot={row.tradable}>
                    {row.tradable ? 'Tradable' : 'Skipped'}
                  </Badge>
                ),
              },
              {
                key: 'prob',
                header: 'Probability',
                align: 'right',
                render: (row) => (
                  <button
                    onClick={() => toggleSort('probability')}
                    className={`font-mono font-bold ${row.probability >= 0.65 ? 'text-bull' : row.probability >= 0.55 ? 'text-warn' : 'text-gray-400'}`}
                  >
                    {fmtPct(row.probability)}
                  </button>
                ),
              },
              {
                key: 'orb_high',
                header: 'ORB High',
                align: 'right',
                render: (row) => fmtPrice(row.orb_high),
              },
              {
                key: 'orb_low',
                header: 'ORB Low',
                align: 'right',
                render: (row) => fmtPrice(row.orb_low),
              },
              {
                key: 'entry',
                header: 'Entry Trigger',
                align: 'right',
                render: (row) => (
                  <span className="font-semibold text-accent">{fmtPrice(row.entry_trigger)}</span>
                ),
              },
              {
                key: 'sl',
                header: 'Stop Loss',
                align: 'right',
                render: (row) => (
                  <span className="text-bear">{fmtPrice(row.stop_loss)}</span>
                ),
              },
              {
                key: 'range',
                header: 'Range %',
                align: 'right',
                render: (row) => (
                  <span className={row.first_candle_range_pct >= 1.5 ? 'text-bull' : 'text-gray-400'}>
                    {row.first_candle_range_pct.toFixed(2)}%
                  </span>
                ),
              },
            ]}
            data={filtered}
            rowKey={(row) => row.symbol}
            emptyMessage="No entries match current filters"
          />
        </Card>
      </div>
    </div>
  )
}
