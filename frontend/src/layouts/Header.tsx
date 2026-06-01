import { RefreshCw, Clock } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { fmtDateTime } from '@/utils/formatters'
import { useSystemStore } from '@/store/useSystemStore'

interface HeaderProps {
  title: string
  subtitle?: string
  onRefresh?: () => void
  isRefreshing?: boolean
  actions?: React.ReactNode
}

export function Header({ title, subtitle, onRefresh, isRefreshing, actions }: HeaderProps) {
  const { lastHealthCheck } = useSystemStore()

  return (
    <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-border bg-bg-secondary px-6">
      <div>
        <h1 className="text-sm font-semibold text-gray-100">{title}</h1>
        {subtitle && <p className="text-xs text-gray-500">{subtitle}</p>}
      </div>

      <div className="flex items-center gap-3">
        {lastHealthCheck && (
          <span className="flex items-center gap-1 text-xs text-gray-600">
            <Clock className="h-3 w-3" />
            {fmtDateTime(lastHealthCheck)}
          </span>
        )}
        {actions}
        {onRefresh && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            loading={isRefreshing}
            icon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            Refresh
          </Button>
        )}
      </div>
    </header>
  )
}
