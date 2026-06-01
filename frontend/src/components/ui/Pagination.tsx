import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from './Button'

interface PaginationProps {
  page: number
  pages: number
  total: number
  pageSize: number
  onPageChange: (page: number) => void
}

export function Pagination({ page, pages, total, pageSize, onPageChange }: PaginationProps) {
  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, total)

  if (total === 0) return null

  return (
    <div className="flex items-center justify-between px-1 py-2 text-xs text-gray-500">
      <span>
        {start}–{end} of {total}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          icon={<ChevronLeft className="h-3.5 w-3.5" />}
        />
        <span className="px-2 font-mono text-gray-300">
          {page} / {pages}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= pages}
          icon={<ChevronRight className="h-3.5 w-3.5" />}
        />
      </div>
    </div>
  )
}
