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
