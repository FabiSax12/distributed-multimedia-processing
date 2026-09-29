// Envío de casos. Tres formas:
//  1. Desde el dataset: elegir archivos que ya están en S3 (GET /api/dataset).
//  2. Subir archivos: POST /api/uploads da URLs PUT prefirmadas, el navegador
//     sube cada archivo directo a S3 y después se crea el caso con esas claves.
//  3. Generación automática: agrupar el dataset por carpeta o por un campo de
//     metadatos y crear un caso por grupo, todos en paralelo.
// En los tres, la operación por archivo es opcional: sin elegir, decide el
// coordinador (DEFAULT_OPERATIONS de shared/routing.py).

import { useEffect, useMemo, useState } from 'react'
import { api, putToS3 } from '../api/client.js'
import { Empty, ErrorBanner, Section } from '../components/ui.jsx'
import { navigate } from '../hooks/useHashRoute.js'
import { ALLOWED_OPERATIONS, DEFAULT_OPERATIONS, MEDIA_TYPES, OPERATIONS, detectMediaType } from '../lib/domain.js'
import { basename, formatBytes, shortId } from '../lib/format.js'

const TABS = [
  { key: 'dataset', label: 'Desde el dataset' },
  { key: 'upload', label: 'Subir archivos' },
  { key: 'auto', label: 'Generación automática' },
]

export default function NewCase() {
  const [tab, setTab] = useState('dataset')
  return (
    <div className="stack-lg">
      <header className="page-head">
        <h1>Nuevo caso</h1>
        <p className="muted">
          Un caso agrupa uno o varios archivos, del mismo tipo o mezclados. El coordinador lo descompone en sub-tareas y
          las encola por tipo y prioridad.
        </p>
      </header>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`tab ${tab === t.key ? 'on' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'dataset' && <FromDataset />}
      {tab === 'upload' && <FromUpload />}
      {tab === 'auto' && <AutoGenerate />}
    </div>
  )
}

// ---- Piezas comunes -------------------------------------------------------- //

function useDataset() {
  const [prefix, setPrefix] = useState('')
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async (p = prefix) => {
    setLoading(true)
    setError(null)
    try {
      setEntries(await api.dataset(p))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { prefix, setPrefix, entries, error, loading, load }
}

function CaseOptions({ label, setLabel, priority, setPriority, showLabel = true }) {
  return (
    <div className="form-row">
      {showLabel && (
        <label className="field">
          <span>Etiqueta del caso</span>
          <input
            id="case-label"
            className="input"
            maxLength={200}
            placeholder="p. ej. Concierto 12-sep, cámara 2"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
      )}
      <fieldset className="field">
        <span>Prioridad</span>
        <div className="segmented">
          <label className={priority === 'normal' ? 'on' : ''}>
            <input type="radio" name="priority" value="normal" checked={priority === 'normal'} onChange={() => setPriority('normal')} />
            Normal
          </label>
          <label className={priority === 'alta' ? 'on' : ''}>
            <input type="radio" name="priority" value="alta" checked={priority === 'alta'} onChange={() => setPriority('alta')} />
            Alta
          </label>
        </div>
      </fieldset>
    </div>
  )
}

// Elección de operaciones para un archivo. `value = null` = automático.
function OperationPicker({ mediaType, value, onChange }) {
  if (!mediaType) {
    return <span className="small tone-text-bad">Formato no soportado: nacerá como sub-tarea fallida</span>
  }
  const selected = value ?? []
  const toggle = (op) => {
    const next = selected.includes(op) ? selected.filter((o) => o !== op) : [...selected, op]
    onChange(next.length ? next : null)
  }
  return (
    <div className="ops">
      {ALLOWED_OPERATIONS[mediaType].map((op) => {
        const on = selected.includes(op)
        const auto = !value && DEFAULT_OPERATIONS[mediaType].includes(op)
        return (
          <button
            key={op}
            type="button"
            className={`op ${on ? 'on' : ''} ${auto ? 'auto' : ''}`}
            onClick={() => toggle(op)}
            title={auto ? 'La elegiría el coordinador (automático)' : ''}
          >
            {OPERATIONS[op]}
          </button>
        )
      })}
      {!value && <span className="small muted">automático</span>}
    </div>
  )
}

function ResultBanner({ result }) {
  if (!result) return null
  return (
    <div className="banner tone-ok">
      <span>
        Caso <b className="mono">{shortId(result.case_id)}</b> creado: {result.file_count} archivos, {result.subtask_count}{' '}
        sub-tareas{result.prefailed_count ? `, ${result.prefailed_count} fallidas por formato no soportado` : ''}.
      </span>
      <a className="btn btn-small" href={`#/casos/${result.case_id}`}>
        Ver caso
      </a>
    </div>
  )
}

