import { create } from 'zustand'
import type { WSConnectionStatus } from '@/types/websocket'

interface BrokerStatus {
  connected: boolean
  last_checked: string | null
}

interface SchedulerJobStatus {
  job_id: string
  next_run: string | null
  last_run: string | null
  status: 'running' | 'idle' | 'failed'
}

interface SystemState {
  // Health
  backendOnline: boolean
  dbConnected: boolean
  lastHealthCheck: string | null

  // Broker
  broker: BrokerStatus

  // Scheduler jobs
  schedulerJobs: SchedulerJobStatus[]

  // WebSocket connection states per room
  wsStatus: Record<string, WSConnectionStatus>

  // Actions
  setBackendOnline: (online: boolean) => void
  setDbConnected: (connected: boolean) => void
  setLastHealthCheck: (ts: string) => void
  setBrokerStatus: (status: BrokerStatus) => void
  setSchedulerJobs: (jobs: SchedulerJobStatus[]) => void
  setWsStatus: (room: string, status: WSConnectionStatus) => void
}

export const useSystemStore = create<SystemState>((set) => ({
  backendOnline: false,
  dbConnected: false,
  lastHealthCheck: null,
  broker: { connected: false, last_checked: null },
  schedulerJobs: [],
  wsStatus: {},

  setBackendOnline: (online) => set({ backendOnline: online }),
  setDbConnected: (connected) => set({ dbConnected: connected }),
  setLastHealthCheck: (ts) => set({ lastHealthCheck: ts }),
  setBrokerStatus: (broker) => set({ broker }),
  setSchedulerJobs: (schedulerJobs) => set({ schedulerJobs }),
  setWsStatus: (room, status) =>
    set((state) => ({ wsStatus: { ...state.wsStatus, [room]: status } })),
}))
