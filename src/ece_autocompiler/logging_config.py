"""Logging setup and lightweight secret redaction.

Developer-facing logs are always English. Redaction is best-effort: it removes
common credential shapes (tokens, keys, passwords, signed URL query strings)
before a line reaches a handler, so build logs captured from third-party tools
do not leak secrets into build history.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key=value or key: value for common secret names.
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            r"auth[_-]?token|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*\S+"
        ),
        r"\1=<redacted>",
    ),
    # Authorization headers.
    (re.compile(r"(?i)\bAuthorization\s*:\s*\S+(\s+\S+)?"), "Authorization: <redacted>"),
    # Query strings that carry signatures or tokens.
    (
        re.compile(r"(?i)([?&](?:sig|signature|token|key|x-amz-signature)=)[^&\s]+"),
        r"\1<redacted>",
    ),
    # Bearer tokens.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer <redacted>"),
)


def redact(text: str) -> str:
    """Return *text* with common secret shapes replaced by '<redacted>'."""
    redacted = text
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
        except Exception:  # pragma: no cover - never let logging break the app
            pass
        return True


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger once and return the application logger."""
    logger = logging.getLogger("ece")
    if getattr(logger, "_ece_configured", False):
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    redaction = _RedactingFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redaction)
    logger.addHandler(console)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "ece-autocompiler.log",
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redaction)
            logger.addHandler(file_handler)
        except OSError:
            # A missing or read-only log directory must not stop the app.
            logger.debug("File logging disabled: could not create log handler")

    logger._ece_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ece.{name}")
