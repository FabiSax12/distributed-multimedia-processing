# Coordinador

Nodo coordinador de la plataforma de procesamiento multimedia distribuido (IC-6600).
Planifica sub-tareas a partir de casos, las encola en SQS, consume resultados,
aplica el barrier de cierre y expone la API REST que consume el panel.

## Modelo de concurrencia

Un solo proceso `uvicorn --workers 1` (ver `deploy/coordinator.service`). Dentro
de ese proceso, varios hilos de Python:

- `results-consumer-0..N` (`RESULTS_CONSUMER_THREADS`, default 4): consumen la
  cola de resultados con `SqsConsumerThread` + el handler de
  `consumers/results.py`. Varios hilos contra la MISMA cola está bien: SQS
  reparte los mensajes entre receivers concurrentes.
- `dlq-consumer`: consume la DLQ con el handler de `consumers/dlq.py`.
- `sweeper-loop`: corre `barrier/sweeper.run_sweep` cada `SWEEP_INTERVAL_S`.
- `snapshot-loop`: corre `monitor/snapshot.run_snapshot_once` cada
  `SNAPSHOT_INTERVAL_S` y cachea el resultado en memoria.
- `samples-loop`: corre `monitor/samples.take_sample_once` cada
  `SAMPLE_INTERVAL_S` y flushea a S3 cada `METRICS_FLUSH_S`.

Todos I/O-bound: pasan casi todo su tiempo bloqueados en llamadas de red de
boto3 (SQS long-polling, Scans/Queries de DynamoDB, Get/PutObject de S3), que
liberan el GIL mientras esperan. Por eso hilos alcanzan y no hace falta
`multiprocessing` ni un framework de tareas aparte. Un solo proceso además
mantiene simple el estado compartido en memoria: el snapshot de
`monitor/snapshot.py` y el ring buffer de `monitor/samples.py` son variables
module-level protegidas por un `threading.Lock`, sin necesitar IPC ni un
backend externo (Redis, etc.) solo para esto.

Los endpoints de la API que llaman a boto3 se declaran `def` (no `async def`)
a propósito: FastAPI los corre en su threadpool en vez del loop de asyncio,
así una llamada bloqueante a DynamoDB/S3 no traba el resto de la API.

Al apagar (`SIGTERM`/shutdown de systemd), el `lifespan` de `main.py` dispara
un `threading.Event` compartido y hace `join(timeout=10s)` de cada hilo. Los
consumers de SQS pueden tardar hasta `wait_time` (20s) en notar el evento si
están a mitad de un `ReceiveMessage` de long-polling — son `daemon=True`, así
que el proceso igual termina, pero un shutdown "limpio" puede tardar unos
segundos más que el timeout de systemd por defecto. Antes de esperar a los
hilos, también se cierran (código `1001`) todas las conexiones WebSocket
abiertas de `/api/ws` (ver abajo).

## Estado en vivo (WebSocket)

`WS /api/ws` empuja el mismo `StateResponse` que arma `snapshot-loop` cada
`SNAPSHOT_INTERVAL_S`, sin que el panel tenga que hacer polling. `GET
/api/state` sigue existiendo y el panel debería usarlo como respaldo mientras
el socket esté caído (ver "Qué debe hacer el dashboard" más abajo).

- **URL**: `wss://<dominio-cloudfront>/api/ws` en producción (el comportamiento
  `/api/*` de CloudFront ya reenvía los headers del upgrade de WebSocket y
  agrega `X-Origin-Verify`, igual que para las rutas REST); `ws://localhost:8000/api/ws`
  corriendo el coordinador en local.

- **Formato de los mensajes**, dos tipos de objeto JSON distinguidos por
  `type`:

  ```json
  {"type": "state", "data": <StateResponse serializado, igual que GET /api/state>}
  {"type": "ping"}
  ```

  El sobre `{"type": "state", "data": ...}` se arma una sola vez en
  `Broadcaster.publish` (serializando el `StateResponse` una sola vez); cada
  cliente conectado recibe ese mismo texto tal cual, sin que el servidor lo
  vuelva a parsear/reserializar por conexión.

