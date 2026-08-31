"""JSON logging, one line per event. Loggers carry stable
context (run_id, section_id, partition_date) via ``get_logger`` kwargs;
per-event fields ride in ``extra={"fields": {...}}``."""

import json
import logging
import sys
from datetime import datetime, timezone

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"fields", "context"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        log_event.update(getattr(record, "context", {}))
        log_event.update(getattr(record, "fields", {}))
        if record.exc_info:
            log_event["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_event, default=str)


def setup_logging(level: str = "INFO") -> None:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers[:] = [stream_handler]
    root_logger.setLevel(level.upper())


class _ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        event_fields = kwargs.pop("fields", None) or {
            key: kwargs.pop(key)
            for key in list(kwargs)
            if key not in _RESERVED
        }
        kwargs["extra"] = {"context": self.extra, "fields": event_fields}
        return msg, kwargs


def get_logger(name: str, **context) -> logging.LoggerAdapter:
    return _ContextAdapter(logging.getLogger(name), context)
