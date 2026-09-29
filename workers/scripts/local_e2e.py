#!/usr/bin/env python3
"""Prueba de punta a punta en una sola laptop, sin AWS ni Docker.

Levanta moto como servidor en proceso (SQS + DynamoDB + S3 por HTTP), crea
las mismas colas, tablas y buckets que Terraform (visibility timeouts y
redrive a la DLQ incluidos), arranca el coordinador REAL sin tocarlo y un
worker real por pool, y manda un caso heterogéneo generado con ffmpeg:

- un video (convert + audio_extract + thumbnail, operaciones por defecto);
- una canción con tags (audio_convert + metadata + lyrics + classify);
- una imagen (image_thumbnail);
- un .mp4 corrupto (el worker lo reporta como corrupt_input);
- un .txt (el coordinador lo marca unsupported_format sin encolarlo).

Espera que el caso cierre como `partially_completed`, que el reporte exista en
S3, que los tres pools aparezcan en `workers_used` y que `WS /api/ws` empuje
el cierre del caso.

lyrics y el catálogo de metadata salen a internet (lyrics.ovh, MusicBrainz);
sin red, esas sub-tareas fallan con external_api_error y el caso igual cierra.

Uso (desde la raíz del repo, necesita ffmpeg en PATH):
    uv sync --all-packages
    uv run --all-packages python workers/scripts/local_e2e.py

Con `--endpoint` usa un emulador que ya esté corriendo en vez de moto, por
ejemplo Floci en Docker:
    docker run -d --name floci -p 4566:4566 floci/floci:latest
    uv run --all-packages python workers/scripts/local_e2e.py --endpoint http://localhost:4566
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import boto3
import httpx
from moto.server import ThreadedMotoServer
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from shared.routing import ALL_WORK_QUEUES, DLQ, RESULTS_QUEUE, Pool

MOTO_PORT = 5055
API_PORT = 8765
API = f"http://127.0.0.1:{API_PORT}"
REGION = "us-east-1"
# Sufijo por corrida: Floci guarda estado entre corridas y así cada una arranca
# con colas, tablas y buckets vacíos.
RUN = f"{int(time.time()) % 1_000_000:06d}"
DATASET_BUCKET = f"dmp-local-dataset-{RUN}"
RESULTS_BUCKET = f"dmp-local-results-{RUN}"
TABLES = {name: f"{name}-{RUN}" for name in ("Cases", "SubTasks", "Workers")}
PREFIX = "e2e/"
CASE_TIMEOUT_S = 240

# Mismos valores que infra/modules/queues/variables.tf.
VISIBILITY_TIMEOUTS = {"video": 900, "audio": 300, "metadatos": 120}
MAX_RECEIVE_COUNT = 3

REPO = Path(__file__).resolve().parents[2]


def _env(endpoint: str) -> dict[str, str]:
    return {
        **os.environ,
        "AWS_ENDPOINT_URL": endpoint,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_REGION": REGION,
        "AWS_DEFAULT_REGION": REGION,
        "TABLE_CASES": TABLES["Cases"],
        "TABLE_SUBTASKS": TABLES["SubTasks"],
        "TABLE_WORKERS": TABLES["Workers"],
        "DATASET_BUCKET": DATASET_BUCKET,
        "RESULTS_BUCKET": RESULTS_BUCKET,
        "API_PORT": str(API_PORT),
        "ORIGIN_VERIFY_SECRET": "",
        "PYTHONUNBUFFERED": "1",
    }


def create_resources(env: dict[str, str]) -> dict[str, str]:
    session = boto3.session.Session(
        aws_access_key_id="testing", aws_secret_access_key="testing", region_name=REGION
    )
    endpoint = env["AWS_ENDPOINT_URL"]
    sqs = session.client("sqs", endpoint_url=endpoint)
    dynamodb = session.client("dynamodb", endpoint_url=endpoint)
    s3 = session.client("s3", endpoint_url=endpoint)

    urls = {DLQ: sqs.create_queue(QueueName=f"{DLQ}-{RUN}")["QueueUrl"]}
    dlq_arn = sqs.get_queue_attributes(QueueUrl=urls[DLQ], AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    urls[RESULTS_QUEUE] = sqs.create_queue(
        QueueName=f"{RESULTS_QUEUE}-{RUN}", Attributes={"VisibilityTimeout": "60"}
    )["QueueUrl"]
    redrive = json.dumps(
        {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": MAX_RECEIVE_COUNT}
    )
    for name in ALL_WORK_QUEUES:
        visibility = VISIBILITY_TIMEOUTS[name.split("-")[0]]
        urls[name] = sqs.create_queue(
            QueueName=f"{name}-{RUN}",
            Attributes={"VisibilityTimeout": str(visibility), "RedrivePolicy": redrive},
        )["QueueUrl"]

    for table, keys in (
        ("Cases", ["case_id"]),
        ("SubTasks", ["case_id", "subtask_id"]),
        ("Workers", ["worker_id"]),
    ):
        dynamodb.create_table(
            TableName=TABLES[table],
            KeySchema=[
                {"AttributeName": k, "KeyType": t}
                for k, t in zip(keys, ("HASH", "RANGE"), strict=False)
            ],
            AttributeDefinitions=[
                {"AttributeName": k, "AttributeType": "S"} for k in keys
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    for bucket in (DATASET_BUCKET, RESULTS_BUCKET):
        s3.create_bucket(Bucket=bucket)
    return urls


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True
    )


def make_media(root: Path) -> list[Path]:
    files = {
        "clip.mp4": [
            "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        ],
        "Queen - Bohemian Rhapsody.mp3": [
            "-f", "lavfi", "-i", "sine=frequency=330:duration=5",
            "-metadata", "artist=Queen", "-metadata", "title=Bohemian Rhapsody",
            "-metadata", "genre=Rock", "-c:a", "libmp3lame",
        ],
        "portada.png": ["-f", "lavfi", "-i", "testsrc=size=1280x720", "-frames:v", "1"],
    }  # fmt: skip
    paths = []
    for name, args in files.items():
        path = root / name
        _ffmpeg(*args, str(path))
        paths.append(path)
    (root / "roto.mp4").write_bytes(b"esto no es un video")
    (root / "notas.txt").write_text("formato no soportado")
    return [*paths, root / "roto.mp4", root / "notas.txt"]


def start(cmd: list[str], env: dict[str, str], log: Path) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, env=env, cwd=REPO, stdout=log.open("w"), stderr=subprocess.STDOUT
    )


def wait_healthy(proc: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"el coordinador murió al arrancar:\n{log.read_text()[-3000:]}")
        try:
            if httpx.get(f"{API}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    sys.exit("el coordinador no respondió /healthz en 60 s")


class StateListener(threading.Thread):
    """Escucha `WS /api/ws` mientras corre el caso: el panel ve lo mismo."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.states: list[dict] = []
        self.stop = threading.Event()

    def run(self) -> None:
        with connect(f"ws://127.0.0.1:{API_PORT}/api/ws") as ws:
            while not self.stop.is_set():
                try:
                    message = json.loads(ws.recv(timeout=1))
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    return  # el coordinador se apagó al final de la corrida
                if message.get("type") == "state":
                    self.states.append(message["data"])

    def case_statuses(self, case_id: str) -> list[str]:
        return [
            summary["case"]["status"]
            for state in list(self.states)
            for summary in state["cases"]
            if summary["case"]["case_id"] == case_id
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        help="emulador ya corriendo (p. ej. Floci en http://localhost:4566); sin esto, moto",
    )
    args = parser.parse_args()

    moto = None
    if args.endpoint is None:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)  # un log por request
        moto = ThreadedMotoServer(ip_address="127.0.0.1", port=MOTO_PORT)
        moto.start()
    env = _env(args.endpoint or f"http://127.0.0.1:{MOTO_PORT}")
    print(f"emulador: {env['AWS_ENDPOINT_URL']}")
    procs: list[subprocess.Popen] = []
    tmp = Path(tempfile.mkdtemp(prefix="dmp-e2e-"))
    try:
        env["QUEUE_URLS"] = json.dumps(create_resources(env))
        s3 = boto3.client(
            "s3",
            endpoint_url=env["AWS_ENDPOINT_URL"],
            region_name=REGION,
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        media_dir = tmp / "media"
        media_dir.mkdir()
        files = make_media(media_dir)
        for path in files:
            s3.upload_file(str(path), DATASET_BUCKET, f"{PREFIX}{path.name}")
        print(f"dataset: {len(files)} archivos en s3://{DATASET_BUCKET}/{PREFIX}")

        coordinator_log = tmp / "coordinator.log"
        coordinator = start(
            [sys.executable, "-m", "uvicorn", "coordinator.main:app",
             "--host", "127.0.0.1", "--port", str(API_PORT)],
            env,
            coordinator_log,
        )  # fmt: skip
        procs.append(coordinator)
        wait_healthy(coordinator, coordinator_log)
        print(f"coordinador en {API}")

        for pool in Pool:
            worker_env = {**env, "POOL": pool.value, "WORKER_CONCURRENCY": "2"}
            procs.append(
                start(
                    [sys.executable, "-m", "workers.main"],
                    worker_env,
                    tmp / f"worker-{pool.value}.log",
                )
            )
        print("workers: video, audio, metadatos")

        listener = StateListener()
        listener.start()

        song = f"{PREFIX}Queen - Bohemian Rhapsody.mp3"
        body = {
            "label": "e2e local",
            "priority": "alta",
            "files": [
                {"input_key": f"{PREFIX}clip.mp4"},
                {
                    "input_key": song,
                    "operations": ["audio_convert", "metadata", "lyrics", "classify"],
                    "params": {"target_format": "ogg"},
                },
                {"input_key": f"{PREFIX}portada.png"},
                {"input_key": f"{PREFIX}roto.mp4", "operations": ["video_thumbnail"]},
                {"input_key": f"{PREFIX}notas.txt"},
            ],
        }
        created = httpx.post(f"{API}/api/cases", json=body, timeout=30)
        created.raise_for_status()
        case_id = created.json()["case_id"]
        print(f"caso {case_id}: {created.json()}")

        deadline = time.monotonic() + CASE_TIMEOUT_S
        while True:
            detail = httpx.get(f"{API}/api/cases/{case_id}", timeout=10).json()
            status = detail["case"]["status"]
            if status in {"completed", "partially_completed", "failed", "cancelled"}:
                break
            if time.monotonic() > deadline:
                sys.exit(f"el caso no cerró en {CASE_TIMEOUT_S} s (estado {status})")
            time.sleep(1)

        print(f"\nestado final: {status}")
        for st in sorted(detail["subtasks"], key=lambda s: s["input_key"]):
            error = (st.get("error") or {}).get("code", "")
            print(
                f"  {st['status']:<10} {st.get('operation') or '-':<16} "
                f"{st.get('worker_id') or '-':<28} {Path(st['input_key']).name} {error}"
            )

        report_url = httpx.get(f"{API}/api/cases/{case_id}/report", timeout=10).json()[
            "url"
        ]
        report = httpx.get(report_url, timeout=10).json()
        print(f"\nreporte: {report['summary']}")
        print(f"workers_used: {report['workers_used']}")

        state = httpx.get(f"{API}/api/state", timeout=10).json()
        alive = sorted(w["worker_id"] for w in state["workers"] if w["alive"])
        print(f"workers vivos en /api/state: {alive}")

        # El snapshot sale cada 2 s: el cierre llega por el WebSocket poco después.
        ws_deadline = time.monotonic() + 15
        while status not in listener.case_statuses(case_id):
            if time.monotonic() > ws_deadline:
                break
            time.sleep(0.5)
        listener.stop.set()
        ws_statuses = listener.case_statuses(case_id)
        print(
            f"WebSocket /api/ws: {len(listener.states)} estados recibidos, "
            f"el caso pasó por {sorted(set(ws_statuses))}"
        )

        pools_used = {w.split("-")[0] for w in report["workers_used"]}
        assert status == "partially_completed", status
        assert pools_used == {p.value for p in Pool}, pools_used
        assert len(alive) == 3, alive
        assert status in ws_statuses, ws_statuses
        print("\nOK: el caso cerró con los tres pools y el WebSocket lo empujó.")
    except BaseException:
        for log in sorted(tmp.glob("*.log")):
            print(f"\n--- {log.name} (últimas líneas) ---\n{log.read_text()[-2500:]}")
        raise
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if moto is not None:
            moto.stop()
        print(f"logs en {tmp}")


if __name__ == "__main__":
    main()
