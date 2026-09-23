"""Explicitly configured console and rotating file logging.

Importing this module is side-effect free. The service lifespan calls
``configure_file_logging`` after configuration and database paths are known.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from maa_api.services.log_hub import mask_token

__all__ = ["configure_file_logging", "logger"]

logger = logging.getLogger("maa_api")
_CONFIGURED_LOGGERS = ("maa_api", "uvicorn.access", "uvicorn.error")
_lock = threading.RLock()
_handlers: dict[str, logging.Handler] = {}
_attached: dict[str, tuple[logging.Handler, ...]] = {}
_configured_target: tuple[Path, str] | None = None


class _TokenMaskingFormatter(logging.Formatter):
    """Apply the shared credential redactor to the complete formatted message."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_token(super().format(record))


def configure_file_logging(
    log_dir: str | Path | None = None, *, level: int = logging.DEBUG
) -> None:
    """Attach console and daily rotating log files to service and uvicorn loggers.

    The operation is idempotent for a given directory and date. Passing a new
    directory later replaces and closes this function's previous handlers.
    When omitted, the log directory is ``DB_PATH.parent / "log"`` and is
    resolved at call time so isolated databases keep their logs isolated too.
    """

    global _configured_target, _handlers, _attached

    if log_dir is None:
        from maa_api.db import session as db_session

        target_dir = Path(db_session.DB_PATH).parent / "log"
    else:
        target_dir = Path(log_dir)
    target_dir = target_dir.expanduser().resolve()
    date_key = datetime.now().strftime("%Y-%m-%d")
    target = (target_dir, date_key)

    with _lock:
        target_dir.mkdir(parents=True, exist_ok=True)
        if _configured_target != target:
            _remove_managed_handlers()
            _handlers, _attached = _create_handlers(target_dir, date_key)
            _configured_target = target

        service_logger = logging.getLogger("maa_api")
        service_logger.setLevel(level)
        desired = {
            "maa_api": (
                _handlers["console"],
                _handlers["info"],
                _handlers["error"],
            ),
            "uvicorn.access": (_handlers["console"], _handlers["info"]),
            "uvicorn.error": (_handlers["console"], _handlers["error"]),
        }
        for name in _CONFIGURED_LOGGERS:
            target_logger = logging.getLogger(name)
            expected = desired[name]
            # Uvicorn can replace its logger configuration during startup. Add
            # any missing handler again while keeping our own calls idempotent.
            for handler in expected:
                if handler not in target_logger.handlers:
                    target_logger.addHandler(handler)
            _attached[name] = expected


def _create_handlers(
    log_dir: Path, date_key: str
) -> tuple[dict[str, logging.Handler], dict[str, tuple[logging.Handler, ...]]]:
    formatter = _TokenMaskingFormatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(formatter)

    info = RotatingFileHandler(
        log_dir / f"{date_key}.log",
        maxBytes=10**6,
        backupCount=20,
        encoding="utf-8",
    )
    info.setLevel(logging.INFO)
    info.setFormatter(formatter)

    error = RotatingFileHandler(
        log_dir / f"{date_key}.error.log",
        maxBytes=10**6,
        backupCount=20,
        encoding="utf-8",
    )
    error.setLevel(logging.ERROR)
    error.setFormatter(formatter)

    handlers: dict[str, logging.Handler] = {
        "console": console,
        "info": info,
        "error": error,
    }
    attached = {
        "maa_api": (console, info, error),
        "uvicorn.access": (console, info),
        "uvicorn.error": (console, error),
    }
    return handlers, attached


def _remove_managed_handlers() -> None:
    for name, handlers in _attached.items():
        target_logger = logging.getLogger(name)
        for handler in handlers:
            target_logger.removeHandler(handler)
    for handler in _handlers.values():
        try:
            handler.flush()
        finally:
            handler.close()
