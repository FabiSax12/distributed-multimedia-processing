"""Punto de entrada del worker: `uv run --package workers python -m workers.main`.

Modelo de concurrencia: un proceso por máquina con `concurrency` hilos
consumidores más un hilo de heartbeat. Los hilos no compiten por el GIL: el
trabajo pesado corre en subprocesos de ffmpeg y el resto es espera de red
(SQS, S3, APIs externas). Así el `concurrency` del heartbeat es literalmente
cuántas sub-tareas puede tener la máquina en curso a la vez.

SIGTERM (systemd stop) activa `stop_event`: cada hilo termina la sub-tarea en
curso y sale. Si systemd lo mata antes, el mensaje no se borró y SQS lo
reentrega a otro worker.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import threading
from urllib.request import Request, urlopen

import boto3
from botocore.config import Config

from shared.routing import Pool

from .config import get_settings
from .logging import setup_logging
from .runner.heartbeat import Heartbeat
from .runner.poller import op_timeouts
from .runner.process import WorkerContext, consume

logger = logging.getLogger(__name__)

_IMDS = "http://169.254.169.254/latest"
_IMDS_TIMEOUT_S = 1.0

# max_pool_connections: un hilo de heartbeat + N consumidores comparten clients.
_BOTO_CONFIG = Config(
    retries={"mode": "standard", "max_attempts": 5}, max_pool_connections=20
)


def ec2_identity() -> tuple[str, str] | None:
    """(instance-id, instance-type) vía IMDSv2, o `None` fuera de EC2."""
    try:
        token_req = Request(
            f"{_IMDS}/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        token = urlopen(token_req, timeout=_IMDS_TIMEOUT_S).read().decode()

        def get(path: str) -> str:
            req = Request(
                f"{_IMDS}/meta-data/{path}", headers={"X-aws-ec2-metadata-token": token}
            )
            return urlopen(req, timeout=_IMDS_TIMEOUT_S).read().decode()

        return get("instance-id"), get("instance-type")
    except OSError:
        return None


def worker_identity(pool: Pool, ec2: tuple[str, str] | None) -> tuple[str, str]:
    """(worker_id, instance_type). En EC2 sin pid: un reinicio pisa el mismo
    ítem de Workers. En una laptop con host y pid, para poder correr varios."""
    if ec2 is not None:
        instance_id, instance_type = ec2
        return f"{pool.value}-{instance_id}", instance_type
    return f"{pool.value}-{socket.gethostname()}-{os.getpid()}", "local"


def check_ffmpeg() -> None:
    # Los tres pools lo usan: metadatos corre ffprobe y ayuda con audio-normal.
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"falta {', '.join(missing)} en PATH (ver workers/README.md)")


def main() -> None:
    setup_logging()
    check_ffmpeg()
    settings = get_settings()
    worker_id, instance_type = worker_identity(settings.POOL, ec2_identity())

    session = boto3.session.Session(region_name=settings.AWS_REGION)
    sqs = session.client("sqs", config=_BOTO_CONFIG)
    s3 = session.client("s3", config=_BOTO_CONFIG)
    dynamodb = session.client("dynamodb", config=_BOTO_CONFIG)

    heartbeat = Heartbeat(
        dynamodb,
        settings.TABLE_WORKERS,
        worker_id=worker_id,
        pool=settings.POOL,
        instance_type=instance_type,
        hostname=socket.gethostname(),
        concurrency=settings.concurrency,
    )
    ctx = WorkerContext(
        sqs=sqs,
        s3=s3,
        dynamodb=dynamodb,
        queue_urls=settings.QUEUE_URLS,
        cases_table=settings.TABLE_CASES,
        dataset_bucket=settings.DATASET_BUCKET,
        results_bucket=settings.RESULTS_BUCKET,
        worker_id=worker_id,
        pool=settings.POOL,
        heartbeat=heartbeat,
        timeouts=op_timeouts(sqs, settings.QUEUE_URLS, settings.poll_order),
    )

    stop_event = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop_event.set())

    threading.Thread(
        target=heartbeat.run, args=(stop_event,), name="heartbeat", daemon=True
    ).start()
    consumers = [
        threading.Thread(
            target=consume,
            args=(ctx, settings.poll_order, stop_event),
            name=f"consumer-{i}",
        )
        for i in range(settings.concurrency)
    ]
    for thread in consumers:
        thread.start()
    logger.info(
        "worker arrancado",
        extra={
            "worker_id": worker_id,
            "pool": settings.POOL.value,
            "concurrency": settings.concurrency,
            "poll_order": settings.poll_order,
            "timeouts": ctx.timeouts,
        },
    )

    # join con timeout: el hilo principal tiene que despertarse para atender
    # las señales (Python solo las entrega en el hilo principal).
    while any(thread.is_alive() for thread in consumers):
        for thread in consumers:
            thread.join(timeout=1)
    logger.info("worker apagado", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
