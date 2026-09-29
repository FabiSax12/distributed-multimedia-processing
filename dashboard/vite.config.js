import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// En producción el panel lo sirve CloudFront y /api/* va al coordinador por el
// mismo dominio (mismo origen, sin CORS). En desarrollo, Vite hace de proxy de
// /api/* (REST y WebSocket) hacia VITE_PROXY_TARGET: la distribución de
// CloudFront ya desplegada (por defecto) o un coordinador local
// (http://localhost:8000).
//
// También hace de proxy de los buckets de S3 (/__s3/<bucket>/...): los buckets
// solo tienen CORS para el dominio de CloudFront, así que desde localhost el
// navegador no puede hacer PUT/GET directo con las URLs prefirmadas. Pasando por
// el proxy la petición es del mismo origen y S3 ve el Host correcto del bucket,
// que es lo que entra en la firma. Ver `toDevS3Url` en src/api/client.js.
const DEFAULT_TARGET = 'https://dydlv3ysi1cxu.cloudfront.net'
const DEFAULT_BUCKETS = 'dmp-dev-dataset,dmp-dev-results'

function proxyError(res, message) {
  if (!res || typeof res.writeHead !== 'function' || res.headersSent) return
  res.writeHead(502, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify({ detail: message }))
}

function s3Proxies(buckets) {
  return Object.fromEntries(
    buckets.map((bucket) => [
      `/__s3/${bucket}`,
      {
        target: `https://${bucket}.s3.amazonaws.com`,
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.slice(`/__s3/${bucket}`.length),
        configure: (proxy) => {
          // Sin Origin/Referer de localhost: S3 no evalúa CORS para esta petición.
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.removeHeader('origin')
            proxyReq.removeHeader('referer')
          })
          proxy.on('error', (err, _req, res) =>
            proxyError(res, `El proxy de Vite no pudo conectar con S3 (${bucket}): ${err.code || err.message}`),
          )
        },
      },
    ]),
  )
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_PROXY_TARGET || DEFAULT_TARGET
  const buckets = (env.VITE_S3_BUCKETS || DEFAULT_BUCKETS).split(',').map((b) => b.trim()).filter(Boolean)

  return {
    plugins: [react()],
    base: './',
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          secure: true,
          ws: true,
          // Si el coordinador no responde, Vite contesta un 500 vacío que en
          // el panel se ve como un error del backend. Respondemos 502 con un
          // detalle que dice qué pasó y hacia dónde apuntaba el proxy.
          configure: (proxy) => {
            proxy.on('error', (err, _req, res) =>
              proxyError(
                res,
                `El proxy de Vite no pudo conectar con el coordinador en ${target} (${err.code || err.message}). Revise VITE_PROXY_TARGET en dashboard/.env.local.`,
              ),
            )
          },
        },
        ...s3Proxies(buckets),
      },
    },
  }
})
