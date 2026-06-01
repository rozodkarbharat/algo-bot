import { AlertTriangle, Shield } from 'lucide-react'
import { Header } from '@/layouts/Header'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useSettingsStore } from '@/store/useSettingsStore'

export function LiveTrading() {
  const { tradingMode } = useSettingsStore()

  if (tradingMode !== 'live') {
    return (
      <div className="flex flex-col">
        <Header title="Live Trading" subtitle="Real broker order execution" />
        <div className="flex flex-1 items-center justify-center p-12">
          <div className="max-w-sm rounded-lg border border-warn/30 bg-warn-muted p-8 text-center">
            <Shield className="mx-auto h-10 w-10 text-warn" />
            <h2 className="mt-4 text-sm font-semibold text-warn">Paper Mode Active</h2>
            <p className="mt-2 text-xs text-gray-400">
              Switch to Live Mode in Settings to enable real broker execution.
              Live trading requires verified broker credentials.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      <Header
        title="Live Trading"
        subtitle="Angel One — Real order execution"
        actions={
          <Badge variant="bear" dot>
            LIVE MODE
          </Badge>
        }
      />

      <div className="p-6 space-y-4">
        <div className="rounded-lg border border-bear/30 bg-bear-muted px-4 py-3 flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 flex-shrink-0 text-bear" />
          <div>
            <p className="text-sm font-medium text-bear">Live trading is active</p>
            <p className="mt-1 text-xs text-gray-400">
              Orders placed here will execute real trades against your Angel One account. Real money is at risk.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card title="Broker Status">
            <div className="space-y-3 text-sm text-gray-400">
              <p>Angel One SmartAPI connection status will appear here once the broker client is implemented (Phase 7).</p>
              <p className="text-xs text-gray-600">Broker adapter: <span className="text-warn">Not yet connected</span></p>
            </div>
          </Card>

          <Card title="Active Orders">
            <p className="text-sm text-gray-500">Order lifecycle tracking coming in Phase 7.</p>
          </Card>
        </div>

        <Card title="Emergency Controls">
          <div className="space-y-3">
            <p className="text-xs text-gray-500">
              Emergency kill-switch and position close controls will be wired to the broker
              client in Phase 7. The kill switch will cancel all open orders and close all
              positions at market price.
            </p>
          </div>
        </Card>
      </div>
    </div>
  )
}
