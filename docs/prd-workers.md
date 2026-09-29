# PRD — Workers (pools video, audio y metadatos)

## Contexto

El coordinador ya planifica sub-tareas, las encola por tipo y prioridad, lleva
el barrier y escribe el reporte consolidado. Falta el lado que ejecuta: los
nodos worker. Hoy solo existe `coordinator/scripts/fake_worker.py`, que imita
el protocolo sin procesar nada. Este documento define el worker real.

Fuente de verdad: el PDF del proyecto (IC-6600, v2.0). Donde el PDF no dice
nada, manda lo que ya está en el repo (`shared/`, `infra/`, el coordinador).

## Lo que pide el PDF a los workers

| El PDF pide | Cómo lo cumple el worker |
|---|---|
| Ejecutar sub-tareas multimedia específicas | Las 8 operaciones de `shared.routing.Operation` |
| Reportar estado y resultados de cada sub-tarea al coordinador | Un `ResultMessage` por cambio de estado en la cola `resultados` |
| Comunicarse con el coordinador y el monitoreo | Cola `resultados` + heartbeat cada 5 s en la tabla `Workers` |
| Pools especializados y justificar el balanceo | Un solo código; `POOL` elige las colas y el orden de `POLL_ORDER` |
| Monitoreo de CPU, memoria y carga por worker | `cpu_percent`, `mem_percent` y `active_subtasks` en el heartbeat |
| Porcentaje de progreso "si aplica" | ffmpeg reporta `out_time_us`; se publica `running` con `progress` |
| Estados pendiente → asignado → en ejecución → completado/fallido | `assigned`, `running`, `retrying`, `completed`, `failed`, `cancelled` |
| Al menos 3 workers en máquinas distintas | 3 EC2 (una por pool), ya creadas por Terraform |

## Alcance

### Dentro

1. Paquete `workers/` (miembro del workspace de `uv`, layout `src/` igual que
   el coordinador).
2. Runner: consume colas en el orden de `POLL_ORDER[POOL]`, con N hilos por
   proceso (`WORKER_CONCURRENCY`, por defecto 2 en video/audio y 4 en
   metadatos, que está ligado a red y no a CPU).
3. Protocolo, idéntico al de `fake_worker.py`:
   - cancelación: `GetItem` consistente en `Cases.cancel_requested` antes de
     empezar; si está cancelado se reporta `cancelled` sin procesar;
   - `retrying` cuando `ApproximateReceiveCount > 1`;
   - `assigned` → `running` (con progreso) → un único estado terminal;
   - el mensaje de trabajo se borra **después** de publicar el terminal.
4. Operaciones:

   | Operación | Pool | Qué hace | Salida |
   |---|---|---|---|
   | `video_convert` | video | ffmpeg a `target_format` (mp4 por defecto) | `<nombre>.<fmt>` |
   | `audio_extract` | video | pista de audio a `audio_format` (mp3) | `<nombre>.<fmt>` |
   | `video_thumbnail` | video | un frame al 10 % de la duración | `thumbnail.jpg` |
   | `audio_convert` | audio | transcodifica a `target_format` (mp3) | `<nombre>.<fmt>` |
   | `image_thumbnail` | audio | escala a `thumbnail_width` (320 px) | `thumbnail.jpg` |
   | `metadata` | metadatos | ffprobe + catálogo MusicBrainz para audio | `metadata.json` |
   | `lyrics` | metadatos | letra desde lyrics.ovh | `lyrics.txt` + `lyrics.json` |
   | `classify` | metadatos | categoría y carpeta sugerida según ffprobe | `classification.json` |

5. Errores → `ErrorCode`:
   - ffprobe no lee el archivo o no existe en el dataset → `corrupt_input`;
   - ffmpeg termina con error → `processing_error`;
   - la operación supera su tiempo → `timeout`;
   - `params` pide un formato que no se soporta → `unsupported_format`;
   - la API de letras no responde → `external_api_error`;
   - cualquier otra excepción (S3 caído, bug): **no** se publica terminal ni se
     borra el mensaje; SQS lo reintenta y, al tercer intento, la DLQ lo
     entrega al coordinador como `retries_exhausted`.
6. Tiempo máximo por operación = `VisibilityTimeout` de la cola de origen menos
   30 s. Así el worker reporta `timeout` antes de que SQS vuelva a entregar el
   mensaje a otro worker (video 900 s, audio 300 s, metadatos 120 s).
7. Despliegue: `deploy/worker.service` (systemd) y pasos por SSM en
   `workers/README.md`, igual que el coordinador.
8. Prueba local de punta a punta sin AWS ni Docker:
   `workers/scripts/local_e2e.py` levanta moto en proceso, el coordinador real
   y un worker real por pool, y procesa un caso heterogéneo generado con
   ffmpeg.

### Fuera

- Autoescalado: el PDF acepta "visualizar o reaccionar"; se escala a mano con
  `worker_count` en Terraform.
- Cancelar una sub-tarea que ya está corriendo: se revisa la cancelación antes
  de empezar. Las que ya arrancaron terminan y el barrier igual cierra.
- Extender el visibility timeout durante la ejecución: el tope por operación
  lo hace innecesario.

## Cambio de infraestructura necesario

`infra/modules/compute/main.tf` instalaba ffmpeg solo en video y audio. El
pool de metadatos lo necesita por dos razones que ya están en el repo:

1. `Operation.METADATA` es "ffprobe + catálogos externos" (`shared/routing.py`).
2. `POLL_ORDER[metadatos]` incluye `audio-normal`, donde viven
   `audio_convert` e `image_thumbnail`, que usan ffmpeg.

Se cambia `install_ffmpeg = false` → `true` en el pool de metadatos. Como el
`user_data` solo corre en el primer arranque, la instancia ya creada necesita
`terraform apply -replace` o instalar ffmpeg a mano (ver `workers/README.md`).

## Criterios de aceptación

- `uv run --package workers python -m pytest workers/tests` pasa sin tocar AWS.
- `uv run ruff check workers` y `uv run ruff format --check workers` limpios.
- `local_e2e.py` cierra un caso heterogéneo (video + audio + imagen + un
  archivo corrupto) como `partially_completed`, con reporte en S3 y los tres
  pools en `workers_used`.
- En AWS, con las 3 instancias desplegadas, `GET /api/state` muestra los 3
  workers `alive` con CPU y memoria reales.

## Estrategia de desarrollo

TDD por unidad de trabajo, con el mismo estilo de tests del coordinador
(pytest plano, `moto` para AWS, docstring en español por módulo). Cada commit
junta el test y la implementación de una unidad:

1. Paquete y configuración.
2. Poller con prioridad y heartbeat.
3. Operaciones ffmpeg (video, audio, imagen).
4. Operaciones de metadatos (catálogo, letras, clasificación).
5. Procesamiento de un mensaje (protocolo completo).
6. Entrypoint, systemd, README y cambio de infra.
7. Prueba local de punta a punta.