- **Puente hilo → event loop** (`monitor/broadcaster.py`): `snapshot-loop`
  corre en un hilo de Python, no en una corrutina, así que no puede tocar un
  `WebSocket` ni un `asyncio.Queue` directamente (no son thread-safe). El
  `Broadcaster` guarda el event loop real (`asyncio.get_running_loop()`,
  capturado en el `lifespan` antes de arrancar los hilos) y, cuando
  `snapshot-loop` llama a `publish()`, agenda la entrega real con
  `loop.call_soon_threadsafe(...)`. Solo código que corre dentro del loop
  toca las colas o los sockets.

  Por qué la cola por cliente es de tamaño 1: cada conexión tiene su propia
  `asyncio.Queue(maxsize=1)`. Si llega un estado nuevo antes de que ese
  cliente haya leído el anterior, el viejo se descarta y se deja el
  nuevo — un cliente lento (pestaña en segundo plano, conexión con latencia
  alta) nunca acumula un backlog de estados viejos; siempre termina viendo el
  último estado disponible, nunca uno atrasado, y nunca bloquea el envío a los
  demás clientes conectados.

  `publish()` también descarta una publicación si el contenido no cambió
  respecto al último envío, ignorando `generated_at` (que cambia en cada
  vuelta del snapshot aunque nada más lo haga) — evita saturar a los clientes
  con "cambios" que no son tales.

- **Por qué la autenticación va en el handshake y no en un middleware**:
  `OriginVerifyMiddleware` (`security.py`) es un `BaseHTTPMiddleware`, y
  Starlette no lo corre sobre conexiones WebSocket (deja pasar sin tocar
  cualquier scope que no sea `http`). Por eso el handler de `/api/ws` revalida
  `X-Origin-Verify` a mano, ANTES de aceptar la conexión (`websocket.accept()`),
  reusando `security.verify_origin` — la misma comparación con
  `hmac.compare_digest` (evita timing attacks) y el mismo criterio de
  "`ORIGIN_VERIFY_SECRET` vacío = modo local, se deja pasar" que ya usa el
  middleware HTTP. Si falta o no coincide, se cierra con código `1008` sin
  haber aceptado nunca la conexión. También hay un tope de conexiones
  simultáneas (`WS_MAX_CLIENTS`, default 20): al superarlo se cierra con
  `1013`.

- **Ping de aplicación**: cada `WS_PING_INTERVAL_S` segundos (default 25) el
  servidor manda `{"type": "ping"}`. Existe porque CloudFront corta
  conexiones inactivas: el ping mantiene el socket vivo y de paso sirve para
  detectar clientes muertos.

- **Qué debe hacer el dashboard**:
  - Reconectar con backoff exponencial si el socket se cae o nunca llega a
    conectar (1 s, 2 s, 4 s, ... hasta un tope de 30 s).
  - Mientras el socket esté caído, volver a hacer polling de `GET
    /api/state` como respaldo.
  - Ignorar los mensajes de tipo `"ping"` (no traen datos, solo mantienen la
    conexión viva).

## Flujo de un caso

1. `POST /api/cases` (`api/cases.py`) valida que cada `input_key` exista en el
   bucket del dataset (`head_object` en paralelo), arma sub-tareas con
   `routing/planner.plan_case`, y escribe en este orden (outbox):
   `SubTasks` (BatchWriteItem) → `Cases` (PutItem) → SQS
   (`routing/enqueue.enqueue_case`). Si el proceso muere entre el
   BatchWriteItem y el PutItem del caso, quedan sub-tareas huérfanas en
   DynamoDB sin un `Cases` que las referencie; como el PutItem nunca se
   ejecutó, nunca se llega a encolarlas — quedan inertes hasta una limpieza
   manual. Aceptable: el cliente reintenta el `POST` con un `case_id` nuevo.
2. Un archivo con extensión no soportada, o una operación inválida para su
   tipo, nace como una sub-tarea ya `failed` (código `unsupported_format`),
   sin encolar. Si TODAS las sub-tareas de un caso nacen así,
   `pending_count == 0` desde el arranque y el caso se cierra en el mismo
   request (`barrier/finalize.finalize`).
