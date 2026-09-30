// Detalle de un caso: barrier, sub-tareas agrupadas por archivo, cancelación y
// reporte consolidado.
//
// GET /api/cases/{id} trae el caso y todas sus sub-tareas. Se vuelve a pedir
// cada vez que el estado en vivo muestra un cambio en ese caso (by_status o
// status), y cada 5 s mientras el caso no termine, por si el cambio fue solo
// de progreso (%), que el resumen del estado no incluye.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, toDevS3Url } from '../api/client.js'
import { Empty, ErrorBanner, Section, StackedProgress, StatusLegend, StatusPill } from '../components/ui.jsx'
import { useInterval } from '../hooks/useInterval.js'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { ERROR_CODES, MEDIA_TYPES, OPERATIONS, SUBTASK_STATUS, TERMINAL_CASE } from '../lib/domain.js'
import { basename, durationBetween, formatDateTime, formatDuration, formatTime, shortId } from '../lib/format.js'

const DETAIL_REFRESH_MS = 5000

export default function CaseDetail({ caseId }) {
  const { data: live } = useLiveState()
  const [detail, setDetail] = useState(null)
  const [outputs, setOutputs] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    // Independientes a propósito: /outputs es un extra sobre el detalle del
    // caso, no debe tumbar una página que ya venía funcionando si falla solo
    // esa llamada (throttling de DynamoDB, hiccup al presignar, etc.).
    const [caseResult, outputsResult] = await Promise.allSettled([
      api.getCase(caseId),
      api.caseOutputs(caseId),
    ])
    if (caseResult.status === 'fulfilled') {
      setDetail(caseResult.value)
      setError(null)
    } else {
      setError(caseResult.reason)
    }
    if (outputsResult.status === 'fulfilled') setOutputs(outputsResult.value)
  }, [caseId])

  // subtask_id -> lista de URLs prefirmadas de sus output_keys.
  const outputsBySubtask = useMemo(() => {
    const map = new Map()
    for (const s of outputs?.subtasks ?? []) map.set(s.subtask_id, s.outputs)
    return map
  }, [outputs])

  useEffect(() => {
    setDetail(null)
    load()
  }, [load])

  // Firma del caso en el estado en vivo: si cambia, recargamos el detalle.
  const summary = live?.cases.find((c) => c.case.case_id === caseId)
  const signature = summary ? `${summary.case.status}|${JSON.stringify(summary.by_status)}` : null
  useEffect(() => {
    if (signature) load()
  }, [signature, load])

  const terminal = detail && TERMINAL_CASE.has(detail.case.status)
  useInterval(load, detail && !terminal ? DETAIL_REFRESH_MS : null)

  if (error && !detail) {
    return (
      <div className="stack-lg">
        <a href="#/casos" className="back">← Casos</a>
        <ErrorBanner error={error.status === 404 ? 'Ese caso no existe.' : error} onRetry={load} />
      </div>
    )
  }
  if (!detail) return <Empty title="Cargando caso…" />

  return (
    <div className="stack-lg">
      <a href="#/casos" className="back">← Casos</a>
      <CaseHeader c={detail.case} onChanged={load} />
      <Barrier c={detail.case} subtasks={detail.subtasks} />
      {terminal && <Report caseId={caseId} status={detail.case.status} />}
      <SubTasks subtasks={detail.subtasks} outputsBySubtask={outputsBySubtask} />
    </div>
  )
}

function CaseHeader({ c, onChanged }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const terminal = TERMINAL_CASE.has(c.status)

  const cancel = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.cancelCase(c.case_id)
      setConfirming(false)
      onChanged()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <header className="case-header">
      <div className="case-title">
        <h1>{c.label || `Caso ${shortId(c.case_id)}`}</h1>
        <div className="case-tags">
          <StatusPill status={c.status} />
          {c.priority === 'alta' ? <span className="pill tone-accent">Prioridad alta</span> : <span className="pill tone-neutral">Prioridad normal</span>}
          <span className="pill tone-neutral">{c.source === 'auto' ? 'Generación automática' : 'Manual'}</span>
          {c.cancel_requested && c.status !== 'cancelled' && <span className="pill tone-warn">Cancelación pedida</span>}
        </div>
        <p className="mono muted small">{c.case_id}</p>
      </div>

      <dl className="facts">
        <div><dt>Creado</dt><dd>{formatDateTime(c.created_at)}</dd></div>
        <div><dt>Inició</dt><dd>{formatDateTime(c.started_at)}</dd></div>
        <div><dt>Terminó</dt><dd>{formatDateTime(c.finished_at)}</dd></div>
        <div><dt>Duración</dt><dd className="num">{formatDuration(durationBetween(c.started_at || c.created_at, c.finished_at))}</dd></div>
      </dl>

      {!terminal && !c.cancel_requested && (
        <div className="case-actions">
          {confirming ? (
            <div className="confirm">
              <span>Las sub-tareas que no arrancaron se descartan; las que ya están en ejecución terminan y se registran.</span>
              <button type="button" className="btn btn-danger" onClick={cancel} disabled={busy}>
                {busy ? 'Cancelando…' : 'Sí, cancelar caso'}
              </button>
              <button type="button" className="btn" onClick={() => setConfirming(false)} disabled={busy}>
                No
              </button>
            </div>
          ) : (
            <button type="button" className="btn" onClick={() => setConfirming(true)}>
              Cancelar caso
            </button>
          )}
          <ErrorBanner error={error} />
        </div>
      )}
    </header>
  )
}

