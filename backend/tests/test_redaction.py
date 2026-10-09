"""Credential redaction and camera-config secret hygiene."""

import logging

import pytest

from app.camera.rtsp import CameraConfig
from app.utils.logs import install_log_redaction, log_event, redact_text


@pytest.mark.parametrize(
    "raw, secret",
    [
        ("rtsp://admin:hunter2@10.0.0.5:554/stream", "hunter2"),
        ("rtsp://admin:p@ss@10.0.0.5/s", "p@ss"),                 # '@' inside the password
        ("rtsps://root:s3cr3t!@cam.local/ch1?x=1", "s3cr3t"),
        ("open failed for rtsp://u:topsecret@h/p", "topsecret"),
        ("rtsp://h/p?password=abc123&x=1", "abc123"),
        ("OperationalError password=letmein host=db", "letmein"),
    ],
)
def test_redact_text_removes_secrets(raw, secret):
    cleaned = redact_text(raw)
    assert secret not in cleaned
    assert "***" in cleaned


def test_redact_keeps_host_and_user_for_diagnostics():
    assert redact_text("rtsp://admin:hunter2@10.0.0.5:554/stream") == \
        "rtsp://admin:***@10.0.0.5:554/stream"


def test_text_without_secrets_is_unchanged():
    assert redact_text("camera=ANN-ENT-01 connected") == "camera=ANN-ENT-01 connected"
    assert redact_text("") == ""


def test_camera_config_never_reveals_password():
    config = CameraConfig(1, "ANN-ENT-01", "Entrance", "rtsp://admin:hunter2@10.0.0.5/s")
    for text in (repr(config), str(config), f"{config}", config.safe_url):
        assert "hunter2" not in text
    assert "admin:***" in config.safe_url


def test_global_log_redaction_scrubs_messages_args_and_tracebacks(caplog):
    install_log_redaction()
    install_log_redaction()                     # idempotent
    logger = logging.getLogger("test.redaction")
    with caplog.at_level(logging.DEBUG, logger="test.redaction"):
        logger.info("connecting to %s", "rtsp://admin:hunter2@10.0.0.5/s")
        logger.error("plain rtsp://admin:hunter2@10.0.0.5/s in message")
        try:
            raise RuntimeError("failed opening rtsp://admin:hunter2@10.0.0.5/s")
        except RuntimeError:
            logger.exception("open failed")

    assert "hunter2" not in caplog.text
    assert "hunter2" not in "".join(r.getMessage() for r in caplog.records)
    assert "admin:***" in caplog.text


def test_log_event_format_and_redaction(caplog):
    install_log_redaction()
    logger = logging.getLogger("test.logevent")
    with caplog.at_level(logging.INFO, logger="test.logevent"):
        log_event(logger, logging.INFO, "AI", "ANN-ENT-01", "stats",
                  detections=8, tracks=7, fps=4.87)
        log_event(logger, logging.INFO, "CAMERA", "ANN-ENT-01", "failed",
                  detail="rtsp://a:leaked@h/x")
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == "[AI] camera=ANN-ENT-01 stats detections=8 tracks=7 fps=4.9"
    assert "leaked" not in lines[1]


# ------------------------------------------------ must not break other formatters
def _capture(formatter):
    """A handler that RAISES if formatting fails, instead of printing to stderr."""
    import io

    class Strict(logging.StreamHandler):
        def handleError(self, record):
            raise

    stream = io.StringIO()
    handler = Strict(stream)
    handler.setFormatter(formatter)
    return handler, stream


def test_uvicorn_access_log_formatting_still_works():
    """REGRESSION: an earlier version replaced record.args with None, which crashed
    uvicorn's AccessFormatter (it unpacks args as a 5-tuple) on every request."""
    uvicorn_logging = pytest.importorskip("uvicorn.logging")
    install_log_redaction()
    handler, stream = _capture(uvicorn_logging.AccessFormatter(
        '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    logger = logging.getLogger("test.uvicorn.access")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.setLevel(logging.INFO)
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/api/health", "1.1", 200)
    finally:
        logger.removeHandler(handler)
    assert stream.getvalue().strip() == '127.0.0.1:5000 - "GET /api/health HTTP/1.1" 200 OK'


def test_secret_in_an_access_log_path_is_redacted_without_breaking_the_formatter():
    uvicorn_logging = pytest.importorskip("uvicorn.logging")
    install_log_redaction()
    handler, stream = _capture(uvicorn_logging.AccessFormatter(
        '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    logger = logging.getLogger("test.uvicorn.access2")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.setLevel(logging.INFO)
        logger.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:1", "GET", "/x?password=hunter2", "1.1", 401)
    finally:
        logger.removeHandler(handler)
    output = stream.getvalue()
    assert "hunter2" not in output and "password=***" in output and "401" in output


def test_untouched_records_keep_their_original_args(caplog):
    install_log_redaction()
    logger = logging.getLogger("test.args")
    with caplog.at_level(logging.INFO, logger="test.args"):
        logger.info("value %s and %d", "plain", 5)
    record = caplog.records[0]
    assert record.args == ("plain", 5)                # shape preserved when nothing is secret
    assert record.getMessage() == "value plain and 5"


def test_a_template_that_looks_like_a_secret_does_not_cause_a_formatting_error(caplog):
    install_log_redaction()
    logger = logging.getLogger("test.template")
    with caplog.at_level(logging.INFO, logger="test.template"):
        logger.info("password=%s", "hunter2")
    assert "hunter2" not in caplog.text
    assert "password=***" in caplog.text


def test_secret_carried_by_a_non_string_argument_is_still_scrubbed(caplog):
    install_log_redaction()

    class Holder:
        def __str__(self):
            return "rtsp://admin:hunter2@10.0.0.5/s"

    logger = logging.getLogger("test.object")
    with caplog.at_level(logging.INFO, logger="test.object"):
        logger.info("source %s", Holder())
    assert "hunter2" not in caplog.text


def test_malformed_format_arguments_never_raise_from_the_factory():
    install_log_redaction()
    logger = logging.getLogger("test.malformed")
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    logger.info("needs two %s %s", "only-one")        # would raise if formatted; must not escape
