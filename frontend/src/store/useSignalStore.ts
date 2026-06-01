import { create } from 'zustand'
import type { LiveSignalResponse, LiveEngineStatusResponse } from '@/types/signal'

interface SignalState {
  // Live signals buffer (newest first, capped at 200)
  liveSignals: LiveSignalResponse[]
  engineStatus: LiveEngineStatusResponse | null

  // Actions
  pushSignal: (signal: LiveSignalResponse) => void
  setSignals: (signals: LiveSignalResponse[]) => void
  setEngineStatus: (status: LiveEngineStatusResponse) => void
  clearSignals: () => void
}

export const useSignalStore = create<SignalState>((set) => ({
  liveSignals: [],
  engineStatus: null,

  pushSignal: (signal) =>
    set((state) => ({
      liveSignals: [signal, ...state.liveSignals].slice(0, 200),
    })),

  setSignals: (liveSignals) => set({ liveSignals }),

  setEngineStatus: (engineStatus) => set({ engineStatus }),

  clearSignals: () => set({ liveSignals: [] }),
}))
