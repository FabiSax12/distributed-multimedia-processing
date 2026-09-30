// Cliente REST del coordinador. Un método por endpoint de shared/api.py.

import { API_BASE } from '../config.js'

export class ApiError extends Error {
  constructor(status, detail) {
    super(describeDetail(status, detail))
    this.status = status
    this.detail = detail
  }
}

function describeDetail(status, detail) {
  if (!detail) {
    if (status >= 500) return `El coordinador respondió ${status} sin detalle (¿está caído o no se puede alcanzar?)`
    return `Error HTTP ${status}`
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // Errores de validación de FastAPI: [{loc, msg, type}, ...]
    return detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join(' · ')
  }
  if (detail.missing) return `No existen en el dataset: ${detail.missing.join(', ')}`
  if (detail.message) return detail.message
  return JSON.stringify(detail)
}

async function request(method, path, body) {
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'Sin respuesta de la red: no se pudo contactar al coordinador.')
  }
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data)
  return data
}

export const api = {
  state: () => request('GET', '/api/state'),
  metrics: (minutes = 30) => request('GET', `/api/metrics?minutes=${minutes}`),
  dataset: (prefix = '') => request('GET', `/api/dataset?prefix=${encodeURIComponent(prefix)}`),
  listCases: (status) => request('GET', `/api/cases${status ? `?status=${status}` : ''}`),
  getCase: (id) => request('GET', `/api/cases/${encodeURIComponent(id)}`),
  createCase: (payload) => request('POST', '/api/cases', payload),
  cancelCase: (id) => request('DELETE', `/api/cases/${encodeURIComponent(id)}`),
  reportUrl: (id) => request('GET', `/api/cases/${encodeURIComponent(id)}/report`),
  caseOutputs: (id) => request('GET', `/api/cases/${encodeURIComponent(id)}/outputs`),
  uploadUrls: (filenames) => request('POST', '/api/uploads', { filenames }),
}

// En desarrollo (localhost) los buckets no aceptan CORS, así que las URLs
// prefirmadas de S3 se reescriben para pasar por el proxy de Vite:
//   https://dmp-dev-dataset.s3.amazonaws.com/uploads/x?firma
//   -> /__s3/dmp-dev-dataset/uploads/x?firma
// En producción (servido por CloudFront) se usan tal cual.
const S3_HOST = /^https:\/\/([a-z0-9.-]+)\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com(\/.*)$/

export function toDevS3Url(url) {
  if (!import.meta.env.DEV) return url
  const m = url.match(S3_HOST)
  return m ? `/__s3/${m[1]}${m[2]}` : url
}

// Sube un archivo directo a S3 con la URL PUT prefirmada que firmó el
// coordinador (los bytes no pasan por el coordinador). XHR en vez de fetch
// para poder reportar el progreso de subida.
//
// El body va SIN Content-Type: el coordinador firma con SigV2 sin ContentType,
// y en SigV2 el Content-Type forma parte de la firma. Si el navegador mandara
// "video/mp4" (lo que hace con un File), S3 respondería SignatureDoesNotMatch.
// `file.slice(0, size, '')` da el mismo contenido con tipo vacío, sin copiarlo.
export function putToS3(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', toDevS3Url(url))
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve()
      const code = xhr.responseText.match(/<Code>([^<]+)<\/Code>/)?.[1]
      reject(new Error(`S3 rechazó la subida de ${file.name} (${xhr.status}${code ? ` ${code}` : ''})`))
    }
    xhr.onerror = () =>
      reject(
        new Error(
          `No se pudo subir ${file.name} a S3. Si el panel no está en el dominio de CloudFront ni en "npm run dev", el bucket lo bloquea por CORS.`,
        ),
      )
    xhr.send(file.slice(0, file.size, ''))
  })
}
