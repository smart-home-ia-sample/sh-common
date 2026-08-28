import json
import logging

from smart_home_common.logging_config import (
    JsonFormatter,
    configure_logging,
    get_logger,
    log_context,
)


def _record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)


def test_configure_logging_installs_a_single_json_handler():
    configure_logging("svc-x", level="DEBUG")
    root = logging.getLogger()

    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_get_logger_returns_a_stdlib_logger():
    assert isinstance(get_logger("a.b"), logging.Logger)


def test_json_formatter_emits_parseable_records_with_the_service_name():
    line = JsonFormatter("svc-y").format(_record("boom"))
    payload = json.loads(line)

    assert payload["service"] == "svc-y"
    assert payload["level"] == "info"
    assert payload["message"] == "boom"
    assert isinstance(payload["timestamp"], float)
    assert payload["correlation_id"] is None


def test_log_context_injects_ids_for_the_duration_of_the_block():
    fmt = JsonFormatter("svc-z")

    with log_context(correlation_id="c1", request_id="r1", task_id="t1"):
        inside = json.loads(fmt.format(_record()))
        assert (inside["correlation_id"], inside["request_id"], inside["task_id"]) == ("c1", "r1", "t1")

    after = json.loads(fmt.format(_record()))
    assert after["correlation_id"] is None
    assert after["request_id"] is None


def test_json_formatter_carries_operation_metadata_when_present():
    rec = _record()
    rec.operation = "dispatch"
    rec.duration_ms = 12
    rec.status = "ok"

    payload = json.loads(JsonFormatter("svc").format(rec))
    assert (payload["operation"], payload["duration_ms"], payload["status"]) == ("dispatch", 12, "ok")
