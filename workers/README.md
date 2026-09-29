# Workers

Un solo código para los tres pools. `POOL` (`video`, `audio` o `metadatos`)
elige qué colas consume y en qué orden (`shared.routing.POLL_ORDER`). El
worker procesa la sub-tarea, sube la salida a S3 y reporta cada cambio de
estado al coordinador por la cola `resultados`. Nunca escribe `Cases` ni
`SubTasks`; su única escritura en DynamoDB es su heartbeat en `Workers`.

El diseño completo y los criterios de aceptación están en
[`docs/prd-workers.md`](../docs/prd-workers.md).

## Modelo de concurrencia

Un proceso por máquina con `WORKER_CONCURRENCY` hilos consumidores (por
defecto 2 en video y audio, 4 en metadatos) más un hilo de heartbeat. El
trabajo pesado corre en subprocesos de ffmpeg y el resto es espera de red, así
que los hilos no compiten por el GIL.

## Protocolo de una sub-tarea

1. Recorre sus colas en el orden de `POLL_ORDER`: primero la `alta` de su
   pool, después la `normal` y al final las `normal` de otros pools que puede
   ayudar. Así se redistribuye la carga sin tocar el coordinador.
2. Lee `Cases.cancel_requested` (lectura consistente). Si el caso se canceló,
   reporta `cancelled` y no procesa.
3. Si SQS ya entregó el mensaje antes (`ApproximateReceiveCount > 1`), reporta
   `retrying`.
4. Reporta `assigned`, descarga el archivo del dataset y reporta `running`
   con el progreso de ffmpeg cada 10 puntos.
5. Sube la salida a `results/{case_id}/{subtask_id}/` y reporta `completed`,
   o `failed` con su `ErrorCode`.
6. Recién ahí borra el mensaje de trabajo.

Si algo inesperado falla (S3 caído, un bug), el worker no reporta terminal ni
borra: SQS reintenta y, al tercer intento, la DLQ se lo entrega al
coordinador como `retries_exhausted`.

Cada operación tiene como tope el visibility timeout de su cola menos 30 s
(video 870 s, audio 270 s, metadatos 90 s). Pasado ese tiempo el worker mata
ffmpeg y reporta `timeout` antes de que SQS reentregue el mensaje.

## Operaciones

| Operación | Pool | Salida | `params` opcionales |
|---|---|---|---|
| `video_convert` | video | `<nombre>.<fmt>` | `target_format`: mp4, mkv, mov, webm |
| `audio_extract` | video | `<nombre>.<fmt>` | `audio_format`: mp3, ogg, m4a, flac, wav |
| `video_thumbnail` | video | `thumbnail.jpg` | `thumbnail_width` (320) |
| `audio_convert` | audio | `<nombre>.<fmt>` | `target_format`: mp3, ogg, m4a, flac, wav |
| `image_thumbnail` | audio | `thumbnail.jpg` | `thumbnail_width` (320) |
| `metadata` | metadatos | `metadata.json` | `artist`, `title` |
| `lyrics` | metadatos | `lyrics.txt`, `lyrics.json` | `artist`, `title` |
| `classify` | metadatos | `classification.json` | — |

- `metadata` junta ffprobe y, para audio, la grabación de MusicBrainz. Si
  MusicBrainz no responde, el resultado sale igual con `catalog.error`.
- `lyrics` usa lyrics.ovh. Que no haya letra es un resultado válido
  (`found: false`). Si la API no responde, es `external_api_error`.
- Artista y título salen de `params`, después de los tags del archivo y por
  último del nombre `Artista - Título.mp3`. El generador de carga puede pasar
  en `params` los metadatos del `manifest.json` del dataset.
- Ninguna API externa pide llave.

## Errores

| Situación | `ErrorCode` |
|---|---|
| ffprobe no lee el archivo, o no existe en el dataset | `corrupt_input` |
| ffmpeg termina con error, o un video sin pista de audio | `processing_error` |
| `params` pide un formato que no existe | `unsupported_format` |
| Pasa el tope de tiempo de la cola | `timeout` |
| lyrics.ovh no responde | `external_api_error` |

## Monitoreo

Cada 5 s el worker escribe su `WorkerItem`: CPU y memoria de la máquina
(psutil), `concurrency` y los `subtask_id` en curso. En EC2 el `worker_id` es
`{pool}-{instance-id}`, sin pid, para que un reinicio de systemd pise el mismo
ítem y no deje un worker "caído" para siempre en el panel.

## Despliegue en EC2 (vía SSM Session Manager, sin SSH)

En cada instancia worker (`terraform output instance_ids`):

```bash
aws ssm start-session --target <instance-id>

sudo git clone <url-del-repo> /opt/dmp
sudo chown -R ec2-user:ec2-user /opt/dmp
cd /opt/dmp
sudo -u ec2-user /usr/local/bin/uv sync --package workers

sudo cp deploy/worker.service /etc/systemd/system/dmp-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now dmp-worker

journalctl -u dmp-worker -f
```

El `user_data` ya deja `/etc/dmp.env` con `POOL`, `QUEUE_URLS`, `TABLE_*` y
los buckets.

**Pool de metadatos:** ahora también necesita ffmpeg
(`install_ffmpeg = true` en `infra/modules/compute/main.tf`). El `user_data`
solo corre en el primer arranque, así que la instancia que ya existe hay que
recrearla:

```bash
cd infra
terraform apply -replace='module.compute.aws_instance.worker_metadatos[0]' -var="alert_email=..."
```

Sin ffmpeg el worker no arranca y lo dice en el log.

Para actualizar el código: `cd /opt/dmp && git pull && sudo systemctl restart dmp-worker`.

## Correr localmente

### Sin AWS ni Docker

```bash
uv sync --all-packages
uv run --all-packages python workers/scripts/local_e2e.py
```

Levanta moto en proceso con las mismas colas, tablas y buckets que Terraform,
el coordinador real y un worker por pool. Después manda un caso con un video,
una canción, una imagen, un `.mp4` corrupto y un `.txt`, y verifica que
cierre como `partially_completed` con los tres pools en el reporte. Necesita
ffmpeg en PATH.

Con Floci (emulador de AWS en Docker) en vez de moto:

```bash
docker run -d --name floci -p 4566:4566 floci/floci:latest
uv run --all-packages python workers/scripts/local_e2e.py --endpoint http://127.0.0.1:4566
```

Conviene `127.0.0.1` y no `localhost`: si Docker corre dentro de WSL, boto3
puede resolver `localhost` a IPv6 y no llegar.

### Contra AWS real

Con el `.env.local` que genera Terraform en la raíz del repo:

```bash
POOL=video uv run --package workers python -m workers.main
```

## Tests

```bash
uv run --package workers python -m pytest workers/tests -v
uv run ruff check workers
uv run ruff format --check workers
```

Todos usan moto; ninguno toca AWS ni internet (las APIs externas se
reemplazan con monkeypatch). Los de operaciones corren ffmpeg de verdad sobre
archivos de 2 s generados al vuelo, y se saltan si ffmpeg no está instalado.

## Fuera de alcance

- Cancelar una sub-tarea que ya está corriendo: la cancelación se revisa antes
  de empezar.
- Extender el visibility timeout: el tope por operación lo hace innecesario.
- Autoescalado: se escala a mano con `worker_count` en Terraform.
