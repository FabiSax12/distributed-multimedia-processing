"""Consumo de colas en el orden de `shared.routing.POLL_ORDER`.

Prioridad estricta: en cada vuelta se recorre la lista desde el principio y
se toma el primer mensaje que aparezca, así una sub-tarea `alta` nunca espera
detrás de una `normal` del mismo pool. Las colas de otros pools (ayuda entre
pools) van al final de la lista y solo se consumen si las propias están vacías.

Las colas de trabajo tienen `receive_wait_time_seconds = 0` en Terraform, así
que el long polling lo pide el worker en cada `receive_message` (`wait_s`),
igual que `coordinator/scripts/fake_worker.py`. Con la cola vacía, eso
también evita que el loop gire en seco.
"""

from __future__ import annotations

from typing import Any

RECEIVE_WAIT_S = 2

# Margen entre el tope de la operación y el visibility timeout de su cola: lo
# que tarda subir la salida a S3 y publicar el resultado antes de que SQS
# vuelva a entregarle el mensaje a otro worker.
_PUBLISH_MARGIN_S = 30


def receive_next(
    sqs: Any,
    queue_urls: dict[str, str],
    poll_order: tuple[str, ...],
    *,
    wait_s: int = RECEIVE_WAIT_S,
) -> tuple[str, dict[str, Any]] | None:
    """Primer mensaje disponible siguiendo `poll_order`, o `None` si todas están vacías."""
    for queue_name in poll_order:
        resp = sqs.receive_message(
            QueueUrl=queue_urls[queue_name],
            WaitTimeSeconds=wait_s,
            MaxNumberOfMessages=1,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages", [])
        if messages:
            return queue_name, messages[0]
    return None


def op_timeouts(
    sqs: Any, queue_urls: dict[str, str], poll_order: tuple[str, ...]
) -> dict[str, int]:
    """Tope de ejecución por cola de origen, leído una vez al arrancar.

    Depende de la cola y no del pool del worker: un worker de metadatos que
    ayuda con `audio-normal` hereda los 300 s de audio, no sus 120 s.
    """
    timeouts: dict[str, int] = {}
    for queue_name in poll_order:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_urls[queue_name], AttributeNames=["VisibilityTimeout"]
        )["Attributes"]
        visibility = int(attrs["VisibilityTimeout"])
        timeouts[queue_name] = max(visibility - _PUBLISH_MARGIN_S, visibility // 2)
    return timeouts
