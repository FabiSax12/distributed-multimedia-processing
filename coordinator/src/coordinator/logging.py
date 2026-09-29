"""Logging en JSON a stdout (CloudWatch Logs lee stdout de la unidad systemd).

Campos fijos: timestamp, level, logger, message. Cualquier `extra` que se pase
a un log (p. ej. `logger.info("cerrado", extra={"case_id": ..., "subtask_id": ...})`)
se agrega tal cual al objeto JSON, para poder filtrar por caso/sub-tarea en
CloudWatch Insights.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Atributos propios de logging.LogRecord: todo lo demás en record.__dict__
# viene de `extra={...}` y se copia tal cual al JSON.
_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    """Se llama UNA vez al arrancar el proceso (main.py, un worker script, etc.)."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
