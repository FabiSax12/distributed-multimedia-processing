"""Reglas de alerta del panel: funciones PURAS, no tocan AWS.

`monitor/snapshot.py` las llama con los datos que ya juntó (colas, workers) y
mete el resultado en `StateResponse.alerts`. Separarlas así permite testear
las reglas sin `moto` (ver `coordinator/tests/test_balancer.py`).

TODO(actuación): por ahora estas reglas solo generan `Alert`s para que el
panel las muestre. El siguiente paso natural (fuera del alcance de esta
tanda) sería que el balanceador publique una configuración que los workers
lean para decidir cuándo ayudar a otro pool (más allá del `POLL_ORDER`
estático de `shared.routing`) — p. ej. un ítem en una tabla/S3 con pesos por
pool que el runner del worker consulte antes de elegir cola.
"""

from __future__ import annotations

from shared.api import Alert, QueueDepth, WorkerView
from shared.routing import DLQ, Pool, Priority, queue_key


def check_pool_saturation(
    queues: list[QueueDepth],
    workers: list[WorkerView],
    *,
    queue_threshold: int,
    cpu_threshold: float,
) -> list[Alert]:
    """Un pool está saturado si su cola de prioridad alta está por encima del
    umbral, o si todos sus workers vivos superan el umbral de CPU.

    "Todos sus workers vivos" a propósito: si un solo worker está por encima
    de CPU pero hay otros con margen, el pool en conjunto todavía puede
    absorber trabajo — no alertamos por un solo worker caliente.
    """
    alerts: list[Alert] = []
    depth_by_queue = {q.queue: q for q in queues}

    for pool in Pool:
        high_queue = queue_key(pool, Priority.HIGH)
        depth = depth_by_queue.get(high_queue)
        if depth is not None and depth.visible > queue_threshold:
            alerts.append(
                Alert(
                    level="warning",
                    pool=pool.value,
                    message=(
                        f"cola '{high_queue}' con {depth.visible} mensajes visibles "
                        f"(umbral {queue_threshold})"
                    ),
                )
            )

        pool_workers = [w for w in workers if w.pool is pool and w.alive]
        if pool_workers and all(w.cpu_percent >= cpu_threshold for w in pool_workers):
            alerts.append(
                Alert(
                    level="warning",
                    pool=pool.value,
                    message=(
                        f"todos los workers vivos del pool '{pool.value}' superan "
                        f"{cpu_threshold}% de CPU"
                    ),
                )
            )

    return alerts


def check_pools_without_workers(workers: list[WorkerView]) -> list[Alert]:
    """Un pool sin ningún worker vivo no puede procesar nada de su cola."""
    alerts: list[Alert] = []
    for pool in Pool:
        if not any(w.pool is pool and w.alive for w in workers):
            alerts.append(
                Alert(
                    level="critical",
                    pool=pool.value,
                    message=f"el pool '{pool.value}' no tiene workers vivos",
                )
            )
    return alerts


def check_dlq(queues: list[QueueDepth]) -> list[Alert]:
    """Cualquier mensaje en la DLQ es una sub-tarea que agotó sus reintentos."""
    depth_by_queue = {q.queue: q for q in queues}
    dlq = depth_by_queue.get(DLQ)
    if dlq is not None and (dlq.visible > 0 or dlq.in_flight > 0):
        return [
            Alert(
                level="critical",
                pool="-",
                message=f"DLQ con {dlq.visible} mensajes visibles y {dlq.in_flight} en vuelo",
            )
        ]
    return []


def check_dead_workers(workers: list[WorkerView]) -> list[Alert]:
    """Un worker que dejó de mandar heartbeat.

    Usa el campo `alive` ya calculado por `monitor/snapshot.py` (con
    `WorkerItem.is_alive()`/`WORKER_DEAD_AFTER_S`) en vez de recalcularlo acá,
    para que todas las reglas de esta pasada vean el mismo instante `now`.
    """
    return [
        Alert(
            level="warning",
            pool=worker.pool.value,
            message=f"worker '{worker.worker_id}' sin heartbeat reciente",
        )
        for worker in workers
        if not worker.alive
    ]


def build_alerts(
    queues: list[QueueDepth],
    workers: list[WorkerView],
    *,
    queue_threshold: int,
    cpu_threshold: float,
) -> list[Alert]:
    """Junta todas las reglas de alerta en el orden en que se muestran."""
    return [
        *check_dlq(queues),
        *check_pools_without_workers(workers),
        *check_pool_saturation(
            queues,
            workers,
            queue_threshold=queue_threshold,
            cpu_threshold=cpu_threshold,
        ),
        *check_dead_workers(workers),
    ]
