import json
import logging

from switchcrew.logging import JsonFormatter, configure_logging


def test_external_exception_and_uvicorn_handlers_are_sanitized(capsys):
    configure_logging()
    try:
        raise RuntimeError("private-credential")
    except RuntimeError:
        logging.getLogger("uvicorn.error").exception("unsafe private-credential")
    captured = capsys.readouterr().err
    assert "private-credential" not in captured
    assert '"event": "external_log"' in captured


def test_internal_json_logging_has_allowlisted_fields():
    record = logging.LogRecord("switchcrew.api", logging.INFO, "", 0, "request", (), None)
    record.request_id = "request-id"
    record.status_code = 201
    record.authorization = "private-credential"
    formatted = JsonFormatter().format(record)
    assert '"status_code": 201' in formatted
    assert "private-credential" not in formatted


def test_transport_recovery_logs_keep_only_safe_diagnostics():
    record = logging.LogRecord(
        "switchcrew.switch", logging.WARNING, "", 0, "stream_reconnecting", (), None
    )
    record.connection_id = "connection"
    record.generation = 7
    record.cursor = 42
    record.status_code = 503
    record.body = "private-credential"
    record.headers = {"Authorization": "private-credential"}
    record.exc_info = (RuntimeError, RuntimeError("private-credential"), None)
    formatted = JsonFormatter().format(record)
    value = json.loads(formatted)
    assert (value["generation"], value["cursor"], value["status_code"]) == (7, 42, 503)
    assert "private-credential" not in formatted
