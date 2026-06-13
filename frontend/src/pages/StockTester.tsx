import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Play, FlaskConical, Search, CheckCircle2, XCircle } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Table } from '@/components/ui/Table'
import { Modal } from '@/components/ui/Modal'
import { Pagination } from '@/components/ui/Pagination'
import { PageSpinner } from '@/components/ui/Spinner'
import { stocksApi, orhvApi } from '@/api'
import { fmtPct, fmtPrice, fmtDate } from '@/utils/formatters'
import type { StockListItem } from '@/types/api'
import type { ORHVSymbolRunMode, ORHVSymbolRunResponse } from '@/types/orhv'
import type { AxiosError } from 'axios'

const PAGE_SIZE = 20

type ToastVariant = 'success' | 'error'
interface Toast {
  id: number
  variant: ToastVariant
  message: string
}

export function StockTester() {
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [toast, setToast] = useState<Toast | null>(null)
  const [result, setResult] = useState<ORHVSymbolRunResponse | null>(null)
  const [runningKey, setRunningKey] = useState<string | null>(null)

  // Debounce the search input and reset to page 1 when the term changes.
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search.trim())
      setPage(1)
    }, 350)
    return () => clearTimeout(t)
  }, [search])

  const showToast = (variant: ToastVariant, message: string) => {
    setToast({ id: Date.now(), variant, message })
    setTimeout(() => setToast(null), 4_000)
  }

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['stocks', 'list', page, debouncedSearch],
    queryFn: () =>
      stocksApi.list({
        page,
        page_size: PAGE_SIZE,
        active_only: true,
        search: debouncedSearch || undefined,
      }),
  })

  const runMutation = useMutation({
    mutationFn: ({ symbol, mode }: { symbol: string; mode: ORHVSymbolRunMode }) =>
      orhvApi.runSymbol({ symbol, mode }),
    onSuccess: (res) => {
      setResult(res)
      showToast(res.tradable ? 'success' : 'error', res.message)
    },
    onError: (err: AxiosError<{ message?: string; detail?: string }>) => {
      const msg = err.response?.data?.message || err.response?.data?.detail || err.message
      showToast('error', msg || 'Run failed.')
    },
    onSettled: () => setRunningKey(null),
  })

  const runSymbol = (symbol: string, mode: ORHVSymbolRunMode) => {
    setRunningKey(`${symbol}:${mode}`)
    runMutation.mutate({ symbol, mode })
  }

  const stocks: StockListItem[] = data?.items ?? []

  if (isLoading) return <PageSpinner />

  return (
    <div className="flex flex-col">
      <Header
        title="Stock Tester"
        subtitle="Run the ORHV strategy on a single stock — full pipeline or Phase 2 validation"
        onRefresh={() => refetch()}
        isRefreshing={isFetching}
      />

      <div className="space-y-4 p-6">
        <Card>
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-600" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search symbol or company..."
                className="h-8 w-64 rounded border border-border bg-bg pl-8 pr-3 text-xs text-gray-200 placeholder:text-gray-600 focus:border-accent focus:outline-none"
              />
            </div>
            <p className="text-xs text-gray-500">
              <span className="text-gray-300">Run Shortlist</span> syncs + detects + validates ·{' '}
              <span className="text-gray-300">Run Phase 2</span> checks stored history only
            </p>
          </div>
        </Card>

        <Card noPadding>
          <Table
            stickyHeader
            columns={[
              {
                key: 'symbol',
                header: 'Symbol',
                render: (row: StockListItem) => (
                  <span className="font-semibold text-gray-100">{row.symbol}</span>
                ),
              },
              {
                key: 'company',
                header: 'Company',
                render: (row: StockListItem) => (
                  <span className="text-gray-400">{row.company_name}</span>
                ),
              },
              {
                key: 'indices',
                header: 'Index',
                render: (row: StockListItem) => (
                  <div className="flex flex-wrap gap-1">
                    {row.indices.slice(0, 2).map((idx) => (
                      <Badge key={idx} variant="ghost">
                        {idx}
                      </Badge>
                    ))}
                  </div>
                ),
              },
              {
                key: 'actions',
                header: 'Actions',
                align: 'right',
                render: (row: StockListItem) => (
                  <div className="flex justify-end gap-2">
                    <Button
                      size="sm"
                      variant="primary"
                      icon={<Play className="h-3 w-3" />}
                      loading={runningKey === `${row.symbol}:full`}
                      disabled={runMutation.isPending}
                      onClick={() => runSymbol(row.symbol, 'full')}
                      title="Sync candles, detect setups, and validate this stock"
                    >
                      Run Shortlist
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={<FlaskConical className="h-3 w-3" />}
                      loading={runningKey === `${row.symbol}:phase2`}
                      disabled={runMutation.isPending}
                      onClick={() => runSymbol(row.symbol, 'phase2')}
                      title="Validate against stored history (check prior performance)"
                    >
                      Run Phase 2
                    </Button>
                  </div>
                ),
              },
            ]}
            data={stocks}
            rowKey={(row: StockListItem) => row.symbol}
            emptyMessage={
              debouncedSearch ? `No stocks match "${debouncedSearch}"` : 'No stocks found'
            }
          />
          {data && (
            <div className="border-t border-border px-3">
              <Pagination
                page={data.page}
                pages={data.pages}
                total={data.total}
                pageSize={data.page_size}
                onPageChange={setPage}
              />
            </div>
          )}
        </Card>
      </div>

      <ResultModal result={result} onClose={() => setResult(null)} />

      {toast && (
        <div
          className={`fixed bottom-6 right-6 z-50 flex max-w-md items-center gap-2 rounded-lg border px-4 py-3 text-sm shadow-2xl ${
            toast.variant === 'success'
              ? 'border-bull/30 bg-bull-muted text-bull'
              : 'border-bear/30 bg-bear-muted text-bear'
          }`}
        >
          {toast.variant === 'success' ? (
            <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
          ) : (
            <XCircle className="h-4 w-4 flex-shrink-0" />
          )}
          {toast.message}
        </div>
      )}
    </div>
  )
}

