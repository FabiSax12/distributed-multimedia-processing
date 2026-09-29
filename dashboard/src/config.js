// Configuración de conexión con el coordinador.
//
// Por defecto todo es "mismo origen": CloudFront sirve el panel y reenvía
// /api/* al coordinador, así que no hace falta CORS ni conocer la IP del EC2
// (el security group del coordinador solo acepta tráfico de CloudFront y el
// coordinador exige X-Origin-Verify, que agrega CloudFront).

export const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

function defaultWsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api/ws`
}

export const WS_URL = import.meta.env.VITE_WS_URL || defaultWsUrl()

// Respaldo mientras el WebSocket está caído (contrato del coordinador).
export const POLL_INTERVAL_MS = 2000
// Backoff de reconexión: 1 s, 2 s, 4 s, ... hasta 30 s.
export const WS_BACKOFF_START_MS = 1000
export const WS_BACKOFF_MAX_MS = 30000
// El servidor manda {"type":"ping"} cada 25 s. Si pasan 60 s sin ningún
// mensaje, damos el socket por muerto y reconectamos.
export const WS_SILENCE_TIMEOUT_MS = 60000
// Serie de carga (GET /api/metrics).
export const METRICS_INTERVAL_MS = 10000
export const METRICS_WINDOW_MIN = 30
