"""Log hygiene: credential redaction and structured camera log lines.

RTSP URLs routinely embed a username and password. They must never reach a log,
whether they are logged deliberately, appear in an exception message, or are
printed by a third-party library. Redaction is therefore applied in a global
LogRecord factory: every record from every logger is scrubbed at creation, so
no handler or formatter can forget to do it.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

MASK = "***"

# scheme://user:password@host  - the password may itself contain '@', so it is
# matched greedily up to the LAST '@' before the path begins.
_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)"
    r"(?P<user>[^\s:/@]*)"
    r":(?P<password>[^\s/]*)@"
)
# scheme://user@host (username only) - left alone, nothing secret to hide.

# ?password=..., &token=..., ;key=...
_QUERY_SECRET = re.compile(
    r"(?i)(?P<prefix>[?&;](?:password|passwd|pwd|pass|token|secret|key|auth|apikey)=)"
    r"(?P<value>[^&\s;]+)"
)
# Bare "password=abc" / "password: abc" fragments, e.g. from driver messages.
_KEYWORD_SECRET = re.compile(
    r"(?i)(?P<prefix>\b(?:password|passwd|pwd)\s*[=:]\s*)(?P<value>[^\s,;'\"]+)"
)


def mask_url_credentials(url: str | None) -> str | None:
    """Replace the password in scheme://user:password@host with '***'."""
    if url is None:
        return None
    return redact_text(url)


def redact_text(text: str) -> str:
    """Scrub URL passwords and password-like tokens from arbitrary text."""
    if not text:
        return text
    text = _URL_CREDENTIALS.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:{MASK}@", text
    )
    text = _QUERY_SECRET.sub(lambda m: f"{m.group('prefix')}{MASK}", text)
    text = _KEYWORD_SECRET.sub(lambda m: f"{m.group('prefix')}{MASK}", text)
    return text


# --------------------------------------------------------------------------
# Global redaction via the LogRecord factory
# --------------------------------------------------------------------------
_install_lock = threading.Lock()
_installed = False


def _redact_args(args: Any) -> Any:
    """Redact string arguments in place, PRESERVING the container's shape.

    Some formatters depend on the exact structure of `record.args` - uvicorn's
    access-log formatter unpacks it as a 5-tuple - so it must stay a tuple/dict.
    """
    if isinstance(args, tuple):
        return tuple(redact_text(a) if isinstance(a, str) else a for a in args)
    if isinstance(args, dict):
        return {k: (redact_text(v) if isinstance(v, str) else v) for k, v in args.items()}
    return args


def _scrub_record(record: logging.LogRecord) -> None:
    """Remove secrets from a record without breaking other formatters."""
    try:
        message = record.getMessage()
    except Exception:  # malformed format args must never break logging
        record.msg, record.args = redact_text(str(record.msg)), None
        return

    cleaned = redact_text(message)
    if cleaned == message:
        return  # nothing secret: leave the record exactly as it was

    # A secret is present. Prefer redacting the arguments and keeping the template,
    # so args keeps its shape; verify that this really removed the secret.
    scrubbed_args = _redact_args(record.args)
    try:
        candidate = record.msg % scrubbed_args if scrubbed_args else str(record.msg)
    except Exception:
        candidate = None
    if candidate is not None and redact_text(candidate) == candidate:
        record.args = scrubbed_args
        return

    # The secret came from the template or a non-string argument (an exception,
    # an object): freeze the already-redacted text.
    record.msg, record.args = cleaned, None


def install_log_redaction() -> None:
    """Scrub every log record in the process. Idempotent and thread-safe."""
    global _installed
    with _install_lock:
        if _installed:
            return
        previous_factory = logging.getLogRecordFactory()

        def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = previous_factory(*args, **kwargs)
            _scrub_record(record)
            if record.exc_info and record.exc_info[0] is not None:
                # Render the traceback now so its text can be scrubbed too.
                rendered = logging.Formatter().formatException(record.exc_info)
                record.exc_text = redact_text(rendered)
                record.exc_info = None
            return record

        logging.setLogRecordFactory(redacting_factory)
        _installed = True


# --------------------------------------------------------------------------
# Structured lines:  [CAMERA] camera=ANN-ENT-01 connected
# --------------------------------------------------------------------------
def log_event(
    logger: logging.Logger,
    level: int,
    tag: str,
    camera_id: str | None,
    message: str,
    **fields: Any,
) -> None:
    """Emit '[TAG] camera=<code> message key=value ...'.

    Only camera *codes* are ever logged, never URLs. Values are stringified and
    redacted as a second line of defence.
    """
    if not logger.isEnabledFor(level):
        return
    parts = [f"[{tag}]"]
    if camera_id is not None:
        parts.append(f"camera={camera_id}")
    parts.append(message)
    for key, value in fields.items():
        if isinstance(value, float):
            value = f"{value:.1f}"
        parts.append(f"{key}={value}")
    logger.log(level, " ".join(redact_text(str(p)) for p in parts))
