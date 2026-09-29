// Gráfico de líneas en SVG plano (sin librerías) para la serie de carga.
// `series`: [{ key, label, color, values: number[] }], todas del mismo largo que `times`.

export default function LineChart({ times, series, max, unit = '', height = 160 }) {
  const width = 600
  const pad = { top: 10, right: 12, bottom: 22, left: 36 }
  const innerW = width - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const n = times.length

  if (n < 2) {
    return <div className="chart-empty">Juntando muestras… (el coordinador toma una cada 5 s)</div>
  }

  const dataMax = Math.max(1, ...series.flatMap((s) => s.values))
  const top = max ?? niceCeil(dataMax)
  const x = (i) => pad.left + (i / (n - 1)) * innerW
  const y = (v) => pad.top + innerH - (Math.min(v, top) / top) * innerH
  const ticks = [0, top / 2, top]

  const first = new Date(times[0])
  const last = new Date(times[n - 1])
  const fmt = (d) => d.toLocaleTimeString('es-CR', { hour: '2-digit', minute: '2-digit' })

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} />
            <text className="axis" x={pad.left - 6} y={y(t) + 4} textAnchor="end">
              {formatTick(t)}
              {unit}
            </text>
          </g>
        ))}
        <text className="axis" x={pad.left} y={height - 6}>
          {fmt(first)}
        </text>
        <text className="axis" x={width - pad.right} y={height - 6} textAnchor="end">
          {fmt(last)}
        </text>
        {series.map((s) => {
          const d = s.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
          const lastV = s.values[n - 1]
          return (
            <g key={s.key}>
              <path d={d} fill="none" stroke={s.color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
              <circle cx={x(n - 1)} cy={y(lastV)} r="3" fill={s.color} />
            </g>
          )
        })}
      </svg>
      <div className="legend">
        {series.map((s) => (
          <span key={s.key} className="legend-item">
            <i className="dot" style={{ background: s.color }} /> {s.label}
            <b className="num">
              {formatTick(s.values[n - 1])}
              {unit}
            </b>
          </span>
        ))}
      </div>
    </div>
  )
}

function niceCeil(v) {
  const pow = 10 ** Math.floor(Math.log10(v))
  const m = v / pow
  const nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10
  return nice * pow
}

function formatTick(v) {
  return Number.isInteger(v) ? v : v.toFixed(1)
}
