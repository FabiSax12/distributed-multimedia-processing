// Espejo en el panel de shared/routing.py y shared/states.py.

export const CASE_STATUS = {
  queued: { label: 'En cola', tone: 'neutral' },
  processing: { label: 'En progreso', tone: 'info' },
  retrying: { label: 'Reintentando', tone: 'warn' },
  completed: { label: 'Completado', tone: 'ok' },
  partially_completed: { label: 'Parcialmente completado', tone: 'warn' },
  failed: { label: 'Fallido', tone: 'bad' },
  cancelled: { label: 'Cancelado', tone: 'muted' },
}

export const TERMINAL_CASE = new Set(['completed', 'partially_completed', 'failed', 'cancelled'])

export const SUBTASK_STATUS = {
  pending: { label: 'Pendiente', tone: 'neutral' },
  assigned: { label: 'Asignado', tone: 'info' },
  running: { label: 'En ejecución', tone: 'info' },
  retrying: { label: 'Reintentando', tone: 'warn' },
  completed: { label: 'Completado', tone: 'ok' },
  failed: { label: 'Fallido', tone: 'bad' },
  cancelled: { label: 'Cancelado', tone: 'muted' },
}

// Orden en el que se apilan los segmentos de la barra de progreso de un caso.
export const SUBTASK_ORDER = ['completed', 'failed', 'cancelled', 'running', 'assigned', 'retrying', 'pending']

export const OPERATIONS = {
  video_convert: 'Conversión de video',
  audio_extract: 'Extracción de audio',
  video_thumbnail: 'Miniatura de video',
  audio_convert: 'Transcodificación de audio',
  image_thumbnail: 'Miniatura de imagen',
  metadata: 'Metadatos',
  lyrics: 'Letras',
  classify: 'Clasificación',
}

export const MEDIA_TYPES = { video: 'Video', audio: 'Audio', image: 'Imagen' }

export const ALLOWED_OPERATIONS = {
  video: ['video_convert', 'audio_extract', 'video_thumbnail', 'metadata', 'classify'],
  audio: ['audio_convert', 'metadata', 'lyrics', 'classify'],
  image: ['image_thumbnail', 'metadata', 'classify'],
}

export const DEFAULT_OPERATIONS = {
  video: ['video_convert', 'audio_extract', 'video_thumbnail'],
  audio: ['audio_convert', 'metadata'],
  image: ['image_thumbnail'],
}

const EXTENSIONS = {
  mp4: 'video', mkv: 'video', mov: 'video', avi: 'video', webm: 'video',
  mp3: 'audio', wav: 'audio', flac: 'audio', ogg: 'audio', m4a: 'audio', aac: 'audio',
  jpg: 'image', jpeg: 'image', png: 'image', webp: 'image',
}

export function detectMediaType(filename) {
  const ext = filename.split('.').pop()?.toLowerCase()
  return EXTENSIONS[ext] ?? null
}

export const POOLS = {
  video: { label: 'Video', instance: 'c7i-flex.large' },
  audio: { label: 'Audio', instance: 'm7i-flex.large' },
  metadatos: { label: 'Metadatos', instance: 't3.micro' },
}

// Orden de consumo de cada pool (POLL_ORDER en shared/routing.py).
export const POLL_ORDER = {
  video: ['video-alta', 'video-normal', 'audio-normal'],
  audio: ['audio-alta', 'audio-normal', 'metadatos-alta', 'metadatos-normal', 'video-normal'],
  metadatos: ['metadatos-alta', 'metadatos-normal', 'audio-normal'],
}

export const ERROR_CODES = {
  unsupported_format: 'Formato no soportado',
  corrupt_input: 'Archivo corrupto',
  processing_error: 'Error de procesamiento',
  external_api_error: 'Error de API externa',
  timeout: 'Tiempo agotado',
  retries_exhausted: 'Reintentos agotados (DLQ)',
  cancelled: 'Cancelado',
  internal: 'Error interno',
}
