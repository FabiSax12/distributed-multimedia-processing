import ConnectionBadge from './components/ConnectionBadge.jsx'
import { useHashRoute } from './hooks/useHashRoute.js'
import { useLiveState } from './hooks/useLiveState.jsx'
import CaseDetail from './views/CaseDetail.jsx'
import CasesList from './views/CasesList.jsx'
import NewCase from './views/NewCase.jsx'
import Overview from './views/Overview.jsx'

const NAV = [
  { href: '#/', label: 'Sistema', match: (p) => p.length === 0 },
  { href: '#/casos', label: 'Casos', match: (p) => p[0] === 'casos' },
  { href: '#/nuevo', label: 'Nuevo caso', match: (p) => p[0] === 'nuevo' },
]

export default function App() {
  const { parts } = useHashRoute()
  const { data } = useLiveState()

  let view
  if (parts[0] === 'casos' && parts[1]) view = <CaseDetail key={parts[1]} caseId={parts[1]} />
  else if (parts[0] === 'casos') view = <CasesList />
  else if (parts[0] === 'nuevo') view = <NewCase />
  else view = <Overview />

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            <i /><i /><i /><i />
          </span>
          <span>
            <strong>Panel DMP</strong>
            <small>Procesamiento multimedia por casos</small>
          </span>
        </a>
        <nav className="nav">
          {NAV.map((n) => (
            <a key={n.href} href={n.href} className={n.match(parts) ? 'on' : ''}>
              {n.label}
            </a>
          ))}
        </nav>
        <ConnectionBadge />
      </header>
      <main className="main">{view}</main>
      <footer className="footer muted small">
        IC-6600 · TEC San Carlos
        {data?.generated_at && <> · snapshot del coordinador {new Date(data.generated_at).toLocaleTimeString('es-CR')}</>}
      </footer>
    </div>
  )
}
