import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type TradingMode = 'paper' | 'live'

interface SettingsState {
  tradingMode: TradingMode
  probabilityThreshold: number
  maxDailyTrades: number
  capitalPerTrade: number
  refreshIntervalMs: number
  autoRefresh: boolean

  setTradingMode: (mode: TradingMode) => void
  setProbabilityThreshold: (v: number) => void
  setMaxDailyTrades: (v: number) => void
  setCapitalPerTrade: (v: number) => void
  setRefreshIntervalMs: (v: number) => void
  setAutoRefresh: (v: boolean) => void
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      tradingMode: 'paper',
      probabilityThreshold: 0.6,
      maxDailyTrades: 5,
      capitalPerTrade: 50000,
      refreshIntervalMs: 30_000,
      autoRefresh: true,

      setTradingMode: (tradingMode) => set({ tradingMode }),
      setProbabilityThreshold: (probabilityThreshold) => set({ probabilityThreshold }),
      setMaxDailyTrades: (maxDailyTrades) => set({ maxDailyTrades }),
      setCapitalPerTrade: (capitalPerTrade) => set({ capitalPerTrade }),
      setRefreshIntervalMs: (refreshIntervalMs) => set({ refreshIntervalMs }),
      setAutoRefresh: (autoRefresh) => set({ autoRefresh }),
    }),
    { name: 'trading-bot-settings' },
  ),
)
