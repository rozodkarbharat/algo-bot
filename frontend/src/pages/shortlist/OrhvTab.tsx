import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Search, SlidersHorizontal } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Table } from '@/components/ui/Table'
import { PageSpinner } from '@/components/ui/Spinner'
import { orhvApi } from '@/api'
import { fmtPct, fmtPrice, fmtDateTime } from '@/utils/formatters'
import type { ORHVShortlistEntry } from '@/types/orhv'
import type { AxiosError } from 'axios'

type SortKey = 'win_rate' | 'symbol' | 'orb_range_pct'
type SortDir = 'asc' | 'desc'

interface Props {
  onToast: (variant: 'success' | 'error', message: string) => void
}

export function OrhvTab({ onToast }: Props) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [view, setView] = useState<'all' | 'candidates' | 'tradable'>('all')
  const [sortKey, setSortKey] = useState<SortKey>('win_rate')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [threshold, setThreshold] = useState<number>(0.7)
  const [selectedDate, setSelectedDate] = useState('')

  const {
    data: shortlist,
    isLoading,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['orhv', selectedDate || 'today', threshold],
    queryFn: () =>
      selectedDate ? orhvApi.forDate(selectedDate, threshold) : orhvApi.today(threshold),
    refetchInterval: selectedDate ? false : 60_000,
  })

  const { data: runStatus } = useQuery({
    queryKey: ['orhv', 'status'],
    queryFn: orhvApi.status,
    refetchInterval: 5_000,
  })

  const prevRunning = useRef(false)

  useEffect(() => {
    const running = runStatus?.running ?? false
    if (prevRunning.current && !running && runStatus?.last_status === 'success') {
      onToast(
        'success',
        `ORHV run finished — ${runStatus.last_total_shortlisted} tradable of ${runStatus.last_total_checked} Phase 1 candidates` +
          (runStatus.last_duration_seconds != null
            ? ` (${runStatus.last_duration_seconds.toFixed(1)}s)`
            : ''),
      )
      queryClient.invalidateQueries({ queryKey: ['orhv', 'today'] })
    }
    if (prevRunning.current && !running && runStatus?.last_status === 'error') {
      onToast('error', runStatus.last_error || 'ORHV run failed.')
      queryClient.invalidateQueries({ queryKey: ['orhv', 'today'] })
    }
    prevRunning.current = running
  }, [runStatus, onToast, queryClient])

  const runMutation = useMutation({
    mutationFn: () => orhvApi.run({ win_rate_threshold: threshold, full_pipeline: true }),
    onSuccess: (data) => {
      if (data.status === 'accepted') {
        onToast(
          'success',
          'ORHV pipeline started — syncing candles and running detection. This may take several minutes for 500 stocks.',
        )
      } else {
        onToast(
          'success',
          `ORHV run finished — ${data.total_shortlisted} tradable of ${data.total_checked} candidates (${data.duration_seconds.toFixed(2)}s)`,
        )
        queryClient.invalidateQueries({ queryKey: ['orhv', 'today'] })
      }
      queryClient.invalidateQueries({ queryKey: ['orhv', 'status'] })
    },
    onError: (err: AxiosError<{ message?: string }>) => {
      const apiMsg = err.response?.data?.message
      const status = err.response?.status
      onToast(
        'error',
        status === 409
          ? apiMsg || 'An ORHV run is already in progress.'
          : apiMsg || err.message || 'ORHV run failed.',
      )
      queryClient.invalidateQueries({ queryKey: ['orhv', 'status'] })
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

  const allEntries: ORHVShortlistEntry[] = shortlist?.entries ?? []
  const filtered = allEntries
    .filter((e) => {
      if (search && !e.symbol.toLowerCase().includes(search.toLowerCase())) return false
      if (view === 'candidates' && !e.is_candidate) return false
      if (view === 'tradable' && !e.tradable) return false
      return true
    })
    .sort((a, b) => {
      let cmp = 0
      if (sortKey === 'win_rate') cmp = a.win_rate - b.win_rate
      else if (sortKey === 'symbol') cmp = a.symbol.localeCompare(b.symbol)
      else if (sortKey === 'orb_range_pct') cmp = a.orb_range_pct - b.orb_range_pct
      return sortDir === 'desc' ? -cmp : cmp
    })

  const emptyMessage =
    allEntries.length === 0
      ? shortlist
        ? isRunning
          ? 'ORHV pipeline running — table will populate when detection finishes.'
          : `No Phase 1 results for setup date ${shortlist.candidate_date}. Click Run ORHV after market close (15:30 IST).`
        : 'No ORHV shortlist data'
      : view === 'candidates'
        ? 'No Phase 1 candidates match current filters'
        : view === 'tradable'
          ? 'No tradable symbols match current filters'
          : 'No entries match current filters'

  if (isLoading) return <PageSpinner />

  const avgWinRate =
    allEntries.length > 0
      ? allEntries.reduce((s, e) => s + e.win_rate, 0) / allEntries.length
      : 0

  return (
    <div className="flex flex-col">
      <Header
        title="ORHV"
        subtitle={
          shortlist
            ? `Trade ${shortlist.trading_date} · setup ${shortlist.candidate_date} · ${shortlist.total_tradable} tradable · ${shortlist.total_candidates} candidates · ${shortlist.total_phase1_scanned} scanned`
            : 'Opening Range Historical Validation'
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
            title="Sync Day D candles, run Phase 1 detection + Phase 2 historical validation"
          >
            {isRunning ? 'Running…' : 'Run ORHV'}
          </Button>
        }
      />

      <div className="space-y-4">
        {runStatus && (runStatus.last_finished_at || runStatus.running) && (
          <div className="rounded-lg border border-border bg-surface px-4 py-2 text-xs text-gray-400">
            {runStatus.running ? (
              <span className="text-warn">ORHV pipeline in progress…</span>
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
          <button
            type="button"
            onClick={() => setView((v) => (v === 'candidates' ? 'all' : 'candidates'))}
            title="Show only Phase 1 candidates"
            className={`rounded-lg border bg-surface px-4 py-2 text-center transition-colors hover:border-accent/60 ${
              view === 'candidates' ? 'border-accent ring-1 ring-accent' : 'border-border'
            }`}
          >
            <p className="text-xs text-gray-500 uppercase tracking-wider">Candidates</p>
            <p className="text-xl font-mono font-bold text-gray-100">
              {shortlist?.total_candidates ?? 0}
            </p>
            <p className="text-[10px] text-gray-600">
              of {shortlist?.total_phase1_scanned ?? 0} scanned
            </p>
          </button>
          <button
            type="button"
            onClick={() => setView((v) => (v === 'tradable' ? 'all' : 'tradable'))}
            title="Show only tradable symbols"
            className={`rounded-lg border bg-accent-muted px-4 py-2 text-center transition-colors hover:border-accent ${
              view === 'tradable' ? 'border-accent ring-1 ring-accent' : 'border-accent/30'
            }`}
          >
            <p className="text-xs text-accent uppercase tracking-wider">Tradable</p>
            <p className="text-xl font-mono font-bold text-accent">
              {shortlist?.total_tradable ?? 0}
            </p>
          </button>
          <div className="rounded-lg border border-border bg-surface px-4 py-2 text-center">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Avg Win Rate</p>
            <p className="text-xl font-mono font-bold text-gray-100">{fmtPct(avgWinRate)}</p>
          </div>
          <div className="rounded-lg border border-border bg-surface px-4 py-2 text-center">
            <p className="text-xs text-gray-500 uppercase tracking-wider">Threshold</p>
            <p className="text-xl font-mono font-bold text-gray-100">
              {shortlist?.threshold_win_rate_pct ?? 70}%
            </p>
          </div>
        </div>

        <p className="text-xs text-gray-500">
          Direction is chosen at breakout on the execution day (first side of ORH / ORL to break).
        </p>

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
            <Button
              size="sm"
              variant={view === 'candidates' ? 'primary' : 'ghost'}
              icon={<SlidersHorizontal className="h-3.5 w-3.5" />}
              onClick={() => setView((v) => (v === 'candidates' ? 'all' : 'candidates'))}
            >
              Candidates only
            </Button>
            <Button
              size="sm"
              variant={view === 'tradable' ? 'primary' : 'ghost'}
              icon={<SlidersHorizontal className="h-3.5 w-3.5" />}
              onClick={() => setView((v) => (v === 'tradable' ? 'all' : 'tradable'))}
            >
              Tradable only
            </Button>
            <label className="flex items-center gap-2 text-xs text-gray-500">
              Min Win Rate:
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
                key: 'tradable',
                header: 'Status',
                render: (row) => (
                  <Badge variant={row.tradable ? 'bull' : 'ghost'} dot={row.tradable}>
                    {row.tradable ? 'Tradable' : 'Skipped'}
                  </Badge>
                ),
              },
              {
                key: 'win_rate',
                header: 'Win Rate',
                align: 'right',
                render: (row) => (
                  <button
                    type="button"
                    onClick={() => toggleSort('win_rate')}
                    className={`font-mono font-bold ${row.win_rate >= 0.7 ? 'text-bull' : row.win_rate >= 0.55 ? 'text-warn' : 'text-gray-400'}`}
                  >
                    {fmtPct(row.win_rate)}
                  </button>
                ),
              },
              {
                key: 'record',
                header: 'W / L',
                align: 'right',
                render: (row) => (
                  <span className="font-mono text-gray-300">
                    {row.wins} / {row.losses}
                  </span>
                ),
              },
              {
                key: 'occ',
                header: 'Occurrences',
                align: 'right',
                render: (row) => (
                  <span className="font-mono text-gray-400">
                    {row.occurrences_used}
                    {row.occurrences_available > row.occurrences_used
                      ? ` / ${row.occurrences_available}`
                      : ''}
                  </span>
                ),
              },
              {
                key: 'orh',
                header: 'ORH (D)',
                align: 'right',
                render: (row) => fmtPrice(row.orh_d),
              },
              {
                key: 'orl',
                header: 'ORL (D)',
                align: 'right',
                render: (row) => fmtPrice(row.orl_d),
              },
              {
                key: 'range',
                header: 'Range %',
                align: 'right',
                render: (row) => (
                  <span className={row.orb_range_pct <= 1 ? 'text-bull' : 'text-warn'}>
                    {row.orb_range_pct.toFixed(2)}%
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
