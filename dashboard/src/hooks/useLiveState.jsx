// Estado en vivo del sistema (StateResponse), compartido por todas las vistas.
//
// Fuente principal: WebSocket /api/ws, que empuja {"type":"state","data":...}
// cada vez que el snapshot del coordinador cambia, y {"type":"ping"} cada 25 s.
// Respaldo: mientras el socket no esté abierto, polling de GET /api/state cada
// 2 s. Reconexión con backoff exponencial 1 s → 2 s → 4 s … hasta 30 s.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../api/client.js'
import {
  POLL_INTERVAL_MS,
  WS_BACKOFF_MAX_MS,
  WS_BACKOFF_START_MS,
  WS_SILENCE_TIMEOUT_MS,
  WS_URL,
} from '../config.js'

const LiveStateContext = createContext(null)

export function LiveStateProvider({ children }) {
  const [data, setData] = useState(null)
  // 'connecting' | 'ws' | 'polling' | 'offline'
  const [mode, setMode] = useState('connecting')
  const [receivedAt, setReceivedAt] = useState(null)
  const [nextRetryAt, setNextRetryAt] = useState(null)
  const [lastError, setLastError] = useState(null)

  const wsRef = useRef(null)
  const backoffRef = useRef(WS_BACKOFF_START_MS)
  const retryTimerRef = useRef(null)
  const silenceTimerRef = useRef(null)
  const pollTimerRef = useRef(null)
  const unmountedRef = useRef(false)

  const accept = useCallback((state) => {
    setData(state)
    setReceivedAt(new Date())
  }, [])

  // ---- Polling de respaldo ------------------------------------------------ //

  const pollOnce = useCallback(async () => {
    try {
      const state = await api.state()
      if (unmountedRef.current) return
      accept(state)
      setLastError(null)
      if (wsRef.current?.readyState !== WebSocket.OPEN) setMode('polling')
    } catch (err) {
      if (unmountedRef.current) return
      setLastError(err.message)
      if (wsRef.current?.readyState !== WebSocket.OPEN) setMode('offline')
    }
  }, [accept])

  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return
    pollOnce()
    pollTimerRef.current = setInterval(pollOnce, POLL_INTERVAL_MS)
  }, [pollOnce])

  const stopPolling = useCallback(() => {
    clearInterval(pollTimerRef.current)
    pollTimerRef.current = null
  }, [])

  // ---- WebSocket ---------------------------------------------------------- //

  const armSilenceTimer = useCallback(() => {
    clearTimeout(silenceTimerRef.current)
    silenceTimerRef.current = setTimeout(() => {
      // Ni estado ni ping en 60 s: el socket está muerto aunque no haya cerrado.
      wsRef.current?.close()
    }, WS_SILENCE_TIMEOUT_MS)
  }, [])

  const connect = useCallback(() => {
    if (unmountedRef.current) return
    clearTimeout(retryTimerRef.current)
    setNextRetryAt(null)

    let ws
    try {
      ws = new WebSocket(WS_URL)
    } catch (err) {
      setLastError(err.message)
      return
    }
    wsRef.current = ws

    ws.onopen = () => {
      backoffRef.current = WS_BACKOFF_START_MS
      setMode('ws')
      setLastError(null)
      stopPolling()
      armSilenceTimer()
    }

    ws.onmessage = (event) => {
      armSilenceTimer()
      let msg
      try {
        msg = JSON.parse(event.data)
      } catch {
        return
      }
      if (msg.type === 'state') accept(msg.data)
      // type === 'ping': solo mantiene viva la conexión, no trae datos.
    }

    ws.onerror = () => {
      // El detalle real llega en onclose; el navegador no expone el motivo.
    }

    ws.onclose = (event) => {
      clearTimeout(silenceTimerRef.current)
      if (wsRef.current !== ws || unmountedRef.current) return
      wsRef.current = null
      if (event.code === 1008) setLastError('El coordinador rechazó el WebSocket (origen no verificado).')
      else if (event.code === 1013) setLastError('El coordinador alcanzó el máximo de clientes WebSocket.')

      setMode((m) => (m === 'offline' ? 'offline' : 'polling'))
      startPolling()

      const delay = backoffRef.current
      backoffRef.current = Math.min(delay * 2, WS_BACKOFF_MAX_MS)
      setNextRetryAt(new Date(Date.now() + delay))
      retryTimerRef.current = setTimeout(connect, delay)
    }
  }, [accept, armSilenceTimer, startPolling, stopPolling])

  const reconnectNow = useCallback(() => {
    backoffRef.current = WS_BACKOFF_START_MS
    const ws = wsRef.current
    wsRef.current = null
    ws?.close()
    connect()
  }, [connect])

  useEffect(() => {
    unmountedRef.current = false
    // Arrancamos con polling para tener datos cuanto antes; se apaga solo en
    // cuanto el socket abre (el coordinador manda el último estado al conectar).
    startPolling()
    connect()
    return () => {
      unmountedRef.current = true
      clearTimeout(retryTimerRef.current)
      clearTimeout(silenceTimerRef.current)
      stopPolling()
      const ws = wsRef.current
      wsRef.current = null
      ws?.close()
    }
  }, [connect, startPolling, stopPolling])

  const value = { data, mode, receivedAt, nextRetryAt, lastError, reconnectNow, refresh: pollOnce }
  return <LiveStateContext.Provider value={value}>{children}</LiveStateContext.Provider>
}

export function useLiveState() {
  const ctx = useContext(LiveStateContext)
  if (!ctx) throw new Error('useLiveState debe usarse dentro de <LiveStateProvider>')
  return ctx
}
