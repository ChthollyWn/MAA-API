"""Cross-platform polling semantics for MaaCore's native log file."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from maa_api.services import asst_log_tailer as tailer_module
from maa_api.services.asst_log_tailer import AsstLogTailer
from maa_api.services.log_hub import LogRecord


def _line(level: str, message: str, *, second: int = 11) -> str:
    return f"[2026-09-16 10:25:{second:02d}.367][{level}][Px26316][Tx65169] {message}\n"


class CaptureHub:
    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def offer(self, record: LogRecord) -> None:
        self.records.append(record)


def test_line_regex_and_level_mapping_match_native_samples() -> None:
    match = tailer_module.LINE_RE.match(
        "[2026-09-16 10:25:11.367][TRC][Px26316][Tx65169] MaaCore Process Start"
    )

    assert match is not None
    assert match.groupdict() == {
        "ts": "2026-09-16 10:25:11.367",
        "level": "TRC",
        "pid": "26316",
        "tid": "65169",
        "msg": "MaaCore Process Start",
    }
    assert tailer_module.LEVEL_MAP == {
        "DBG": "DEBUG",
        "TRC": "DEBUG",
        "INF": "INFO",
        "WRN": "WARNING",
        "ERR": "ERROR",
    }


def test_first_open_skips_history_then_reads_appended_lines(tmp_path: Path) -> None:
    path = tmp_path / "asst.log"
    path.write_text(_line("INF", "old history"), encoding="utf-8")
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub)

    tailer._poll_once()
    assert hub.records == []
    old_size = path.stat().st_size
    with path.open("a", encoding="utf-8") as file:
        file.write(_line("INF", "new record"))
    tailer._poll_once()
    tailer._close()

    assert len(hub.records) == 1
    record = hub.records[0]
    assert record.content == "new record"
    assert record.source == "core" and record.level == "INFO"
    assert record.raw == {
        "raw": _line("INF", "new record").rstrip("\n"),
        "file": "asst.log",
        "offset": old_size,
    }


def test_rename_rotation_drains_old_descriptor_before_new_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "asst.log"
    path.write_text("", encoding="utf-8")
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub)
    tailer._poll_once()
    old_inode = path.stat().st_ino

    path.write_text(_line("INF", "old tail"), encoding="utf-8")
    tailer._poll_once()
    rotated = tmp_path / "asst.bak.log"
    os.rename(path, rotated)
    path.write_text(_line("ERR", "new file"), encoding="utf-8")
    assert path.stat().st_ino != old_inode
    tailer._poll_once()

    assert [record.content for record in hub.records] == ["old tail"]
    tailer._close()
    assert [record.content for record in hub.records] == ["old tail", "new file"]
    assert hub.records[0].raw["file"] == "asst.log"
    assert hub.records[0].raw["offset"] == 0
    assert hub.records[1].raw["offset"] == 0


def test_truncation_resets_offset_without_requiring_inode_change(tmp_path: Path) -> None:
    path = tmp_path / "asst.log"
    path.write_text("", encoding="utf-8")
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub)
    tailer._poll_once()
    inode = path.stat().st_ino

    path.write_text(_line("INF", "a" * 180), encoding="utf-8")
    tailer._poll_once()
    assert tailer._offset > 100
    assert hub.records == []

    path.write_text("plain short line\n", encoding="utf-8")
    assert path.stat().st_ino == inode
    tailer._poll_once()

    assert [record.content for record in hub.records] == ["a" * 180, "plain short line"]
    tailer._close()


def test_half_line_is_retried_and_continuations_join_previous_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "asst.log"
    path.write_text("", encoding="utf-8")
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub)
    tailer._poll_once()
    first = _line("ERR", "failure stack head").rstrip("\n")
    with path.open("ab") as file:
        file.write(first.encode("utf-8"))
    tailer._poll_once()
    assert hub.records == []
    assert tailer._offset == 0

    with path.open("ab") as file:
        file.write(b"\n  at native frame\n")
        file.write(_line("INF", "after stack").encode("utf-8"))
    tailer._poll_once()

    assert len(hub.records) == 1
    assert hub.records[0].content == "failure stack head\n  at native frame"
    assert hub.records[0].raw["raw"] == first + "\n  at native frame"
    tailer._close()
    assert hub.records[-1].content == "after stack"


def test_malformed_utf8_is_replaced_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "asst.log"
    path.write_bytes(b"")
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub)
    tailer._poll_once()

    prefix = b"[2026-09-16 10:25:11.367][ERR][Px26316][Tx65169] native "
    with path.open("ab") as file:
        file.write(prefix + b"\xff detail\n")
    tailer._poll_once()
    tailer._close()

    assert len(hub.records) == 1
    assert hub.records[0].content == "native \ufffd detail"
    assert "\ufffd" in hub.records[0].raw["raw"]


def test_min_level_filters_trace_and_debug_before_hub(tmp_path: Path) -> None:
    path = tmp_path / "asst.log"
    path.write_text("", encoding="utf-8")
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub, min_level="INF")
    tailer._poll_once()
    path.write_text(
        _line("TRC", "trace")
        + _line("DBG", "debug")
        + _line("INF", "info")
        + _line("WRN", "warning")
        + _line("ERR", "error"),
        encoding="utf-8",
    )
    tailer._poll_once()
    tailer._close()

    assert [record.content for record in hub.records] == ["info", "warning", "error"]
    assert [record.level for record in hub.records] == ["INFO", "WARNING", "ERROR"]


def test_missing_path_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "asst.log"
    tailer = AsstLogTailer(path, CaptureHub())

    tailer._poll_once()

    assert not path.exists()
    assert not path.parent.exists()


def test_file_created_after_initial_missing_probe_is_read_from_start(
    tmp_path: Path,
) -> None:
    path = tmp_path / "late" / "asst.log"
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub)

    tailer._poll_once()  # service starts before MaaCore creates its debug folder
    assert not path.exists() and not path.parent.exists()
    path.parent.mkdir(parents=True)
    path.write_text(_line("ERR", "first post-start core record"), encoding="utf-8")
    tailer._poll_once()
    tailer._close()

    assert [record.content for record in hub.records] == ["first post-start core record"]


def test_run_cancellation_closes_tail_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "asst.log"
    path.write_text("", encoding="utf-8")
    hub = CaptureHub()
    tailer = AsstLogTailer(path, hub)
    sleeps: list[float] = []

    async def cancel_after_one_poll(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(tailer_module.asyncio, "sleep", cancel_after_one_poll)

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError):
            await tailer.run()

    asyncio.run(scenario())

    assert sleeps == [0.2]
    assert tailer._fp is None
