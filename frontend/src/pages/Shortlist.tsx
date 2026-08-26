import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle } from 'lucide-react'
import { OrhvTab } from '@/pages/shortlist/OrhvTab'

type ToastVariant = 'success' | 'error'
interface Toast {
  id: number
  variant: ToastVariant
  message: string
}

export function Shortlist() {
  const [toast, setToast] = useState<Toast | null>(null)

  const showToast = (variant: ToastVariant, message: string) => {
    setToast({ id: Date.now(), variant, message })
  }

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4_000)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <div className="flex flex-col">
      <div className="border-b border-border bg-surface px-6 pt-4 pb-3">
        <h1 className="text-lg font-semibold text-gray-100">Daily Shortlist</h1>
        <p className="mt-0.5 text-xs text-gray-500">
          ORHV — two-sided breakout setups validated by historical win rate.
        </p>
      </div>

      {toast && (
        <div className="fixed right-6 top-6 z-50">
          <div
            role="status"
            className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs shadow-lg ${
              toast.variant === 'success'
                ? 'border-bull/40 bg-bull-muted text-bull'
                : 'border-bear/40 bg-bear-muted text-bear'
            }`}
          >
            {toast.variant === 'success' ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            ) : (
              <XCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            )}
            <span className="max-w-xs">{toast.message}</span>
          </div>
        </div>
      )}

      <div className="p-6">
        <OrhvTab onToast={showToast} />
      </div>
    </div>
  )
}
