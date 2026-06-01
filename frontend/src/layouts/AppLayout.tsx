import { Outlet } from 'react-router-dom'
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Sidebar } from './Sidebar'
import { healthApi } from '@/api'
import { useSystemStore } from '@/store/useSystemStore'

export function AppLayout() {
  const { setBackendOnline, setDbConnected, setLastHealthCheck } = useSystemStore()

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: healthApi.liveness,
    refetchInterval: 15_000,
    retry: false,
  })

  const { data: readiness } = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: healthApi.readiness,
    refetchInterval: 15_000,
    retry: false,
  })

  useEffect(() => {
    setBackendOnline(!!health)
    setLastHealthCheck(health ? new Date().toISOString() : '')
  }, [health, setBackendOnline, setLastHealthCheck])

  useEffect(() => {
    setDbConnected(readiness?.database === 'connected')
  }, [readiness, setDbConnected])

  return (
    <div className="flex h-screen overflow-hidden bg-bg text-gray-100">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
