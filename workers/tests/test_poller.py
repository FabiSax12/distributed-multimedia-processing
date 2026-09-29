"""`runner/poller.py`: orden de consumo por prioridad y tope de tiempo por cola.

`POLL_ORDER` es la prioridad estricta del diagrama: primero la cola alta del
pool, después la normal y recién ahí las colas de otros pools que puede ayudar.
"""

from __future__ import annotations

from shared.routing import POLL_ORDER, Pool
from workers.runner.poller import op_timeouts, receive_next

from .conftest import QUEUE_URLS


def _send(aws, queue: str, body: str) -> None:
    aws["sqs"].send_message(QueueUrl=QUEUE_URLS[queue], MessageBody=body)


def test_high_priority_queue_wins(aws) -> None:
    _send(aws, "video-normal", "normal")
    _send(aws, "video-alta", "alta")

    queue, message = receive_next(
        aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.VIDEO], wait_s=0
    )

    assert (queue, message["Body"]) == ("video-alta", "alta")


def test_helps_other_pool_only_when_own_queues_are_empty(aws) -> None:
    _send(aws, "audio-normal", "ayuda")

    queue, message = receive_next(
        aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.VIDEO], wait_s=0
    )

    assert queue == "audio-normal"
    # El intento viaja como atributo: el worker lo necesita para `attempt`.
    assert message["Attributes"]["ApproximateReceiveCount"] == "1"


def test_ignores_queues_outside_its_poll_order(aws) -> None:
    # video nunca toma metadatos: no está en su POLL_ORDER.
    _send(aws, "metadatos-alta", "no-es-mio")

    assert receive_next(aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.VIDEO], wait_s=0) is None


def test_op_timeout_is_below_each_queue_visibility_timeout(aws) -> None:
    timeouts = op_timeouts(aws["sqs"], QUEUE_URLS, POLL_ORDER[Pool.METADATA])

    # metadatos 120 s y audio 300 s (infra/modules/queues), menos 30 s de margen
    # para publicar el resultado antes de que SQS reentregue el mensaje.
    assert timeouts == {
        "metadatos-alta": 90,
        "metadatos-normal": 90,
        "audio-normal": 270,
    }
