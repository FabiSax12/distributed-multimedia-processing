import { useEffect, useRef } from 'react'

// setInterval que siempre llama a la versión más reciente del callback.
// `delay = null` lo pausa. Con `immediate` corre una vez al montar.
export function useInterval(callback, delay, { immediate = false } = {}) {
  const saved = useRef(callback)
  saved.current = callback

  useEffect(() => {
    if (delay == null) return undefined
    if (immediate) saved.current()
    const id = setInterval(() => saved.current(), delay)
    return () => clearInterval(id)
  }, [delay, immediate])
}
