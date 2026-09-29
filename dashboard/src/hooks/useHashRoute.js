// Ruteo por hash (#/casos/ID). CloudFront no reescribe rutas de la SPA (ver
// infra/modules/frontend), así que con hash routing cualquier recarga pide
// siempre index.html y nunca choca con /api/*.

import { useEffect, useState } from 'react'

function parse() {
  const path = window.location.hash.replace(/^#/, '') || '/'
  const parts = path.split('/').filter(Boolean)
  return { path, parts }
}

export function useHashRoute() {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const onChange = () => setRoute(parse())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function navigate(path) {
  window.location.hash = path
}
