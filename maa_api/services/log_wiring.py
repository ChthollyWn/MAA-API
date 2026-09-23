"""Application-lifetime wiring for service, CoreClient and native logs."""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maa_api.services.asst_log_tailer import AsstLogTailer
from maa_api.services.log_hub import (
    LEVEL_ORDER,
    LogHub,
    LogRecord,
    LogHubHandler,
    attach_log_hub,
    get_log_hub,
    set_log_hub,
)
from maa_api.settings import REPO_ROOT, get_settings

if TYPE_CHECKING:
    from maa_api.services.callback_translator import CallbackTranslator

__all__ = [
    "install_core_logging",
    "install_service_logging",
    "resolve_core_log_path",
    "start_core_debug_tailer",
    "stop_logging",
]

_TARGET_LOGGERS = ("maa_api", "uvicorn", "uvicorn.access", "uvicorn.error")


def _hub_or_current(hub: LogHub | None) -> LogHub:
    selected = hub if hub is not None else get_log_hub()
    if selected is None:
        raise RuntimeError("LogHub 尚未启动")
    return selected


def install_service_logging(
    hub: LogHub | None = None, *, log_dir: str | Path | None = None
) -> None:
    """Install the existing console/file handlers and the shared LogHub handler."""
    selected = _hub_or_current(hub)
    # M4-03 made this import side-effect free. Configuration belongs to lifespan,
    # after settings and the temporary/production DB path are known.
    from maa_api.log import configure_file_logging

    configure_file_logging(log_dir)
    attach_log_hub(selected)


def install_core_logging(
    client: Any,
    hub: LogHub | None = None,
    translator: CallbackTranslator | None = None,
) -> CallbackTranslator:
    """Forward CoreClient LOG and CALLBACK payloads into the shared hub."""
    from maa_api.services.callback_translator import CallbackTranslator

    selected = _hub_or_current(hub)
    on = getattr(client, "on", None)
    if not callable(on):
        raise TypeError("client 必须提供 on(event_type, handler)")
    callback_translator = translator or CallbackTranslator()

    def on_log(payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        level = str(payload.get("level", "INFO")).upper()
        if level == "WARN":
            level = "WARNING"
        if level not in LEVEL_ORDER:
            level = "INFO"
        original_logger = str(payload.get("logger", "maa_api"))
        logger_name = (
            original_logger
            if original_logger.startswith("core_worker.")
            else f"core_worker.{original_logger}"
        )
        selected.offer(
            LogRecord(
                ts=float(payload.get("ts") or time.time()),
                source="service",
                level=level,
                content=str(payload.get("content", "")),
                logger=logger_name,
                raw={"core_worker": True},
            )
        )

    def on_callback(payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        try:
            msg = int(payload.get("msg"))
        except (TypeError, ValueError):
            msg = payload.get("msg")
        details = payload.get("details")
        if not isinstance(details, dict):
            details = {}
        for record in callback_translator.translate(msg, details):
            selected.offer(record)

    on("LOG", on_log)
    on("CALLBACK", on_callback)
    return callback_translator


def resolve_core_log_path(path: str | Path | None = None) -> Path:
    """Resolve configured or platform-default MaaCore debug log location."""
    if path is not None:
        return Path(path)
    settings = get_settings()
    core_root = Path(settings.maa_core_path) if settings.maa_core_path else (
        REPO_ROOT / "resource" / "lib" / "maa" / platform.system()
    )
    return core_root / "debug" / "asst.log"


def start_core_debug_tailer(
    hub: LogHub | None = None,
    *,
    path: str | Path | None = None,
    interval: float = 0.2,
) -> asyncio.Task[None]:
    """Start polling asst.log; missing files remain absent and are retried."""
    selected = _hub_or_current(hub)
    tailer = AsstLogTailer(
        resolve_core_log_path(path),
        selected,
        interval=interval,
        min_level=get_settings().log.core_min_level,
    )
    return asyncio.create_task(tailer.run(), name="maa-api-core-debug-tailer")


async def stop_logging(
    tailer_task: asyncio.Task[None] | None = None,
    hub: LogHub | None = None,
) -> None:
    """Close sockets, stop native tailing, detach handlers and flush the hub."""
    from maa_api.api.ws import manager

    selected = hub if hub is not None else get_log_hub()
    await manager.close_all(1001, "server_shutdown")
    manager.bind_hub(None)
    if tailer_task is not None:
        if not tailer_task.done():
            tailer_task.cancel()
        try:
            await tailer_task
        except asyncio.CancelledError:
            pass
    if selected is not None:
        for logger_name in _TARGET_LOGGERS:
            target = logging.getLogger(logger_name)
            for handler in tuple(target.handlers):
                if isinstance(handler, LogHubHandler) and handler.hub is selected:
                    target.removeHandler(handler)
        await selected.aclose()
    set_log_hub(None)
