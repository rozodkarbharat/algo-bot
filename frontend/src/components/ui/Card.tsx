import { cn } from '@/utils/cn'
import type { ReactNode } from 'react'

interface CardProps {
  title?: string
  subtitle?: string
  children: ReactNode
  className?: string
  headerRight?: ReactNode
  noPadding?: boolean
}

export function Card({ title, subtitle, children, className, headerRight, noPadding }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-surface',
        className,
      )}
    >
      {(title || headerRight) && (
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            {title && (
              <h3 className="text-sm font-semibold uppercase tracking-wider text-gray-300">
                {title}
              </h3>
            )}
            {subtitle && <p className="mt-0.5 text-xs text-gray-500">{subtitle}</p>}
          </div>
          {headerRight && <div className="flex items-center gap-2">{headerRight}</div>}
        </div>
      )}
      <div className={cn(!noPadding && 'p-4')}>{children}</div>
    </div>
  )
}

interface StatCardProps {
  label: string
  value: string | number
  sub?: string
  valueClass?: string
  icon?: ReactNode
  trend?: 'up' | 'down' | 'neutral'
}

export function StatCard({ label, value, sub, valueClass, icon }: StatCardProps) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-start justify-between">
        <p className="text-xs font-medium uppercase tracking-wider text-gray-500">{label}</p>
        {icon && <div className="text-gray-600">{icon}</div>}
      </div>
      <p className={cn('mt-2 text-2xl font-semibold font-mono', valueClass ?? 'text-gray-100')}>
        {value}
      </p>
      {sub && <p className="mt-1 text-xs text-gray-500">{sub}</p>}
    </div>
  )
}
