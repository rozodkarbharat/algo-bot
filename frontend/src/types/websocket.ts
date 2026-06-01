// ── WebSocket message envelopes ───────────────────────────────────────────────

export type WSRoom =
  | 'signals'
  | 'orders'
  | 'live:market-state'
  | 'paper:trades'
  | 'paper:positions'
  | 'paper:pnl'
  | 'paper:account'
  | `market:${string}`

export type WSMessageType =
  // Live engine
  | 'live.engine.started'
  | 'live.engine.stopped'
  | 'live.signal'
  | 'live.candle'
  | 'live.breakout'
  | 'live.market_state'
  // Paper trading
  | 'paper.position.opened'
  | 'paper.position.closed'
  | 'paper.position.mtm'
  | 'paper.trade.completed'
  | 'paper.pnl.snapshot'
  | 'paper.account.updated'
  // Orders
  | 'order.placed'
  | 'order.filled'
  | 'order.cancelled'
  | 'order.rejected'
  // Market
  | 'market.tick'
  | 'market.candle'
  // System
  | 'system.heartbeat'
  | 'system.scheduler_job'

export interface WSMessage<T = unknown> {
  type: WSMessageType
  data: T
  timestamp: string
}

export type WSConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error'
