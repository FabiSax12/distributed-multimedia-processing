export function formatBytes(n) {
  if (n == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('es-CR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function formatDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('es-CR', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return '—'
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  if (m < 60) return `${m} min ${s} s`
  return `${Math.floor(m / 60)} h ${m % 60} min`
}

export function durationBetween(startIso, endIso) {
  if (!startIso) return null
  const end = endIso ? new Date(endIso) : new Date()
  return (end - new Date(startIso)) / 1000
}

export function secondsAgo(iso) {
  if (!iso) return null
  return Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
}

export function shortId(id) {
  return id ? id.slice(-8) : '—'
}

export function basename(key) {
  return key ? key.split('/').pop() : '—'
}