3. Cada worker (fuera del alcance de este código; `scripts/fake_worker.py` es
   el "protocolo" que va a implementar) consume sus colas según
   `shared.routing.POLL_ORDER` y publica un `ResultMessage` por cada cambio de
   estado a la cola de resultados. Los workers **nunca** tocan `Cases`/
   `SubTasks` directamente.
4. `consumers/results.py` aplica progreso no terminal
   (`assigned`/`running`/`retrying`) con `SubTasksRepo.update_progress`
   (condicionado a que `state_order` avance) y cierra sub-tareas terminales
   con `barrier/close.close_subtask`.
5. `close_subtask` descuenta `pending_count` y marca la sub-tarea cerrada en
   una única transacción (`TransactWriteItems`); si eso deja `pending_count`
   en 0, llama a `barrier/finalize.finalize` en el mismo hilo.
6. `finalize` calcula el estado final (`shared.states.final_case_status`),
   arma el reporte consolidado (`reports/builder.build_report`), lo sube a S3
   **antes** de escribir el estado final del caso, y recién ahí marca el caso
   terminal (`CasesRepo.finalize_write`, condicionado a que no sea ya
   terminal).
7. Un mensaje que agota los reintentos de SQS cae en la DLQ; el coordinador la
   consume (`consumers/dlq.py`) y lo registra como `failed`/
   `retries_exhausted` vía `shared.messages.result_from_dlq`, exactamente como
   si el worker hubiera reportado el fallo.
8. `barrier/sweeper.run_sweep` corre al arrancar (antes de aceptar tráfico) y
   luego cada `SWEEP_INTERVAL_S`: reencola sub-tareas que quedaron con
   `enqueued=false` (un `SendMessageBatch` parcialmente fallido) y repara
   casos que llegaron a `pending_count == 0` pero nunca se finalizaron (el
   proceso murió entre el `TransactWriteItems` de `close_subtask` y la llamada
   a `finalize`).

## El barrier

`pending_count` en `Cases` es el contador del barrier: arranca en
`total - prefailed_count` y cada sub-tarea terminal lo descuenta en 1. Dos
invariantes lo protegen (ver docstrings de `barrier/close.py`):

- **Nunca restar dos veces por la misma sub-tarea**: `build_close_update` en
  `repo/cases.py` usa `ADD closed :id_set` (string-set, idempotente) con
  `ConditionExpression: NOT contains(closed, :id)`. Un `ResultMessage`
  duplicado (SQS Standard puede reentregar) hace fallar la condición; se
  detecta como `TransactionCanceledException` con `ConditionalCheckFailed` y
  se ignora el cierre (pero igual se revisa si hay que finalizar, por si el
  duplicado llega después de que el contador ya esté en 0 sin haberse
  finalizado).
- **SubTasks y Cases se actualizan atómicamente**: las dos escrituras van en
  un único `TransactWriteItems`. Si solo se actualizara `SubTasks` y el
  proceso muriera antes de tocar `Cases`, `pending_count` quedaría colgado
  para siempre; si fuera al revés, bajaría sin que la sub-tarea reflejara su
  estado final.
- **`finalize` es idempotente**: protegido por `finalize_write`, condicionado
  a que el caso no sea ya terminal. Puede correr en paralelo desde varios
  hilos/llamadas (el barrier normal, un duplicado tardío, el sweeper) sin
  duplicar el reporte ni corromper el estado — el que pierde la carrera
  simplemente no escribe nada.
- **Desorden de SQS Standard**: cada estado de sub-tarea lleva un
  `state_order` monótono (`shared.states.state_order`); las escrituras de
  progreso y cierre están condicionadas a `state_order < :nuevo`, así un
  mensaje atrasado (`running` de un intento viejo) nunca pisa uno más
  reciente (`completed`).

## Garantías ante caídas y límites conocidos

