import { useEffect, useState, useCallback, useRef } from 'react'
import { wsManager } from '@/websocket/WebSocketManager'
import type { WSMessage, WSConnectionStatus, WSRoom, WSMessageType } from '@/types/websocket'

export function useWebSocket<T = unknown>(
  room: WSRoom,
  messageType?: WSMessageType | WSMessageType[],
): {
  lastMessage: WSMessage<T> | null
  status: WSConnectionStatus
} {
  const [lastMessage, setLastMessage] = useState<WSMessage<T> | null>(null)
  const [status, setStatus] = useState<WSConnectionStatus>('disconnected')
  const typesRef = useRef(messageType)
  typesRef.current = messageType

  const handler = useCallback(
    (msg: WSMessage) => {
      const types = typesRef.current
      if (!types) {
        setLastMessage(msg as WSMessage<T>)
        return
      }
      const allowed = Array.isArray(types) ? types : [types]
      if (allowed.includes(msg.type)) {
        setLastMessage(msg as WSMessage<T>)
      }
    },
    [],
  )

  useEffect(() => {
    const unsubMsg = wsManager.subscribe(room, handler)
    const unsubStatus = wsManager.onStatusChange(room, setStatus)
    setStatus(wsManager.getStatus(room))
    return () => {
      unsubMsg()
      unsubStatus()
    }
  }, [room, handler])

  return { lastMessage, status }
}

export function useWebSocketMessages<T = unknown>(
  room: WSRoom,
  messageType?: WSMessageType | WSMessageType[],
  maxMessages = 100,
): {
  messages: WSMessage<T>[]
  status: WSConnectionStatus
  clear: () => void
} {
  const [messages, setMessages] = useState<WSMessage<T>[]>([])
  const [status, setStatus] = useState<WSConnectionStatus>('disconnected')
  const typesRef = useRef(messageType)
  typesRef.current = messageType

  const handler = useCallback(
    (msg: WSMessage) => {
      const types = typesRef.current
      if (types) {
        const allowed = Array.isArray(types) ? types : [types]
        if (!allowed.includes(msg.type)) return
      }
      setMessages((prev) => [msg as WSMessage<T>, ...prev].slice(0, maxMessages))
    },
    [maxMessages],
  )

  useEffect(() => {
    const unsubMsg = wsManager.subscribe(room, handler)
    const unsubStatus = wsManager.onStatusChange(room, setStatus)
    setStatus(wsManager.getStatus(room))
    return () => {
      unsubMsg()
      unsubStatus()
    }
  }, [room, handler])

  const clear = useCallback(() => setMessages([]), [])

  return { messages, status, clear }
}
