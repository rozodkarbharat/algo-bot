import { cn } from '@/utils/cn'
import type { WSConnectionStatus } from '@/types/websocket'

interface StatusDotProps {
  status: 'online' | 'offline' | 'warning' | 'unknown' | WSConnectionStatus
  label?: string
  className?: string
  animate?: boolean
}

const statusColors = {
  online: 'bg-bull',
  connected: 'bg-bull',
  offline: 'bg-bear',
  disconnected: 'bg-gray-600',
  warning: 'bg-warn',
  error: 'bg-bear',
  unknown: 'bg-gray-600',
  connecting: 'bg-warn',
}

export function StatusDot({ status, label, className, animate }: StatusDotProps) {
  const color = statusColors[status as keyof typeof statusColors] ?? 'bg-gray-600'
  const isActive = status === 'online' || status === 'connected'

  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className="relative inline-flex">
        {(animate ?? isActive) && (
          <span
            className={cn('absolute inline-flex h-full w-full animate-ping rounded-full opacity-75', color)}
          />
        )}
        <span className={cn('relative inline-flex h-2 w-2 rounded-full', color)} />
      </span>
      {label && <span className="text-xs text-gray-400">{label}</span>}
    </span>
  )
}
