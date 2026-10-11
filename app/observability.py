"""Structured JSON logging to stdout.

Events carry identifiers and counts only - never evidence contents, prompts or secrets.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime, timezone

LOGGER_NAME = "proofflow"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def new_operation_id() -> str:
    return uuid.uuid4().hex[:12]


def log_event(event: str, *, level: int = logging.INFO, exc_info: bool = False, **fields: object) -> None:
    logging.getLogger(LOGGER_NAME).log(level, event, extra={"fields": fields}, exc_info=exc_info)
