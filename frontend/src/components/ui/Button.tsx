import { cn } from '@/utils/cn'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'bull' | 'bear'
type ButtonSize = 'sm' | 'md' | 'lg'

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-light border-transparent disabled:bg-accent/50',
  secondary:
    'bg-surface-50 text-gray-200 hover:bg-surface-100 border-border disabled:opacity-50',
  ghost: 'bg-transparent text-gray-400 hover:text-gray-200 hover:bg-surface-50 border-transparent',
  danger: 'bg-bear text-white hover:bg-bear-light border-transparent disabled:bg-bear/50',
  bull: 'bg-bull text-black hover:bg-bull-light border-transparent disabled:bg-bull/50',
  bear: 'bg-bear text-white hover:bg-bear-light border-transparent disabled:bg-bear/50',
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-5 py-2.5 text-sm',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
  children?: ReactNode
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading,
  icon,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled ?? loading}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded border font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-accent/50 disabled:cursor-not-allowed',
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
    >
      {loading ? (
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
      ) : (
        icon
      )}
      {children}
    </button>
  )
}
