"""MaaCore debug log path resolution.

The path was derived from the kernel directory (``app.maa_core_path`` /
``resource/lib/maa/<platform>``) while MaaCore actually writes
``<user_dir>/debug/asst.log`` after ``AsstSetUserDir``. The two derivations
disagreed, so the tailer watched a file nothing wrote and the 核心 log view
stayed empty. These tests pin the single source of truth.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import pytest

import maa_api.settings as settings_module
from maa_api.services.log_hub import LogRecord
from maa_api.services.log_wiring import resolve_core_log_path, start_core_debug_tailer
from maa_api.settings import LogSettings, Settings, core_user_dir, set_settings


class _RecordingHub:
    """只收集记录的最小 hub 替身（``AsstLogTailer`` 只调用 ``offer``）。"""

    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def offer(self, record: LogRecord) -> None:
        self.records.append(record)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """本模块注入过进程内配置缓存，用完必须清掉，避免污染同会话的其他用例。"""

    yield
    set_settings(None)


def test_default_path_follows_the_core_user_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "REPO_ROOT", tmp_path)

    assert core_user_dir() == tmp_path / "resource" / "maa-api"
    assert resolve_core_log_path() == tmp_path / "resource" / "maa-api" / "debug" / "asst.log"


def test_default_path_ignores_the_kernel_directory(tmp_path, monkeypatch) -> None:
    """``maa_core_path`` 只决定内核位置，不决定日志位置。"""

    monkeypatch.setattr(settings_module, "REPO_ROOT", tmp_path)
    kernel_dir = tmp_path / "resource" / "lib" / "maa" / "Darwin"
    set_settings(Settings(maa_core_path=str(kernel_dir)))

    resolved = resolve_core_log_path()

    assert resolved == tmp_path / "resource" / "maa-api" / "debug" / "asst.log"
    assert kernel_dir not in resolved.parents


def test_explicit_path_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom" / "asst.log"

    assert resolve_core_log_path(explicit) == explicit
    assert resolve_core_log_path(str(explicit)) == explicit


def test_tailer_ingests_lines_written_to_the_default_path(tmp_path, monkeypatch) -> None:
    """端到端：内核按 ``AsstSetUserDir`` 往 ``<user_dir>/debug/asst.log`` 写的内容，
    必须成为 ``core`` 来源的日志。

    这正是曾经断掉的一环 —— tailer 盯的是内核目录下的另一份文件，于是核心日志
    永远是空的。这里走的是 lifespan 用的同一个入口（``start_core_debug_tailer``）。
    """

    monkeypatch.setattr(settings_module, "REPO_ROOT", tmp_path)
    asst_log = core_user_dir() / "debug" / "asst.log"
    hub = _RecordingHub()
    set_settings(Settings(log=LogSettings(core_min_level="INF")))

    async def scenario() -> list[LogRecord]:
        task = start_core_debug_tailer(hub, interval=0.01)
        try:
            await asyncio.sleep(0.05)  # 让首轮探测把缺失文件当作「从零开始」
            asst_log.parent.mkdir(parents=True, exist_ok=True)
            asst_log.write_text(
                "[2026-09-29 09:37:19.023][WRN][Px92601][Tx41915] 内核写入的一行\n",
                encoding="utf-8",
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if hub.records:
                    break
                await asyncio.sleep(0.02)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return list(hub.records)

    records = asyncio.run(scenario())

    assert [record.content for record in records] == ["内核写入的一行"]
    assert records[0].source == "core"
    assert records[0].level == "WARNING"
    assert records[0].ts == pytest.approx(
        datetime(2026, 9, 29, 9, 37, 19, 23000).timestamp()
    )
