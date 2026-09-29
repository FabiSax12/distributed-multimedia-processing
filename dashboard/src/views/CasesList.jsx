// Lista de casos: los abiertos y los últimos terminados que trae el estado en vivo.

import { useMemo, useState } from 'react'
import { Empty, Section, StackedProgress, StatusLegend, StatusPill } from '../components/ui.jsx'
import { useLiveState } from '../hooks/useLiveState.jsx'
import { navigate } from '../hooks/useHashRoute.js'
import { CASE_STATUS } from '../lib/domain.js'
import { durationBetween, formatDateTime, formatDuration, shortId } from '../lib/format.js'

export default function CasesList() {
  const { data } = useLiveState()
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')

  const cases = useMemo(() => {
    const list = (data?.cases ?? []).slice().sort((a, b) => b.case.case_id.localeCompare(a.case.case_id))
    const q = query.trim().toLowerCase()
    return list.filter(
      ({ case: c }) =>
        (filter === 'all' || c.status === filter) &&
        (!q || c.case_id.toLowerCase().includes(q) || (c.label || '').toLowerCase().includes(q)),
    )
  }, [data, filter, query])

  const counts = useMemo(() => {
    const out = {}
    for (const { case: c } of data?.cases ?? []) out[c.status] = (out[c.status] || 0) + 1
    return out
  }, [data])

  return (
    <Section
      title="Casos"
      aside={
        <a className="btn btn-primary" href="#/nuevo">
          Nuevo caso
        </a>
      }
    >
      <div className="toolbar">
        <div className="chips" role="group" aria-label="Filtrar por estado">
          <button type="button" className={`chip ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>
            Todos <span className="num">{data?.cases.length ?? 0}</span>
          </button>
          {Object.entries(CASE_STATUS).map(([key, info]) => (
            <button
              key={key}
              type="button"
              className={`chip ${filter === key ? 'on' : ''}`}
              onClick={() => setFilter(key)}
              disabled={!counts[key]}
            >
              {info.label} <span className="num">{counts[key] || 0}</span>
            </button>
          ))}
        </div>
        <input
          id="case-search"
          className="input search"
          type="search"
          placeholder="Buscar por etiqueta o ID"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <StatusLegend />

      {cases.length === 0 ? (
        <Empty title={data ? 'No hay casos que coincidan' : 'Cargando casos…'}>
          El estado en vivo incluye los casos abiertos y los 50 terminados más recientes.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Caso</th>
                <th>Estado</th>
                <th className="col-progress">Sub-tareas</th>
                <th className="right">Archivos</th>
                <th>Prioridad</th>
                <th>Origen</th>
                <th>Creado</th>
                <th className="right">Duración</th>
              </tr>
            </thead>
            <tbody>
              {cases.map(({ case: c, by_status }) => (
                <tr key={c.case_id} className="clickable" onClick={() => navigate(`/casos/${c.case_id}`)}>
                  <td>
                    <a href={`#/casos/${c.case_id}`} onClick={(e) => e.stopPropagation()}>
                      <strong>{c.label || 'Sin etiqueta'}</strong>
                    </a>
                    <div className="mono muted small">{shortId(c.case_id)}</div>
                  </td>
                  <td>
                    <StatusPill status={c.status} />
                    {c.cancel_requested && !['cancelled'].includes(c.status) && (
                      <div className="small muted">cancelación pedida</div>
                    )}
                  </td>
                  <td className="col-progress">
                    <StackedProgress byStatus={by_status} total={c.total} />
                    <span className="num small muted">
                      {c.total - c.pending_count}/{c.total}
                      {c.failed_count ? ` · ${c.failed_count} fallidas` : ''}
                    </span>
                  </td>
                  <td className="right num">{c.file_count}</td>
                  <td>{c.priority === 'alta' ? <span className="pill tone-accent">Alta</span> : 'Normal'}</td>
                  <td>{c.source === 'auto' ? 'Automático' : 'Manual'}</td>
                  <td className="small">{formatDateTime(c.created_at)}</td>
                  <td className="right num small">
                    {formatDuration(durationBetween(c.started_at || c.created_at, c.finished_at))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}
