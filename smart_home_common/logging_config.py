import json
import logging
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)


@contextmanager
def log_context(correlation_id: str | None = None, request_id: str | None = None, task_id: str | None = None):
    tokens = [
        _correlation_id.set(correlation_id) if correlation_id is not None else None,
        _request_id.set(request_id) if request_id is not None else None,
        _task_id.set(task_id) if task_id is not None else None,
    ]
    try:
        yield
    finally:
        for var, token in zip((_correlation_id, _request_id, _task_id), tokens):
            if token is not None:
                var.reset(token)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "service": self.service,
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "correlation_id": _correlation_id.get(),
            "request_id": _request_id.get(),
            "task_id": _task_id.get(),
            "operation": getattr(record, "operation", None),
            "duration_ms": getattr(record, "duration_ms", None),
            "status": getattr(record, "status", None),
            "timestamp": time.time(),
        }
        return json.dumps(payload)


def configure_logging(service: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