function ResultModal({
  result,
  onClose,
}: {
  result: ORHVSymbolRunResponse | null
  onClose: () => void
}) {
  if (!result) return null

  const statusVariant = result.tradable
    ? 'bull'
    : result.is_candidate
      ? 'warn'
      : 'ghost'

  return (
    <Modal
      open={!!result}
      onClose={onClose}
      title={`${result.symbol} — ${result.mode === 'full' ? 'Run Shortlist' : 'Run Phase 2'}`}
      footer={
        <Button variant="secondary" size="sm" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-4">
        <div
          className={`rounded-lg border px-3 py-2 text-sm ${
            result.tradable
              ? 'border-bull/30 bg-bull-muted text-bull'
              : result.is_candidate
                ? 'border-warn/30 bg-warn-muted text-warn'
                : 'border-border bg-surface text-gray-300'
          }`}
        >
          {result.message}
        </div>

        <div className="flex flex-wrap gap-2">
          <Badge variant={result.has_phase1_setup ? 'accent' : 'ghost'} dot>
            {result.has_phase1_setup ? 'Phase 1 stored' : 'No Phase 1'}
          </Badge>
          <Badge variant={result.is_candidate ? 'accent' : 'ghost'} dot>
            {result.is_candidate ? 'Candidate' : 'Not candidate'}
          </Badge>
          <Badge variant={result.validated ? 'accent' : 'ghost'} dot>
            {result.validated ? 'Phase 2 validated' : 'No validation'}
          </Badge>
          <Badge variant={statusVariant} dot>
            {result.tradable ? 'Tradable' : 'Not tradable'}
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <Field label="Setup date (D)" value={fmtDate(result.candidate_date)} />
          <Field label="Execution date (D+1)" value={fmtDate(result.execution_date)} />
          <Field label="Win rate" value={fmtPct(result.win_rate)} />
          <Field label="W / L" value={`${result.wins} / ${result.losses}`} />
          <Field
            label="Occurrences (used / avail)"
            value={`${result.occurrences_used} / ${result.occurrences_available}`}
          />
          <Field label="ORH (D)" value={fmtPrice(result.orh_d)} />
          <Field label="ORL (D)" value={fmtPrice(result.orl_d)} />
          {result.reason && <Field label="Reason" value={result.reason} span2 />}
        </div>

        {result.mode === 'full' && (
          <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs text-gray-400">
            History guard: {result.history_candle_days} candle day(s) in window ·{' '}
            {result.history_detection_days} detected · {result.candles_synced} candle bucket(s)
            synced · {result.duration_seconds.toFixed(1)}s
          </div>
        )}
      </div>
    </Modal>
  )
}

function Field({
  label,
  value,
  span2,
}: {
  label: string
  value: string
  span2?: boolean
}) {
  return (
    <div className={span2 ? 'col-span-2' : ''}>
      <p className="text-gray-500">{label}</p>
      <p className="font-mono text-gray-200">{value}</p>
    </div>
  )
}