// El barrier: pending_count arranca en N y cada sub-tarea terminal lo baja en 1.
function Barrier({ c, subtasks }) {
  const byStatus = useMemo(() => {
    const out = {}
    for (const s of subtasks) out[s.status] = (out[s.status] || 0) + 1
    return out
  }, [subtasks])
  const closed = c.total - c.pending_count

  return (
    <Section title="Barrier / join" aside={<span className="muted">El caso cierra cuando pending_count llega a 0</span>}>
      <div className="barrier">
        <div className="barrier-counter">
          <span className="kpi-label">pending_count</span>
          <span className="barrier-num num">{c.pending_count}</span>
          <span className="muted small">de {c.total} sub-tareas · {c.file_count} archivos</span>
        </div>
        <div className="barrier-body">
          <StackedProgress byStatus={byStatus} total={c.total} />
          <div className="barrier-stats">
            <span><b className="num">{closed}</b> resueltas</span>
            <span><b className="num">{byStatus.completed || 0}</b> completadas</span>
            <span><b className="num">{c.failed_count}</b> fallidas</span>
            <span><b className="num">{c.cancelled_count}</b> canceladas</span>
            <span><b className="num">{(byStatus.running || 0) + (byStatus.assigned || 0)}</b> en proceso</span>
            <span><b className="num">{byStatus.retrying || 0}</b> reintentando</span>
          </div>
          <StatusLegend />
        </div>
      </div>
    </Section>
  )
}

