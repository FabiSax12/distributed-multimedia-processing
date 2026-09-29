# Dashboard

SPA estática en React 18 + Vite (JavaScript, sin router ni librerías de UI).
CloudFront la sirve desde el bucket del sitio y reenvía `/api/*` al coordinador,
así que el panel siempre habla con la API por el **mismo origen**: sin CORS y
sin conocer la IP del EC2.

## Vistas (hash routing)

| Ruta | Qué muestra |
|---|---|
| `#/` | KPIs, alertas del balanceador, workers por pool (CPU, memoria, slots), colas SQS, casos en curso y serie de carga de 30 min |
| `#/casos` | Casos abiertos y los 50 terminados más recientes, filtro por estado |
| `#/casos/{id}` | Barrier (`pending_count`), sub-tareas por archivo, cancelar, reporte consolidado |
| `#/nuevo` | Caso desde el dataset, subiendo archivos (URL PUT prefirmada) o generación automática por carpeta/metadatos |

Se usa hash routing porque CloudFront no tiene reescritura de rutas para la SPA
(ver `infra/modules/frontend/main.tf`).

## Estado en vivo

`src/hooks/useLiveState.jsx` abre `WS /api/ws` y recibe `{"type":"state","data":StateResponse}`
(ignora `{"type":"ping"}`). Mientras el socket no está abierto hace polling de
`GET /api/state` cada 2 s. Reconecta con backoff 1 s → 2 s → … → 30 s, y si pasan
60 s sin ningún mensaje (el ping llega cada 25 s) da el socket por muerto.

La URL del socket se deriva del origen (`https://<cloudfront>` → `wss://<cloudfront>/api/ws`).
Se puede forzar con `VITE_WS_URL` (ver `.env.example`).

## Desarrollo

```bash
cd dashboard
npm install
cp .env.example .env.local   # elegir VITE_PROXY_TARGET
npm run dev                  # http://localhost:5173
```

Vite hace de proxy de `/api/*` (REST y WebSocket) hacia `VITE_PROXY_TARGET`:

- `https://dydlv3ysi1cxu.cloudfront.net` (por defecto): coordinador desplegado; CloudFront agrega `X-Origin-Verify`.
- `http://localhost:8000`: solo si el coordinador está corriendo en la laptop (`uvicorn`). Si no hay nada
  escuchando ahí, el panel muestra "El proxy de Vite no pudo conectar con el coordinador… (ECONNREFUSED)".

Los buckets del dataset y de resultados solo tienen CORS para el dominio de
CloudFront. Para que la subida de archivos y la lectura del reporte funcionen desde
`localhost`, Vite también hace de proxy de S3: en desarrollo, `toDevS3Url`
(`src/api/client.js`) reescribe `https://<bucket>.s3.amazonaws.com/...` a
`/__s3/<bucket>/...`. Los buckets se configuran con `VITE_S3_BUCKETS`.

Las URLs PUT que firma el coordinador son SigV2 sin `ContentType`, y en SigV2 el
`Content-Type` entra en la firma: por eso `putToS3` manda el archivo sin ese header
(con otro valor S3 responde `403 SignatureDoesNotMatch`).

## Publicar

```bash
npm run deploy   # build + s3 sync a dmp-dev-site + invalidación de CloudFront
```

Lee el bucket y la distribución de `terraform output` en `infra/`, o de
`SITE_BUCKET` / `DISTRIBUTION_ID` si están definidas. Necesita AWS CLI con el
perfil del proyecto.
