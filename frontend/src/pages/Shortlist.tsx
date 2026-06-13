import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, XCircle } from 'lucide-react'
import { OneSideOrbTab } from '@/pages/shortlist/OneSideOrbTab'
import { OrhvTab } from '@/pages/shortlist/OrhvTab'

export type ShortlistStrategyTab = 'one_side_orb' | 'orhv'

const TABS: { id: ShortlistStrategyTab; label: string; hint: string }[] = [
  {
    id: 'one_side_orb',
    label: 'One-Side ORB',
    hint: 'Yesterday one-side day + continuation probability',
  },
  {
    id: 'orhv',
    label: 'ORHV',
    hint: 'Two-sided breakout + historical win-rate validation',
  },
]

type ToastVariant = 'success' | 'error'
interface Toast {
  id: number
  variant: ToastVariant
  message: string
}

export function Shortlist() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('strategy')
  const activeTab: ShortlistStrategyTab = tabParam === 'orhv' ? 'orhv' : 'one_side_orb'

  const [toast, setToast] = useState<Toast | null>(null)

  const showToast = (variant: ToastVariant, message: string) => {
    setToast({ id: Date.now(), variant, message })
  }

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4_000)
    return () => clearTimeout(t)
  }, [toast])

  const setActiveTab = (id: ShortlistStrategyTab) => {
    if (id === 'one_side_orb') {
      setSearchParams({})
    } else {
      setSearchParams({ strategy: id })
    }
  }

  return (
    <div className="flex flex-col">
      <div className="border-b border-border bg-surface px-6 pt-4">
        <h1 className="text-lg font-semibold text-gray-100">Daily Shortlist</h1>
        <p className="mt-0.5 text-xs text-gray-500">
          Pick a strategy — each has its own pipeline, run button, and candidate list.
        </p>
        <div className="mt-4 flex gap-1" role="tablist" aria-label="Shortlist strategy">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              title={tab.hint}
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-t-md border px-4 py-2 text-xs font-medium transition-colors ${
                activeTab === tab.id
                  ? 'border-border border-b-surface bg-bg text-accent'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
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
        {activeTab === 'one_side_orb' ? (
          <OneSideOrbTab onToast={showToast} />
        ) : (
          <OrhvTab onToast={showToast} />
        )}
      </div>
    </div>
  )
}
