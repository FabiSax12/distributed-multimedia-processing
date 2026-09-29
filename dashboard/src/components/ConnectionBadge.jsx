// Indicador de cómo le llegan los datos al panel: WebSocket, polling de
// respaldo o sin conexión. Deja reconectar a mano sin esperar el backoff.

import { useEffect, useState } from 'react'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { formatTime } from '../lib/format.js'

const LABELS = {
  connecting: { text: 'Conectando…', tone: 'neutral' },
  ws: { text: 'En vivo · WebSocket', tone: 'ok' },
  polling: { text: 'Respaldo · polling 2 s', tone: 'warn' },
  offline: { text: 'Sin conexión', tone: 'bad' },
}

export default function ConnectionBadge() {
  const { mode, receivedAt, nextRetryAt, lastError, reconnectNow } = useLiveState()
  const [, tick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const info = LABELS[mode]
  const retryIn = nextRetryAt ? Math.max(0, Math.ceil((nextRetryAt - Date.now()) / 1000)) : null
  const detail = [
    receivedAt && `Último dato ${formatTime(receivedAt.toISOString())}`,
    mode !== 'ws' && retryIn != null && `WebSocket reintenta en ${retryIn} s`,
    lastError,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="conn" title={detail}>
      <span className={`conn-dot tone-${info.tone}`} aria-hidden="true" />
      <span className="conn-text">
        <strong>{info.text}</strong>
        <small>{detail || 'Esperando el primer estado del coordinador'}</small>
      </span>
      {mode !== 'ws' && (
        <button type="button" className="btn btn-small" onClick={reconnectNow}>
          Reconectar
        </button>
      )}
    </div>
  )
}