function SubTasks({ subtasks, outputsBySubtask }) {
  const [filter, setFilter] = useState('all')

  const groups = useMemo(() => {
    const map = new Map()
    for (const s of subtasks) {
      if (filter !== 'all' && s.status !== filter) continue
      if (!map.has(s.input_key)) map.set(s.input_key, [])
      map.get(s.input_key).push(s)
    }
    return [...map.entries()]
  }, [subtasks, filter])

  const counts = useMemo(() => {
    const out = {}
    for (const s of subtasks) out[s.status] = (out[s.status] || 0) + 1
    return out
  }, [subtasks])

  return (
    <Section title="Sub-tareas" aside={<span className="muted">{subtasks.length} en total, agrupadas por archivo</span>}>
      <div className="chips" role="group" aria-label="Filtrar sub-tareas">
        <button type="button" className={`chip ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>
          Todas <span className="num">{subtasks.length}</span>
        </button>
        {Object.entries(SUBTASK_STATUS).map(([key, info]) =>
          counts[key] ? (
            <button key={key} type="button" className={`chip ${filter === key ? 'on' : ''}`} onClick={() => setFilter(key)}>
              {info.label} <span className="num">{counts[key]}</span>
            </button>
          ) : null,
        )}
      </div>

      <div className="table-wrap">
        <table className="table subtasks">
          <thead>
            <tr>
              <th>Operación</th>
              <th>Estado</th>
              <th>Worker</th>
              <th>Cola</th>
              <th className="right">Intento</th>
              <th>Progreso</th>
              <th>Inicio</th>
              <th className="right">Duración</th>
            </tr>
          </thead>
          {groups.map(([inputKey, list]) => (
            <tbody key={inputKey}>
              <tr className="file-row">
                <td colSpan={8}>
                  <span className="mono">{basename(inputKey)}</span>
                  <span className="muted small"> · {MEDIA_TYPES[list[0].media_type] ?? 'Tipo no soportado'} · {inputKey}</span>
                </td>
              </tr>
              {list.map((s) => (
                <SubTaskRow key={s.subtask_id} s={s} outputs={outputsBySubtask.get(s.subtask_id) ?? []} />
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </Section>
  )
}

// Extensión -> tipo de preview inline. Cualquier otra extensión (metadata
// JSON, etc.) se queda solo con el link de descarga.
const PREVIEW_KIND = {
  mp4: 'video', mkv: 'video', webm: 'video',
  mp3: 'audio', wav: 'audio', ogg: 'audio', m4a: 'audio',
  jpg: 'image', jpeg: 'image', png: 'image', webp: 'image',
}

function previewKind(key) {
  return PREVIEW_KIND[key.split('.').pop()?.toLowerCase()] ?? null
}

// Descarga + preview inline (bajo un <details>, para no cargar de entrada
// video/audio/imágenes pesados) de los output_keys de una sub-tarea. Las
// URLs son prefirmadas (bucket privado) y vienen de GET /api/cases/{id}/outputs.
function OutputFiles({ keys, outputs }) {
  const byKey = useMemo(() => new Map(outputs.map((o) => [o.key, o])), [outputs])

  return (
    <span className="muted small outputs">
      Salida:{' '}
      {keys.map((k) => {
        const output = byKey.get(k)
        if (!output) return <code key={k}>{basename(k)}</code> // todavía sin URL prefirmada
        const url = toDevS3Url(output.url)
        const kind = previewKind(k)
        return (
          <span key={k} className="output-file">
            <a href={url} download>{basename(k)}</a>
            {kind && (
              <details>
                <summary>Ver</summary>
                {kind === 'video' && <video controls src={url} />}
                {kind === 'audio' && <audio controls src={url} />}
                {kind === 'image' && <img src={url} alt={basename(k)} />}
              </details>
            )}
          </span>
        )
      })}
    </span>
  )
}

function SubTaskRow({ s, outputs }) {
  const running = s.status === 'running'
  return (
    <>
      <tr>
        <td>
          {OPERATIONS[s.operation] ?? '—'}
          <div className="mono muted small" title={s.subtask_id}>{shortId(s.subtask_id)}</div>
        </td>
        <td><StatusPill status={s.status} kind="subtask" /></td>
        <td className="mono small">{s.worker_id ?? '—'}</td>
        <td className="mono small">{s.queue ?? '—'}</td>
        <td className="right num">{s.attempt || '—'}</td>
        <td>
          {s.progress != null ? (
            <div className="mini-progress" title={`${s.progress} %`}>
              <span style={{ width: `${s.progress}%` }} className={running ? 'live' : ''} />
              <small className="num">{s.progress}%</small>
            </div>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
        <td className="small num">{formatTime(s.started_at)}</td>
        <td className="right small num">{s.started_at ? formatDuration(durationBetween(s.started_at, s.finished_at)) : '—'}</td>
      </tr>
      {(s.error || s.output_keys.length > 0) && (
        <tr className="sub-extra">
          <td colSpan={8}>
            {s.error && (
              <span className="err">
                <b>{ERROR_CODES[s.error.code] ?? s.error.code}:</b> {s.error.message}
              </span>
            )}
            {s.output_keys.length > 0 && <OutputFiles keys={s.output_keys} outputs={outputs} />}
          </td>
        </tr>
      )}
    </>
  )
}

// Reporte consolidado (results/{case_id}/report.json) vía URL GET prefirmada.
function Report({ caseId, status }) {
  const [state, setState] = useState({ loading: false })

  const open = async () => {
    setState({ loading: true })
    try {
      const { url } = await api.reportUrl(caseId)
      try {
        const res = await fetch(toDevS3Url(url))
        if (!res.ok) throw new Error(`S3 respondió ${res.status}`)
        setState({ url, report: await res.json() })
      } catch (err) {
        // El bucket solo permite CORS desde CloudFront (en dev pasa por el proxy de Vite).
        setState({ url, fetchError: err.message })
      }
    } catch (err) {
      setState({ error: err })
    }
  }

  const r = state.report
  return (
    <Section
      title="Reporte consolidado"
      aside={
        <span className="row-gap">
          {state.url && (
            <a className="btn btn-small" href={state.url} target="_blank" rel="noreferrer">
              Descargar report.json
            </a>
          )}
          <button type="button" className="btn btn-primary btn-small" onClick={open} disabled={state.loading}>
            {state.loading ? 'Cargando…' : r ? 'Actualizar' : 'Ver reporte'}
          </button>
        </span>
      }
    >
      {!r && !state.error && !state.fetchError && (
        <p className="muted">
          El caso terminó como <StatusPill status={status} />. El coordinador escribió el reporte en S3 al cerrar el barrier.
        </p>
      )}
      <ErrorBanner error={state.error} />
      {state.fetchError && (
        <p className="muted small">
          No se pudo leer el reporte desde el navegador ({state.fetchError}). Use el botón de descarga.
        </p>
      )}
      {r && (
        <div className="report">
          <p className="report-summary">{r.summary}</p>
          <dl className="facts">
            <div><dt>Archivos</dt><dd className="num">{r.file_count}</dd></div>
            <div><dt>Sub-tareas</dt><dd className="num">{r.subtask_count}</dd></div>
            <div><dt>Duración</dt><dd className="num">{formatDuration(r.duration_s)}</dd></div>
            <div><dt>Workers</dt><dd className="num">{r.workers_used.length}</dd></div>
          </dl>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Tipo</th>
                  <th>Operación</th>
                  <th className="right">Total</th>
                  <th className="right">Completadas</th>
                  <th className="right">Fallidas</th>
                  <th className="right">Canceladas</th>
                </tr>
              </thead>
              <tbody>
                {r.groups.map((g, i) => (
                  <tr key={i}>
                    <td>{MEDIA_TYPES[g.media_type] ?? 'No soportado'}</td>
                    <td>{OPERATIONS[g.operation] ?? '—'}</td>
                    <td className="right num">{g.total}</td>
                    <td className="right num">{g.completed}</td>
                    <td className="right num">{g.failed}</td>
                    <td className="right num">{g.cancelled}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">
            Workers responsables: {r.workers_used.map((w) => <code key={w}>{w}</code>)}
          </p>
        </div>
      )}
    </Section>
  )
}
