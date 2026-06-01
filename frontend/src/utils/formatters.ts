import { format, parseISO } from 'date-fns'

export function fmtCurrency(value: number | null | undefined, decimals = 2): string {
  if (value == null) return '—'
  const absVal = Math.abs(value)
  if (absVal >= 1_00_000) {
    return `₹${(value / 1_00_000).toFixed(2)}L`
  }
  if (absVal >= 1_000) {
    return `₹${(value / 1_000).toFixed(2)}K`
  }
  return `₹${value.toFixed(decimals)}`
}

export function fmtPrice(value: number | null | undefined): string {
  if (value == null) return '—'
  return `₹${value.toFixed(2)}`
}

export function fmtPct(value: number | null | undefined, decimals = 1): string {
  if (value == null) return '—'
  return `${(value * 100).toFixed(decimals)}%`
}

export function fmtPctRaw(value: number | null | undefined, decimals = 1): string {
  if (value == null) return '—'
  return `${value.toFixed(decimals)}%`
}

export function fmtNumber(value: number | null | undefined, decimals = 0): string {
  if (value == null) return '—'
  return value.toLocaleString('en-IN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function fmtDate(isoString: string | null | undefined): string {
  if (!isoString) return '—'
  try {
    return format(parseISO(isoString), 'dd MMM yyyy')
  } catch {
    return isoString
  }
}

export function fmtDateTime(isoString: string | null | undefined): string {
  if (!isoString) return '—'
  try {
    return format(parseISO(isoString), 'dd MMM HH:mm:ss')
  } catch {
    return isoString
  }
}

export function fmtTime(isoString: string | null | undefined): string {
  if (!isoString) return '—'
  try {
    return format(parseISO(isoString), 'HH:mm:ss')
  } catch {
    return isoString
  }
}

export function pnlClass(value: number | null | undefined): string {
  if (value == null) return 'text-gray-400'
  if (value > 0) return 'text-bull'
  if (value < 0) return 'text-bear'
  return 'text-gray-400'
}

export function pnlSign(value: number | null | undefined): string {
  if (value == null || value === 0) return ''
  return value > 0 ? '+' : ''
}
