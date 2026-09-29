"""Hilo consumidor de SQS genérico, reutilizable para resultados y DLQ.

El `handler` procesa UN mensaje crudo de SQS y devuelve `True` si hay que
borrarlo (ya se aplicó, o es basura irrecuperable) o `False` si no (falla
transitoria: se deja que el visibility timeout expire y SQS lo vuelva a
entregar).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], bool]


class SqsConsumerThread(threading.Thread):
    def __init__(
        self,
        *,
        sqs: Any,
        queue_url: str,
        handler: Handler,
        stop_event: threading.Event,
        wait_time: int = 20,
        max_messages: int = 10,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self._sqs = sqs
        self._queue_url = queue_url
        self._handler = handler
        self._stop_event = stop_event
        self._wait_time = wait_time
        self._max_messages = max_messages

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()

    def _poll_once(self) -> None:
        try:
            resp = self._sqs.receive_message(
                QueueUrl=self._queue_url,
                WaitTimeSeconds=self._wait_time,
                MaxNumberOfMessages=self._max_messages,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except Exception:
            logger.exception(
                "ReceiveMessage falló", extra={"queue_url": self._queue_url}
            )
            return

        messages = resp.get("Messages", [])
        if not messages:
            return

        # El lote que ya empezamos a recibir se procesa completo aunque
        # stop_event se dispare a mitad de camino: no queremos dejar mensajes
        # a medio manejar (ya invisibles por el receive) sin decidir si se
        # borran o no.
        to_delete: list[dict[str, Any]] = []
        for message in messages:
            try:
                should_delete = self._handler(message)
            except Exception:
                logger.exception(
                    "excepción no manejada en el handler, se deja para reintento de SQS",
                    extra={
                        "queue_url": self._queue_url,
                        "message_id": message.get("MessageId"),
                    },
                )
                should_delete = False
            if should_delete:
                to_delete.append(message)

        if to_delete:
            self._delete_batch(to_delete)

    def _delete_batch(self, messages: list[dict[str, Any]]) -> None:
        entries = [
            {"Id": str(i), "ReceiptHandle": message["ReceiptHandle"]}
            for i, message in enumerate(messages)
        ]
        for start in range(0, len(entries), 10):  # límite de DeleteMessageBatch
            chunk = entries[start : start + 10]
            try:
                self._sqs.delete_message_batch(QueueUrl=self._queue_url, Entries=chunk)
            except Exception:
                logger.exception(
                    "DeleteMessageBatch falló, los mensajes van a reaparecer y reprocesarse",
                    extra={"queue_url": self._queue_url},
                )
