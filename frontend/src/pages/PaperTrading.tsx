import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Pause, Play, RotateCcw, AlertTriangle, X } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card, StatCard } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Table } from '@/components/ui/Table'
import { Modal } from '@/components/ui/Modal'
import { StatusDot } from '@/components/ui/StatusDot'
import { PageSpinner } from '@/components/ui/Spinner'
import { Pagination } from '@/components/ui/Pagination'
import { useWebSocket } from '@/hooks/useWebSocket'
import { paperApi } from '@/api'
import { usePaperStore } from '@/store/usePaperStore'
import {
  fmtCurrency,
  fmtPrice,
  fmtPct,
  fmtDateTime,
  fmtTime,
  pnlClass,
  pnlSign,
} from '@/utils/formatters'
import type { PaperPositionResponse, PaperTradeResponse } from '@/types/paper'

export function PaperTrading() {
  const qc = useQueryClient()
  const { account, openPositions, pnlSnapshot, setAccount, setOpenPositions, setPnlSnapshot } =
    usePaperStore()
  const [tradesPage, setTradesPage] = useState(1)
  const [hardResetModal, setHardResetModal] = useState(false)
  const [closeAllModal, setCloseAllModal] = useState(false)

  const { data: accountData, refetch: refetchAccount } = useQuery({
    queryKey: ['paper', 'account'],
    queryFn: paperApi.account,
    refetchInterval: 15_000,
  })

  const { data: positionsData, refetch: refetchPositions } = useQuery({
    queryKey: ['paper', 'positions', 'open'],
    queryFn: () => paperApi.positions({ open_only: true, page_size: 50 }),
    refetchInterval: 5_000,
  })

  const { data: pnlData, refetch: refetchPnl } = useQuery({
    queryKey: ['paper', 'pnl'],
    queryFn: paperApi.pnl,
    refetchInterval: 10_000,
  })

  const { data: tradesData } = useQuery({
    queryKey: ['paper', 'trades', tradesPage],
    queryFn: () => paperApi.trades({ page: tradesPage, page_size: 20 }),
    refetchInterval: 30_000,
  })

  // WebSocket real-time updates
  const { lastMessage: posMsg } = useWebSocket('paper:positions')
  const { lastMessage: pnlMsg } = useWebSocket('paper:pnl')
  const { lastMessage: accountMsg } = useWebSocket('paper:account')

  useEffect(() => { if (accountData) setAccount(accountData) }, [accountData, setAccount])
  useEffect(() => { if (positionsData) setOpenPositions(positionsData.items) }, [positionsData, setOpenPositions])
  useEffect(() => { if (pnlData) setPnlSnapshot(pnlData) }, [pnlData, setPnlSnapshot])

  useEffect(() => {
    if (posMsg?.data) refetchPositions()
  }, [posMsg, refetchPositions])
  useEffect(() => {
    if (pnlMsg?.data) refetchPnl()
  }, [pnlMsg, refetchPnl])
  useEffect(() => {
    if (accountMsg?.data) refetchAccount()
  }, [accountMsg, refetchAccount])

  const pauseMutation = useMutation({
    mutationFn: account?.is_paused ? paperApi.resume : paperApi.pause,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['paper'] }) },
  })

  const hardResetMutation = useMutation({
    mutationFn: paperApi.hardReset,
    onSuccess: () => { setHardResetModal(false); qc.invalidateQueries({ queryKey: ['paper'] }) },
  })

  const closeAllMutation = useMutation({
    mutationFn: paperApi.closeAll,
    onSuccess: () => { setCloseAllModal(false); qc.invalidateQueries({ queryKey: ['paper'] }) },
  })

  const handleRefresh = () => {
    refetchAccount(); refetchPositions(); refetchPnl()
  }

  if (!accountData && !account) return <PageSpinner />

  const acc = account ?? accountData
  const pnl = pnlSnapshot ?? pnlData

  const positionColumns = [
    {
      key: 'symbol',
      header: 'Symbol',
      render: (row: PaperPositionResponse) => (
        <span className="font-semibold text-gray-100">{row.symbol}</span>
      ),
    },
    {
      key: 'side',
      header: 'Side',
      render: (row: PaperPositionResponse) => (
        <Badge variant={row.side === 'LONG' ? 'bull' : 'bear'}>{row.side}</Badge>
      ),
    },
    {
      key: 'qty',
      header: 'Qty',
      align: 'right' as const,
      render: (row: PaperPositionResponse) => row.quantity,
    },
    {
      key: 'entry',
      header: 'Entry',
      align: 'right' as const,
      render: (row: PaperPositionResponse) => fmtPrice(row.entry_price),
    },
    {
      key: 'ltp',
      header: 'LTP',
      align: 'right' as const,
      render: (row: PaperPositionResponse) => (
        <span className="text-gray-300">{row.current_price ? fmtPrice(row.current_price) : '—'}</span>
      ),
    },
    {
      key: 'sl',
      header: 'SL',
      align: 'right' as const,
      render: (row: PaperPositionResponse) => (
        <span className="text-bear">{fmtPrice(row.stop_loss)}</span>
      ),
    },
    {
      key: 'pnl',
      header: 'Unreal. PnL',
      align: 'right' as const,
      render: (row: PaperPositionResponse) => (
        <span className={pnlClass(row.unrealized_pnl)}>
          {pnlSign(row.unrealized_pnl)}{fmtCurrency(row.unrealized_pnl)}
        </span>
      ),
    },
    {
      key: 'time',
      header: 'Entry Time',
      render: (row: PaperPositionResponse) => fmtTime(row.entry_time),
    },
  ]

  const tradeColumns = [
    {
      key: 'symbol',
      header: 'Symbol',
      render: (row: PaperTradeResponse) => (
        <span className="font-semibold text-gray-100">{row.symbol}</span>
      ),
    },
    {
      key: 'side',
      header: 'Side',
      render: (row: PaperTradeResponse) => (
        <Badge variant={row.side === 'LONG' ? 'bull' : 'bear'}>{row.side}</Badge>
      ),
    },
    {
      key: 'entry',
      header: 'Entry',
      align: 'right' as const,
      render: (row: PaperTradeResponse) => fmtPrice(row.entry_price),
    },
    {
      key: 'exit',
      header: 'Exit',
      align: 'right' as const,
      render: (row: PaperTradeResponse) => fmtPrice(row.exit_price),
    },
    {
      key: 'net_pnl',
      header: 'Net PnL',
      align: 'right' as const,
      render: (row: PaperTradeResponse) => (
        <span className={pnlClass(row.net_pnl)}>
          {pnlSign(row.net_pnl)}{fmtCurrency(row.net_pnl)}
        </span>
      ),
    },
    {
      key: 'exit_reason',
      header: 'Exit',
      render: (row: PaperTradeResponse) => (
        <Badge
          variant={
            row.exit_reason === 'STOP_LOSS'
              ? 'bear'
              : row.exit_reason === 'EOD_EXIT'
                ? 'warn'
                : 'ghost'
          }
        >
          {row.exit_reason}
        </Badge>
      ),
    },
    {
      key: 'time',
      header: 'Entry → Exit',
      render: (row: PaperTradeResponse) => (
        <span className="text-gray-500">
          {fmtTime(row.entry_time)} → {fmtTime(row.exit_time)}
        </span>
      ),
    },
  ]

  return (
    <div className="flex flex-col">
      <Header
        title="Paper Trading"
        subtitle="Simulated trading with real signals"
        onRefresh={handleRefresh}
        actions={
          <div className="flex gap-2">
            <Button
              variant={acc?.is_paused ? 'bull' : 'secondary'}
              size="sm"
              loading={pauseMutation.isPending}
              icon={acc?.is_paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
              onClick={() => pauseMutation.mutate()}
            >
              {acc?.is_paused ? 'Resume' : 'Pause'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={<X className="h-3.5 w-3.5" />}
              onClick={() => setCloseAllModal(true)}
            >
              Close All
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={<RotateCcw className="h-3.5 w-3.5" />}
              onClick={() => setHardResetModal(true)}
            >
              Reset
            </Button>
          </div>
        }
      />

      <div className="p-6 space-y-4">
        {/* Account stats */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard
            label="Current Capital"
            value={fmtCurrency(acc?.current_capital)}
            sub={`Started: ${fmtCurrency(acc?.starting_capital)}`}
          />
          <StatCard
            label="Total Realized PnL"
            value={`${pnlSign(acc?.total_realized_pnl)}${fmtCurrency(acc?.total_realized_pnl)}`}
            valueClass={pnlClass(acc?.total_realized_pnl)}
            sub={`${acc?.total_trades ?? 0} total trades`}
          />
          <StatCard
            label="Win Rate"
            value={
              acc?.win_rate != null
                ? `${(acc.win_rate * 100).toFixed(1)}%`
                : '—'
            }
            valueClass={
              acc?.win_rate != null
                ? acc.win_rate >= 0.6
                  ? 'text-bull'
                  : acc.win_rate >= 0.45
                    ? 'text-warn'
                    : 'text-bear'
                : 'text-gray-500'
            }
            sub={`W:${acc?.winning_trades ?? 0} L:${acc?.losing_trades ?? 0}`}
          />
          <StatCard
            label="Max Drawdown"
            value={fmtCurrency(Math.abs(acc?.max_drawdown ?? 0))}
            valueClass="text-bear"
            sub={
              acc?.is_paused ? 'PAUSED' : 'Active'
            }
          />
        </div>

        {/* Today's PnL */}
        {pnl && (
          <div className="grid grid-cols-3 gap-4 lg:grid-cols-6">
            <div className="rounded-lg border border-border bg-surface px-4 py-3">
              <p className="text-xs text-gray-500">Realized PnL</p>
              <p className={`mt-1 text-lg font-mono font-bold ${pnlClass(pnl.realized_pnl)}`}>
                {pnlSign(pnl.realized_pnl)}{fmtCurrency(pnl.realized_pnl)}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-surface px-4 py-3">
              <p className="text-xs text-gray-500">Unrealized PnL</p>
              <p className={`mt-1 text-lg font-mono font-bold ${pnlClass(pnl.unrealized_pnl)}`}>
                {pnlSign(pnl.unrealized_pnl)}{fmtCurrency(pnl.unrealized_pnl)}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-surface px-4 py-3">
              <p className="text-xs text-gray-500">Total PnL</p>
              <p className={`mt-1 text-lg font-mono font-bold ${pnlClass(pnl.total_pnl)}`}>
                {pnlSign(pnl.total_pnl)}{fmtCurrency(pnl.total_pnl)}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-surface px-4 py-3">
              <p className="text-xs text-gray-500">Trades</p>
              <p className="mt-1 text-lg font-mono font-bold text-gray-100">{pnl.total_trades}</p>
            </div>
            <div className="rounded-lg border border-border bg-surface px-4 py-3">
              <p className="text-xs text-gray-500">Win Rate</p>
              <p className={`mt-1 text-lg font-mono font-bold ${pnl.win_rate != null ? (pnl.win_rate >= 0.6 ? 'text-bull' : 'text-warn') : 'text-gray-500'}`}>
                {pnl.win_rate != null ? `${(pnl.win_rate * 100).toFixed(1)}%` : '—'}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-surface px-4 py-3">
              <p className="text-xs text-gray-500">Open</p>
              <p className="mt-1 text-lg font-mono font-bold text-accent">{pnl.open_positions}</p>
            </div>
          </div>
        )}

        {/* Open Positions */}
        <Card
          title={`Open Positions (${openPositions.length})`}
          headerRight={
            <div className="flex items-center gap-2">
              {acc?.is_paused ? (
                <Badge variant="warn" dot>Paused</Badge>
              ) : (
                <StatusDot status="online" animate label="Active" />
              )}
            </div>
          }
        >
          <Table
            columns={positionColumns}
            data={openPositions}
            rowKey={(row) => row.id}
            emptyMessage="No open positions"
          />
        </Card>

        {/* Trade History */}
        <Card
          title="Trade History"
          subtitle="Closed trades, newest first"
        >
          <Table
            columns={tradeColumns}
            data={tradesData?.items ?? []}
            rowKey={(row) => row.id}
            emptyMessage="No closed trades yet"
          />
          {tradesData && (
            <Pagination
              page={tradesPage}
              pages={tradesData.pages}
              total={tradesData.total}
              pageSize={tradesData.page_size}
              onPageChange={setTradesPage}
            />
          )}
        </Card>
      </div>

      {/* Hard Reset Modal */}
      <Modal
        open={hardResetModal}
        onClose={() => setHardResetModal(false)}
        title="Hard Reset Account"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setHardResetModal(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={hardResetMutation.isPending}
              icon={<AlertTriangle className="h-3.5 w-3.5" />}
              onClick={() => hardResetMutation.mutate()}
            >
              Confirm Reset
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm text-gray-300">
            This will wipe all paper trades and reset the account back to starting capital.
          </p>
          <p className="text-xs text-bear">This action cannot be undone.</p>
        </div>
      </Modal>

      {/* Close All Modal */}
      <Modal
        open={closeAllModal}
        onClose={() => setCloseAllModal(false)}
        title="Force Close All Positions"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCloseAllModal(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              loading={closeAllMutation.isPending}
              onClick={() => closeAllMutation.mutate()}
            >
              Close All
            </Button>
          </>
        }
      >
        <p className="text-sm text-gray-300">
          Force-close all {openPositions.length} open positions at current market prices.
        </p>
      </Modal>
    </div>
  )
}
