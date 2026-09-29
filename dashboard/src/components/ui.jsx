// Piezas visuales pequeñas que usan todas las vistas.

import { CASE_STATUS, SUBTASK_ORDER, SUBTASK_STATUS } from '../lib/domain.js'

export function StatusPill({ status, kind = 'case' }) {
  const table = kind === 'case' ? CASE_STATUS : SUBTASK_STATUS
  const info = table[status] ?? { label: status, tone: 'neutral' }
  return <span className={`pill tone-${info.tone}`}>{info.label}</span>
}

// Barra apilada con el conteo de sub-tareas por estado (by_status).
export function StackedProgress({ byStatus = {}, total }) {
  const sum = total ?? Object.values(byStatus).reduce((a, b) => a + b, 0)
  if (!sum) return <div className="stack empty" />
  return (
    <div className="stack" role="img" aria-label={describe(byStatus)}>
      {SUBTASK_ORDER.filter((s) => byStatus[s]).map((s) => (
        <span
          key={s}
          className={`seg seg-${s}`}
          style={{ width: `${(byStatus[s] / sum) * 100}%` }}
          title={`${SUBTASK_STATUS[s].label}: ${byStatus[s]}`}
        />
      ))}
    </div>
  )
}

function describe(byStatus) {
  return Object.entries(byStatus)
    .map(([s, n]) => `${SUBTASK_STATUS[s]?.label ?? s}: ${n}`)
    .join(', ')
}

export function StatusLegend() {
  return (
    <div className="legend">
      {SUBTASK_ORDER.map((s) => (
        <span key={s} className="legend-item">
          <i className={`dot seg-${s}`} /> {SUBTASK_STATUS[s].label}
        </span>
      ))}
    </div>
  )
}

// Medidor horizontal 0–100 % (CPU, memoria). Cambia de tono al saturarse.
export function Meter({ value = 0, label, warnAt = 70, badAt = 85 }) {
  const v = Math.max(0, Math.min(100, value))
  const tone = v >= badAt ? 'bad' : v >= warnAt ? 'warn' : 'ok'
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <span className="num">{v.toFixed(0)} %</span>
      </div>
      <div className="meter-track">
        <div className={`meter-fill tone-${tone}`} style={{ width: `${v}%` }} />
      </div>
    </div>
  )
}

export function ErrorBanner({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="banner tone-bad" role="alert">
      <span>{typeof error === 'string' ? error : error.message}</span>
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Reintentar
        </button>
      )}
    </div>
  )
}

export function Empty({ title, children }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  )
}

export function Section({ title, aside, children, className = '' }) {
  return (
    <section className={`section ${className}`}>
      <header className="section-head">
        <h2>{title}</h2>
        {aside && <div className="section-aside">{aside}</div>}
      </header>
      {children}
    </section>
  )
}
