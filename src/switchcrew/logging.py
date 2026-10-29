"""Allowlisted JSON logs: never copy HTTP bodies, headers, or exception text."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        if record.name in {"switchcrew.api", "switchcrew.workers"}:
            result["event"] = record.msg
            for key in ("request_id", "method", "status_code", "error_type"):
                if hasattr(record, key):
                    result[key] = getattr(record, key)
        else:
            # Third-party records can include URLs, payloads and credentials.
            result["event"] = "external_log"
        return json.dumps(result)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Uvicorn installs its own stderr handlers before loading the application.
    # Route those through our formatter so server exception traces cannot leak
    # exception messages or request payloads after a sanitized HTTP response.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    for name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).disabled = True