- **Huérfanas outbox**: sub-tareas escritas pero sin `Cases` asociado si el
  proceso muere entre el `BatchWriteItem` y el `PutItem` del caso (ver "Flujo
  de un caso", paso 1). No se limpian solas.
- **Reencolado duplicado posible, pero idempotente**: si `SendMessageBatch`
  reporta éxito pero la confirmación (`mark_enqueued`) no llega a escribirse,
  el sweeper puede reencolar la misma sub-tarea otra vez. El worker la procesa
  dos veces; el barrier solo cuenta el primer cierre que gane la condición
  (ver arriba), así que el resultado final es correcto aunque haya trabajo
  duplicado.
- **Resultado duplicado o tardío**: se cuenta una sola vez (ver el barrier).
- **Crash entre `pending_count == 0` y `finalize`**: reparado por el próximo
  duplicado que pase por `close_subtask`, o por el sweeper en su próxima
  pasada (a lo sumo `SWEEP_INTERVAL_S` de demora).
- **Shutdown de los consumers de SQS**: puede tardar hasta `wait_time` (20s)
  en notar el `stop_event` (ver "Modelo de concurrencia").
- **Scans en vez de GSIs**: `CasesRepo.scan_non_terminal`/`list_recent` y
  `SubTasksRepo.scan_orphan_enqueue_candidates` son `Scan` con filtro, O(tabla
  completa) incluso si filtran después. Aceptable a la escala de un proyecto
  de curso (cientos de casos); en producción serían GSIs por
  `status`/`(status, enqueued)`.

Decisiones de alcance que quedaron deliberadamente fuera de esta iteración
(no son bugs pendientes, son límites conscientes del alcance actual):

- **Sin autenticación/autorización de usuario en `/api/*`**: solo hay
  verificación de origen compartida (`X-Origin-Verify`, ver `security.py`), no
  identidad de usuario ni roles. Aceptable para el alcance actual (proyecto de
  curso, sin multi-tenant), pero si el panel llega a exponerse a más de
  un usuario/equipo esto necesita revisarse.
- **`/healthz` no dispara ninguna acción de infraestructura**: reporta
  correctamente si un hilo interno murió (ver "Modelo de concurrencia"), pero
  nada en la infraestructura actual actúa sobre esa señal — no hay ALB/target
  group frente al coordinador, es una sola EC2 con Elastic IP. Es una
  limitación de la arquitectura actual, no algo para arreglar en el código del
  coordinador.
- **`CaseFileIn.input_key` solo se valida por existencia, no por forma**:
  `create_case` confirma con `head_object` que la clave exista en el bucket
  del dataset, pero no sanea su contenido. Si el worker real llega a derivar
  un path de archivo local a partir de esa clave, debería volver a sanearla
  (mismo criterio que `_sanitize_filename` en `uploads.py`) en vez de confiar
  en ella cruda.

## Convención del dataset

- Los archivos del dataset viven en `DATASET_BUCKET` bajo prefijos arbitrarios
  (p. ej. `batch1/`, `uploads/{upload_id}/`).
- `shared.routing.EXTENSIONS` define qué extensiones se reconocen y a qué
  `MediaType` mapean (`.mp4`/`.mkv`/... → video, `.mp3`/`.wav`/... → audio,
  `.jpg`/`.png`/... → imagen). Cualquier otra extensión es "no soportada".
- Un `manifest.json` **opcional** por prefijo (`GET /api/dataset?prefix=X`
  busca `{prefix}manifest.json`): un dict `input_key -> metadata` arbitraria.
  Si no existe, las entradas de ese prefijo simplemente no traen `metadata`.
  Esto lo usa el generador de carga (otro componente) para anotar sus lotes
  con contexto (p. ej. cámara de origen, fecha de captura).
- Los archivos `.json` de un prefijo (el manifest y cualquier otro) NUNCA
  aparecen como entradas multimedia en `GET /api/dataset`.

## Cambios a `shared/`

Hechos por la tanda anterior; documentados acá para que quede registro:

1. `shared/init.py` renombrado a `shared/__init__.py` (typo de scaffolding,
   archivo vacío).
2. `MetricsResponse` agregado a `shared/api.py` (respuesta de
   `GET /api/metrics`).
3. `Alert` + campo `alerts: list[Alert] = []` en `StateResponse`, agregado a
   `shared/api.py` (alertas operativas del panel, calculadas por
   `monitor/balancer.py`).
4. `load_samples_chunk_key(ts)` agregado a `shared/models.py`, sin tocar
   `load_samples_key` (clave por chunk de minuto para el flush periódico de
   `monitor/samples.py`, en vez de reescribir el archivo del día completo en
   cada flush).

## Despliegue en EC2 (vía SSM Session Manager, sin SSH)

```bash
aws ssm start-session --target <instance-id>   # IDs: terraform output instance_ids

sudo dnf install -y git   # si el user_data todavía no lo dejó instalado
sudo git clone <url-del-repo> /opt/dmp
cd /opt/dmp
uv sync --package coordinator

sudo cp deploy/coordinator.service /etc/systemd/system/dmp-coordinator.service
sudo systemctl daemon-reload
sudo systemctl enable --now dmp-coordinator

# logs:
journalctl -u dmp-coordinator -f
```

El `user_data` de Terraform (`infra/modules/compute/templates/coordinator.sh.tftpl`)
ya deja `/etc/dmp.env` con `AWS_REGION`, `QUEUE_URLS`, `TABLE_*`,
`DATASET_BUCKET`, `RESULTS_BUCKET`, `API_PORT` y `ORIGIN_VERIFY_SECRET` — no
hay que armarlo a mano.

## Correr localmente y probar el pipeline completo (smoke test)

Necesita `../.env.local` en la raíz del repo (lo genera `infra/README.md`
contra recursos reales de AWS ya aplicados con Terraform).

1. Levantar el coordinador:

   ```bash
   uv run --package coordinator uvicorn coordinator.main:app --reload
   ```

2. Levantar 2-3 `fake_worker.py`, uno por pool (en terminales separadas):

   ```bash
   uv run --package coordinator python coordinator/scripts/fake_worker.py --pool video
   uv run --package coordinator python coordinator/scripts/fake_worker.py --pool audio
   uv run --package coordinator python coordinator/scripts/fake_worker.py --pool metadatos
   ```

   (`--fail-rate 0.1 --crash-rate 0.05` para ejercitar fallos/reintentos/DLQ.)

3. Correr el smoke test (usa AWS real, solo lo corre el usuario a mano):

   ```bash
   uv run --package coordinator python coordinator/scripts/smoke.py
   ```

## Tests

```bash
uv run --package coordinator python -m pytest coordinator/tests -v
uv run ruff check coordinator
uv run ruff format --check coordinator
```

Nota: `uv run --package coordinator pytest ...` (invocando el script `pytest`
directamente, sin `python -m`) puede fallar con
`ModuleNotFoundError: No module named 'shared'` en este repo. Es un problema
del **instalado editable** de `shared/` (su `pyproject.toml` remapea el
layout plano del código a un paquete `shared/` vía
`[tool.hatch.build.targets.wheel.sources]`, pero el instalado editable de uv
apunta `sys.path` directo al directorio fuente sin aplicar ese remapeo —
funciona por builds/instalados no editables, no en editable). `python -m
pytest` (o cualquier invocación con el directorio raíz del repo como cwd, que
es el caso normal) no lo sufre porque `python -m` agrega el cwd a `sys.path`,
donde `shared/` sí es un subdirectorio válido con `__init__.py`. No se tocó
`shared/pyproject.toml` para no violar el alcance de esta tanda; si se quiere
arreglar de raíz, revisar cómo uv genera el `.pth` editable para paquetes con
`sources` remapeado en `hatchling`.

Todos los tests usan `moto` (SQS + DynamoDB + S3 en memoria); ninguno toca AWS
real. `coordinator/tests/conftest.py` fija explícitamente todas las variables
de `Settings` (incluidas `TABLE_*`) para no heredar sin querer los valores de
un `.env.local` real si existe en el repo.
