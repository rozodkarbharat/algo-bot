import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Search, SlidersHorizontal } from 'lucide-react'
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

type SortKey = 'probability' | 'symbol' | 'first_candle_range_pct'
type SortDir = 'asc' | 'desc'
type FilterDir = 'ALL' | 'BULLISH' | 'BEARISH'

interface Props {
  onToast: (variant: 'success' | 'error', message: string) => void
}

export function OneSideOrbTab({ onToast }: Props) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterDir, setFilterDir] = useState<FilterDir>('ALL')
  const [filterTradable, setFilterTradable] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('probability')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [threshold, setThreshold] = useState<number>(0.6)
  const [selectedDate, setSelectedDate] = useState('')

  const {
    data: shortlist,
    isLoading,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['shortlist', selectedDate || 'today', threshold],
    queryFn: () =>
      selectedDate
        ? shortlistApi.forDate(selectedDate, threshold)
        : shortlistApi.today(threshold),
    refetchInterval: selectedDate ? false : 60_000,
  })

  const { data: runStatus } = useQuery({
    queryKey: ['shortlist', 'status'],
    queryFn: shortlistApi.status,
    refetchInterval: 5_000,
  })

  const runMutation = useMutation({
    mutationFn: () =>
      shortlistApi.run({ probability_threshold: threshold, full_pipeline: true }),
    onSuccess: (data) => {
      const pipelineDetail = data.full_pipeline
        ? ` · synced ${data.candles_synced ?? 0} buckets for ${data.data_date ?? '—'}`
        : ''
      onToast(
        'success',
        `One-Side ORB run finished — ${data.total_shortlisted} of ${data.total_checked} tradable (${data.duration_seconds.toFixed(2)}s)${pipelineDetail}`,
      )
      queryClient.invalidateQueries({ queryKey: ['shortlist', 'today'] })
      queryClient.invalidateQueries({ queryKey: ['shortlist', 'status'] })
    },
    onError: (err: AxiosError<{ message?: string }>) => {
      const apiMsg = err.response?.data?.message
      const status = err.response?.status
      onToast(
        'error',
        status === 409
          ? apiMsg || 'A shortlist run is already in progress.'
          : apiMsg || err.message || 'Shortlist run failed.',
      )
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

  const allEntries: ShortlistEntry[] = shortlist?.entries ?? []
  const filtered: ShortlistEntry[] = allEntries
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
      else if (sortKey === 'first_candle_range_pct')
        cmp = a.first_candle_range_pct - b.first_candle_range_pct
      return sortDir === 'desc' ? -cmp : cmp
    })

  const emptyMessage =
    allEntries.length === 0
      ? shortlist
        ? `No candidates for setup date ${shortlist.yesterday}. If the scheduled evening run was missed, click "Run Shortlist" to rebuild it for ${shortlist.trading_date}.`
        : 'No shortlist data available'
      : 'No entries match current filters'

  if (isLoading) return <PageSpinner />

  return (
    <div className="flex flex-col">
      <Header
        title="One-Side ORB"
        subtitle={
          shortlist
            ? `Trade ${shortlist.trading_date} · setup ${shortlist.yesterday} · ${shortlist.total_tradable} tradable of ${shortlist.total_candidates} candidates`
            : ''
        }
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
            title="Rebuild the current session's shortlist: sync the prior session's candles, run OSD detection, recompute stats, build shortlist. Use this if the scheduled evening run was missed."
          >
            {isRunning ? 'Running…' : 'Run Shortlist'}
          </Button>
        }
      />

      <div className="space-y-4">
        {runStatus && (runStatus.last_finished_at || runStatus.running) && (
          <div className="rounded-lg border border-border bg-surface px-4 py-2 text-xs text-gray-400">
            {runStatus.running ? (
              <span className="text-warn">One-Side ORB run in progress…</span>
            ) : (
              <>
                Last run:{' '}
                <span className="font-mono text-gray-200">
                  {fmtDateTime(runStatus.last_finished_at)}
                </span>
                {' · '}
                <span
                  className={
                    runStatus.last_status === 'success'
                      ? 'text-bull'
                      : runStatus.last_status === 'error'
                        ? 'text-bear'
                        : 'text-gray-300'
                  }
                >
                  {runStatus.last_status}
                </span>
                {runStatus.last_status === 'success' && (
                  <>
                    {' · '}
                    {runStatus.last_total_shortlisted} tradable of{' '}
                    {runStatus.last_total_checked} candidates
                    {runStatus.last_duration_seconds != null &&
                      ` · ${runStatus.last_duration_seconds.toFixed(2)}s`}
                    {runStatus.last_trigger && ` · via ${runStatus.last_trigger}`}
                  </>
                )}
                {runStatus.last_error && (
                  <span className="text-bear"> — {runStatus.last_error}</span>
                )}
              </>
            )}
          </div>
        )}

        <div className="flex flex-wrap gap-3">
          <div className="rounded-lg border border-border bg-surface px-4 py-2 text-center">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Candidates</p>
            <p className="text-xl font-mono font-bold text-gray-100">
              {shortlist?.total_candidates ?? 0}
            </p>
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
            <p className="text-xl font-mono font-bold text-accent">
              {shortlist?.total_tradable ?? 0}
            </p>
          </div>
        </div>

        <Card
          title="Filters"
          headerRight={
            <span className="text-xs text-gray-500">
              Generated: {fmtDateTime(shortlist?.generated_at)}
            </span>
          }
        >
          <div className="flex flex-wrap items-center gap-3">
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
            <label className="flex items-center gap-2 text-xs text-gray-500">
              Date:
              <input
                type="date"
                value={selectedDate}
                onChange={(e) => setSelectedDate(e.target.value)}
                className="h-8 rounded border border-border bg-bg px-2 text-xs text-gray-200 focus:border-accent focus:outline-none [color-scheme:dark]"
              />
              {selectedDate && (
                <Button size="sm" variant="ghost" onClick={() => setSelectedDate('')}>
                  Today
                </Button>
              )}
            </label>
            <div className="flex gap-1">
              {(['ALL', 'BULLISH', 'BEARISH'] as FilterDir[]).map((d) => (
                <Button
                  key={d}
                  size="sm"
                  variant={
                    filterDir === d
                      ? d === 'BULLISH'
                        ? 'bull'
                        : d === 'BEARISH'
                          ? 'bear'
                          : 'primary'
                      : 'ghost'
                  }
                  onClick={() => setFilterDir(d)}
                >
                  {d}
                </Button>
              ))}
            </div>
            <Button
              size="sm"
              variant={filterTradable ? 'primary' : 'ghost'}
              icon={<SlidersHorizontal className="h-3.5 w-3.5" />}
              onClick={() => setFilterTradable((v) => !v)}
            >
              Tradable only
            </Button>
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
                    type="button"
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
                render: (row) => <span className="text-bear">{fmtPrice(row.stop_loss)}</span>,
              },
              {
                key: 'range',
                header: 'Range %',
                align: 'right',
                render: (row) => (
                  <span
                    className={row.first_candle_range_pct >= 1.5 ? 'text-bull' : 'text-gray-400'}
                  >
                    {row.first_candle_range_pct.toFixed(2)}%
                  </span>
                ),
              },
            ]}
            data={filtered}
            rowKey={(row) => row.symbol}
            emptyMessage={emptyMessage}
          />
        </Card>
      </div>
    </div>
  )
}
