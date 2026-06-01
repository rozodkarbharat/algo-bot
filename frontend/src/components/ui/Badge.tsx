import { cn } from '@/utils/cn'

type BadgeVariant = 'default' | 'bull' | 'bear' | 'warn' | 'accent' | 'ghost' | 'success' | 'error'

const variantClasses: Record<BadgeVariant, string> = {
  default: 'bg-surface-50 text-gray-300 border-border',
  bull: 'bg-bull-muted text-bull border-bull/30',
  bear: 'bg-bear-muted text-bear border-bear/30',
  warn: 'bg-warn-muted text-warn border-warn/30',
  accent: 'bg-accent-muted text-accent border-accent/30',
  ghost: 'bg-transparent text-gray-400 border-transparent',
  success: 'bg-bull-muted text-bull border-bull/30',
  error: 'bg-bear-muted text-bear border-bear/30',
}

interface BadgeProps {
  children: React.ReactNode
  variant?: BadgeVariant
  className?: string
  dot?: boolean
}

export function Badge({ children, variant = 'default', className, dot }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-xs font-medium',
        variantClasses[variant],
        className,
      )}
    >
      {dot && (
        <span
          className={cn(
            'inline-block h-1.5 w-1.5 rounded-full',
            variant === 'bull' || variant === 'success' ? 'bg-bull' : '',
            variant === 'bear' || variant === 'error' ? 'bg-bear' : '',
            variant === 'warn' ? 'bg-warn' : '',
            variant === 'accent' ? 'bg-accent' : '',
            variant === 'default' ? 'bg-gray-400' : '',
            variant === 'ghost' ? 'bg-gray-600' : '',
          )}
        />
      )}
      {children}
    </span>
  )
}
