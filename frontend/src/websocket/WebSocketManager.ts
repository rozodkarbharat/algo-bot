import type { WSMessage, WSConnectionStatus, WSRoom } from '@/types/websocket'

type MessageHandler = (msg: WSMessage) => void

interface WSConnection {
  ws: WebSocket | null
  room: WSRoom
  status: WSConnectionStatus
  handlers: Set<MessageHandler>
  reconnectTimer: ReturnType<typeof setTimeout> | null
  reconnectDelay: number
  manualClose: boolean
}

const WS_BASE_URL = (() => {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = import.meta.env.VITE_WS_BASE_URL ?? window.location.host
  return `${proto}://${host}`
})()

const MAX_RECONNECT_DELAY = 30_000
const INITIAL_RECONNECT_DELAY = 1_000

export class WebSocketManager {
  private connections: Map<WSRoom, WSConnection> = new Map()
  private statusListeners: Map<WSRoom, Set<(s: WSConnectionStatus) => void>> = new Map()

  private roomToPath(room: WSRoom): string {
    if (room.startsWith('market:')) {
      const symbol = room.slice('market:'.length)
      return `/ws/market/${symbol}`
    }
    if (room === 'signals') return '/ws/signals'
    if (room === 'orders') return '/ws/orders'
    if (room === 'live:market-state') return '/ws/live/market-state'
    if (room === 'paper:trades') return '/ws/paper/trades'
    if (room === 'paper:positions') return '/ws/paper/positions'
    if (room === 'paper:pnl') return '/ws/paper/pnl'
    if (room === 'paper:account') return '/ws/paper/account'
    throw new Error(`Unknown WS room: ${room}`)
  }

  subscribe(room: WSRoom, handler: MessageHandler): () => void {
    if (!this.connections.has(room)) {
      this.connections.set(room, {
        ws: null,
        room,
        status: 'disconnected',
        handlers: new Set(),
        reconnectTimer: null,
        reconnectDelay: INITIAL_RECONNECT_DELAY,
        manualClose: false,
      })
    }

    const conn = this.connections.get(room)!
    conn.handlers.add(handler)

    if (conn.ws === null || conn.ws.readyState === WebSocket.CLOSED) {
      this.connect(room)
    }

    return () => {
      conn.handlers.delete(handler)
      if (conn.handlers.size === 0) {
        this.disconnect(room)
      }
    }
  }

  onStatusChange(room: WSRoom, listener: (s: WSConnectionStatus) => void): () => void {
    if (!this.statusListeners.has(room)) {
      this.statusListeners.set(room, new Set())
    }
    this.statusListeners.get(room)!.add(listener)
    return () => this.statusListeners.get(room)?.delete(listener)
  }

  private connect(room: WSRoom): void {
    const conn = this.connections.get(room)
    if (!conn) return

    conn.manualClose = false
    this.setStatus(room, 'connecting')

    const url = `${WS_BASE_URL}${this.roomToPath(room)}`
    const ws = new WebSocket(url)
    conn.ws = ws

    ws.onopen = () => {
      conn.reconnectDelay = INITIAL_RECONNECT_DELAY
      this.setStatus(room, 'connected')
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as WSMessage
        conn.handlers.forEach((h) => h(msg))
      } catch {
        // non-JSON messages (ping/pong) — ignore
      }
    }

    ws.onerror = () => {
      this.setStatus(room, 'error')
    }

    ws.onclose = () => {
      conn.ws = null
      if (conn.manualClose) {
        this.setStatus(room, 'disconnected')
        return
      }
      this.setStatus(room, 'disconnected')
      this.scheduleReconnect(room)
    }
  }

  private scheduleReconnect(room: WSRoom): void {
    const conn = this.connections.get(room)
    if (!conn || conn.handlers.size === 0) return

    if (conn.reconnectTimer) clearTimeout(conn.reconnectTimer)

    conn.reconnectTimer = setTimeout(() => {
      conn.reconnectTimer = null
      if (conn.handlers.size > 0) {
        this.connect(room)
      }
      conn.reconnectDelay = Math.min(conn.reconnectDelay * 2, MAX_RECONNECT_DELAY)
    }, conn.reconnectDelay)
  }

  private disconnect(room: WSRoom): void {
    const conn = this.connections.get(room)
    if (!conn) return

    conn.manualClose = true
    if (conn.reconnectTimer) {
      clearTimeout(conn.reconnectTimer)
      conn.reconnectTimer = null
    }
    if (conn.ws) {
      conn.ws.close()
      conn.ws = null
    }
    this.connections.delete(room)
    this.statusListeners.delete(room)
  }

  private setStatus(room: WSRoom, status: WSConnectionStatus): void {
    const conn = this.connections.get(room)
    if (conn) conn.status = status
    this.statusListeners.get(room)?.forEach((l) => l(status))
  }

  getStatus(room: WSRoom): WSConnectionStatus {
    return this.connections.get(room)?.status ?? 'disconnected'
  }

  disconnectAll(): void {
    for (const room of [...this.connections.keys()]) {
      this.disconnect(room as WSRoom)
    }
  }
}

export const wsManager = new WebSocketManager()
