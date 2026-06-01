import { create } from 'zustand'
import type { PaperPositionResponse, PaperPnLResponse, PaperAccountResponse } from '@/types/paper'

interface PaperState {
  account: PaperAccountResponse | null
  openPositions: PaperPositionResponse[]
  pnlSnapshot: PaperPnLResponse | null

  setAccount: (account: PaperAccountResponse) => void
  setOpenPositions: (positions: PaperPositionResponse[]) => void
  upsertPosition: (position: PaperPositionResponse) => void
  removePosition: (id: string) => void
  setPnlSnapshot: (pnl: PaperPnLResponse) => void
}

export const usePaperStore = create<PaperState>((set) => ({
  account: null,
  openPositions: [],
  pnlSnapshot: null,

  setAccount: (account) => set({ account }),

  setOpenPositions: (openPositions) => set({ openPositions }),

  upsertPosition: (position) =>
    set((state) => {
      const idx = state.openPositions.findIndex((p) => p.id === position.id)
      if (idx >= 0) {
        const updated = [...state.openPositions]
        updated[idx] = position
        return { openPositions: updated }
      }
      return { openPositions: [position, ...state.openPositions] }
    }),

  removePosition: (id) =>
    set((state) => ({
      openPositions: state.openPositions.filter((p) => p.id !== id),
    })),

  setPnlSnapshot: (pnlSnapshot) => set({ pnlSnapshot }),
}))