// ---- 1. Desde el dataset --------------------------------------------------- //

function FromDataset() {
  const ds = useDataset()
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [selected, setSelected] = useState({}) // input_key -> operations | null
  const [label, setLabel] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (ds.entries ?? []).filter(
      (e) =>
        (typeFilter === 'all' || (e.media_type ?? 'none') === typeFilter) &&
        (!q || e.input_key.toLowerCase().includes(q)),
    )
  }, [ds.entries, query, typeFilter])

  const byKey = useMemo(() => new Map((ds.entries ?? []).map((e) => [e.input_key, e])), [ds.entries])
  const selectedKeys = Object.keys(selected)
  const mix = new Set(selectedKeys.map((k) => byKey.get(k)?.media_type ?? 'none'))

  const toggle = (key) =>
    setSelected((s) => {
      const next = { ...s }
      if (key in next) delete next[key]
      else next[key] = null
      return next
    })

  const allVisibleSelected = visible.length > 0 && visible.every((e) => e.input_key in selected)
  const toggleAll = () =>
    setSelected((s) => {
      const next = { ...s }
      for (const e of visible) {
        if (allVisibleSelected) delete next[e.input_key]
        else if (!(e.input_key in next)) next[e.input_key] = null
      }
      return next
    })

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await api.createCase({
        files: selectedKeys.map((k) => ({ input_key: k, operations: selected[k] })),
        priority,
        label: label.trim() || null,
        source: 'manual',
      })
      navigate(`/casos/${res.case_id}`)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="stack-lg">
      <Section
        title="Archivos del dataset"
        aside={
          <span className="row-gap">
            <input
              id="dataset-prefix"
              className="input"
              placeholder="Prefijo (p. ej. batch1/)"
              value={ds.prefix}
              onChange={(e) => ds.setPrefix(e.target.value)}
            />
            <button type="button" className="btn btn-small" onClick={() => ds.load()} disabled={ds.loading}>
              {ds.loading ? 'Cargando…' : 'Listar'}
            </button>
          </span>
        }
      >
        <ErrorBanner error={ds.error} onRetry={() => ds.load()} />
        <div className="toolbar">
          <div className="chips">
            {['all', 'video', 'audio', 'image', 'none'].map((t) => (
              <button key={t} type="button" className={`chip ${typeFilter === t ? 'on' : ''}`} onClick={() => setTypeFilter(t)}>
                {t === 'all' ? 'Todos' : t === 'none' ? 'No soportados' : MEDIA_TYPES[t]}
              </button>
            ))}
          </div>
          <input
            id="dataset-search"
            className="input search"
            type="search"
            placeholder="Filtrar por nombre"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {ds.entries && visible.length === 0 ? (
          <Empty title="No hay archivos que coincidan" />
        ) : (
          <div className="table-wrap scroll-y">
            <table className="table compact">
              <thead>
                <tr>
                  <th className="col-check">
                    <input type="checkbox" aria-label="Seleccionar visibles" checked={allVisibleSelected} onChange={toggleAll} />
                  </th>
                  <th>Archivo</th>
                  <th>Tipo</th>
                  <th className="right">Tamaño</th>
                  <th>Metadatos</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((e) => (
                  <tr key={e.input_key} className="clickable" onClick={() => toggle(e.input_key)}>
                    <td className="col-check">
                      <input
                        type="checkbox"
                        aria-label={`Seleccionar ${e.input_key}`}
                        checked={e.input_key in selected}
                        onChange={() => toggle(e.input_key)}
                        onClick={(ev) => ev.stopPropagation()}
                      />
                    </td>
                    <td className="mono small">{e.input_key}</td>
                    <td>{e.media_type ? MEDIA_TYPES[e.media_type] : <span className="tone-text-bad">No soportado</span>}</td>
                    <td className="right num small">{formatBytes(e.size_bytes)}</td>
                    <td className="small muted meta-cell">{metaPreview(e.metadata)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section
        title={`Caso a enviar · ${selectedKeys.length} archivos`}
        aside={
          selectedKeys.length > 0 && (
            <span className={`pill ${mix.size > 1 ? 'tone-accent' : 'tone-neutral'}`}>
              {mix.size > 1 ? 'Heterogéneo' : 'Homogéneo'}
            </span>
          )
        }
      >
        {selectedKeys.length === 0 ? (
          <p className="muted">Marque archivos en la tabla de arriba.</p>
        ) : (
          <ul className="picked">
            {selectedKeys.map((k) => (
              <li key={k}>
                <span className="mono small picked-name" title={k}>{basename(k)}</span>
                <OperationPicker
                  mediaType={byKey.get(k)?.media_type ?? detectMediaType(k)}
                  value={selected[k]}
                  onChange={(ops) => setSelected((s) => ({ ...s, [k]: ops }))}
                />
                <button type="button" className="btn-icon" aria-label={`Quitar ${k}`} onClick={() => toggle(k)}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <CaseOptions label={label} setLabel={setLabel} priority={priority} setPriority={setPriority} />
        <ErrorBanner error={error} />
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy || selectedKeys.length === 0}>
            {busy ? 'Enviando…' : 'Enviar caso'}
          </button>
        </div>
      </Section>
    </form>
  )
}

function metaPreview(meta) {
  const entries = Object.entries(meta || {})
  if (!entries.length) return '—'
  return entries
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' · ')
}

// ---- 2. Subir archivos ----------------------------------------------------- //

function FromUpload() {
  const [files, setFiles] = useState([]) // [{ file, ops, progress, status }]
  const [label, setLabel] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const add = (list) => {
    setResult(null)
    setFiles((prev) => [
      ...prev,
      ...Array.from(list).map((file) => ({ file, ops: null, progress: 0, status: 'listo' })),
    ])
  }

  const patch = (i, changes) => setFiles((prev) => prev.map((f, j) => (j === i ? { ...f, ...changes } : f)))

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // 1) URLs PUT prefirmadas (máx. 200 por pedido, validado por el coordinador).
      const { urls } = await api.uploadUrls(files.map((f) => f.file.name))
      // 2) Subida directa a S3, en paralelo.
      await Promise.all(
        urls.map(async (u, i) => {
          patch(i, { status: 'subiendo' })
          await putToS3(u.url, files[i].file, (p) => patch(i, { progress: p }))
          patch(i, { status: 'subido', progress: 100 })
        }),
      )
      // 3) Caso con las claves recién subidas.
      const res = await api.createCase({
        files: urls.map((u, i) => ({ input_key: u.input_key, operations: files[i].ops })),
        priority,
        label: label.trim() || null,
        source: 'manual',
      })
      setResult(res)
      setFiles([])
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="stack-lg">
      <Section title="Archivos locales">
        <label
          className="dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            add(e.dataTransfer.files)
          }}
        >
          <input id="upload-files" type="file" multiple onChange={(e) => add(e.target.files)} disabled={busy} />
          <strong>Arrastre archivos aquí o haga clic para elegirlos</strong>
          <span className="muted small">
            Se suben directo al bucket del dataset con una URL prefirmada; los bytes no pasan por el coordinador.
          </span>
        </label>

        {files.length > 0 && (
          <ul className="picked">
            {files.map((f, i) => (
              <li key={`${f.file.name}-${i}`}>
                <span className="mono small picked-name" title={f.file.name}>
                  {f.file.name} <span className="muted">· {formatBytes(f.file.size)}</span>
                </span>
                {busy ? (
                  <div className="mini-progress wide">
                    <span style={{ width: `${f.progress}%` }} className="live" />
                    <small className="num">{f.status} {f.progress}%</small>
                  </div>
                ) : (
                  <OperationPicker mediaType={detectMediaType(f.file.name)} value={f.ops} onChange={(ops) => patch(i, { ops })} />
                )}
                {!busy && (
                  <button
                    type="button"
                    className="btn-icon"
                    aria-label={`Quitar ${f.file.name}`}
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
        <CaseOptions label={label} setLabel={setLabel} priority={priority} setPriority={setPriority} />
        <ErrorBanner error={error} />
        <ResultBanner result={result} />
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy || files.length === 0 || files.length > 200}>
            {busy ? 'Subiendo y enviando…' : `Subir ${files.length} y enviar caso`}
          </button>
          {files.length > 200 && <span className="small tone-text-bad">Máximo 200 archivos por subida.</span>}
        </div>
      </Section>
    </form>
  )
}

// ---- 3. Generación automática --------------------------------------------- //

function AutoGenerate() {
  const ds = useDataset()
  const [criterion, setCriterion] = useState('folder')
  const [metaKey, setMetaKey] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState([])

  const metaKeys = useMemo(() => {
    const keys = new Set()
    for (const e of ds.entries ?? []) Object.keys(e.metadata || {}).forEach((k) => keys.add(k))
    return [...keys].sort()
  }, [ds.entries])

  const groups = useMemo(() => {
    const map = new Map()
    for (const e of ds.entries ?? []) {
      let key
      if (criterion === 'folder') {
        const i = e.input_key.lastIndexOf('/')
        key = i >= 0 ? e.input_key.slice(0, i + 1) : '(raíz)'
      } else {
        const v = e.metadata?.[metaKey]
        if (v == null) continue
        key = `${metaKey} = ${typeof v === 'object' ? JSON.stringify(v) : v}`
      }
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(e)
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [ds.entries, criterion, metaKey])

  const run = async () => {
    setBusy(true)
    setResults(groups.map(([name]) => ({ name, state: 'enviando' })))
    // Todos los casos salen a la vez: así se ve la concurrencia entre casos.
    await Promise.all(
      groups.map(async ([name, entries], i) => {
        try {
          const res = await api.createCase({
            files: entries.slice(0, 1000).map((e) => ({ input_key: e.input_key })),
            priority,
            label: `auto · ${name}`,
            source: 'auto',
          })
          setResults((r) => r.map((x, j) => (j === i ? { name, state: 'ok', res } : x)))
        } catch (err) {
          setResults((r) => r.map((x, j) => (j === i ? { name, state: 'error', error: err.message } : x)))
        }
      }),
    )
    setBusy(false)
  }

  return (
    <div className="stack-lg">
      <Section title="Criterio de agrupación">
        <p className="muted">
          Agrupa los archivos del dataset y crea un caso por grupo, todos al mismo tiempo. El coordinador decide las
          operaciones de cada archivo según su tipo.
        </p>
        <div className="form-row">
          <fieldset className="field">
            <span>Agrupar por</span>
            <div className="segmented">
              <label className={criterion === 'folder' ? 'on' : ''}>
                <input type="radio" name="criterion" checked={criterion === 'folder'} onChange={() => setCriterion('folder')} />
                Carpeta
              </label>
              <label className={criterion === 'meta' ? 'on' : ''}>
                <input
                  type="radio"
                  name="criterion"
                  checked={criterion === 'meta'}
                  onChange={() => setCriterion('meta')}
                  disabled={!metaKeys.length}
                />
                Campo de metadatos
              </label>
            </div>
          </fieldset>
          {criterion === 'meta' && (
            <label className="field">
              <span>Campo</span>
              <select id="meta-key" className="input" value={metaKey} onChange={(e) => setMetaKey(e.target.value)}>
                <option value="">Elegir…</option>
                {metaKeys.map((k) => (
                  <option key={k} value={k}>{k}</option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            <span>Prefijo</span>
            <span className="row-gap">
              <input
                id="auto-prefix"
                className="input"
                placeholder="todo el bucket"
                value={ds.prefix}
                onChange={(e) => ds.setPrefix(e.target.value)}
              />
              <button type="button" className="btn btn-small" onClick={() => ds.load()} disabled={ds.loading}>
                {ds.loading ? 'Cargando…' : 'Listar'}
              </button>
            </span>
          </label>
        </div>
        <CaseOptions showLabel={false} priority={priority} setPriority={setPriority} />
        <ErrorBanner error={ds.error} onRetry={() => ds.load()} />
      </Section>

      <Section
        title={`Vista previa · ${groups.length} casos`}
        aside={
          <button type="button" className="btn btn-primary" onClick={run} disabled={busy || groups.length === 0}>
            {busy ? 'Enviando…' : `Crear ${groups.length} casos en paralelo`}
          </button>
        }
      >
        {groups.length === 0 ? (
          <Empty title={ds.entries ? 'No hay grupos con este criterio' : 'Cargando dataset…'} />
        ) : (
          <div className="table-wrap scroll-y">
            <table className="table compact">
              <thead>
                <tr>
                  <th>Grupo</th>
                  <th className="right">Archivos</th>
                  <th>Composición</th>
                  <th>Resultado</th>
                </tr>
              </thead>
              <tbody>
                {groups.map(([name, entries], i) => {
                  const r = results[i]?.name === name ? results[i] : null
                  return (
                    <tr key={name}>
                      <td className="mono small">{name}</td>
                      <td className="right num">{entries.length}</td>
                      <td className="small">{composition(entries)}</td>
                      <td className="small">
                        {!r && <span className="muted">—</span>}
                        {r?.state === 'enviando' && <span className="muted">enviando…</span>}
                        {r?.state === 'ok' && (
                          <a href={`#/casos/${r.res.case_id}`}>
                            {shortId(r.res.case_id)} · {r.res.subtask_count} sub-tareas
                          </a>
                        )}
                        {r?.state === 'error' && <span className="tone-text-bad">{r.error}</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}

function composition(entries) {
  const count = {}
  for (const e of entries) {
    const t = e.media_type ?? 'none'
    count[t] = (count[t] || 0) + 1
  }
  const parts = Object.entries(count).map(([t, n]) => `${n} ${t === 'none' ? 'no soportados' : MEDIA_TYPES[t].toLowerCase()}`)
  return `${parts.join(' · ')}${Object.keys(count).length > 1 ? ' — heterogéneo' : ' — homogéneo'}`
}
