"""Explicit logging configuration and import-safety contracts."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from maa_api import log as log_module
from maa_api.db import session as db_session


@pytest.fixture
def clean_configured_logging():
    service_logger = logging.getLogger("maa_api")
    original_level = service_logger.level
    try:
        yield
    finally:
        for name, handlers in log_module._attached.items():
            target = logging.getLogger(name)
            for handler in handlers:
                target.removeHandler(handler)
        for handler in log_module._handlers.values():
            handler.close()
        log_module._attached.clear()
        log_module._handlers.clear()
        log_module._configured_target = None
        service_logger.setLevel(original_level)


def test_import_has_no_filesystem_or_legacy_config_side_effects(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    code = (
        "import os, sys, maa_api.log; "
        "assert 'maa_api.config' not in sys.modules; "
        "assert os.listdir(os.getcwd()) == [], os.listdir(os.getcwd())"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_file_and_console_handlers_are_idempotent_and_mask_tokens(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    clean_configured_logging,
) -> None:
    logger = logging.getLogger("maa_api")

    log_module.configure_file_logging(tmp_path)
    first_handlers = dict(log_module._handlers)
    log_module.configure_file_logging(tmp_path)

    assert log_module._handlers == first_handlers
    assert len(log_module._handlers) == 3
    assert all(
        isinstance(log_module._handlers[name], RotatingFileHandler)
        for name in ("info", "error")
    )
    assert len(list(tmp_path.iterdir())) == 2
    assert sum(h is log_module._handlers["console"] for h in logger.handlers) == 1
    assert sum(h is log_module._handlers["info"] for h in logger.handlers) == 1
    assert sum(h is log_module._handlers["error"] for h in logger.handlers) == 1
    assert log_module._handlers["info"].maxBytes == 10**6  # type: ignore[attr-defined]
    assert log_module._handlers["info"].backupCount == 20  # type: ignore[attr-defined]

    logger.warning("GET /api/ws?token=secret123&x=1 HTTP/1.1")
    logger.error("GET /api/ws?token=secret456 HTTP/1.1")
    for handler in log_module._handlers.values():
        handler.flush()

    files = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()}
    combined = "\n".join(files.values())
    assert "secret123" not in combined
    assert "secret456" not in combined
    assert combined.count("token=***") == 3  # warning in info, error in both
    error_text = next(text for name, text in files.items() if name.endswith(".error.log"))
    assert "secret456" not in error_text and "token=***" in error_text
    assert "secret123" not in error_text  # warning is below the error file threshold

    console = capsys.readouterr().err
    assert "secret123" not in console and "secret456" not in console
    assert "token=***" in console


def test_default_log_directory_is_derived_from_current_database_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_configured_logging
) -> None:
    resource = tmp_path / "isolated-resource"
    monkeypatch.setattr(db_session, "DB_PATH", resource / "test.db")

    log_module.configure_file_logging()

    assert sorted(path.name for path in (resource / "log").iterdir()) == sorted(
        [
            f"{datetime.now():%Y-%m-%d}.log",
            f"{datetime.now():%Y-%m-%d}.error.log",
        ]
    )


def test_reconfiguration_replaces_prior_directory_without_duplicate_handlers(
    tmp_path: Path, clean_configured_logging
) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    log_module.configure_file_logging(old)
    previous = tuple(log_module._handlers.values())

    log_module.configure_file_logging(new)

    logger = logging.getLogger("maa_api")
    assert len(list(old.iterdir())) == 2
    assert len(list(new.iterdir())) == 2
    assert all(handler not in logger.handlers for handler in previous)
    assert sum(isinstance(handler, RotatingFileHandler) for handler in logger.handlers) == 2
