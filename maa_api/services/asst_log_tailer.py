"""Poll and translate the native MaaCore ``debug/asst.log`` file."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from maa_api.services.log_hub import LogRecord, LEVEL_ORDER

if TYPE_CHECKING:
    from maa_api.services.log_hub import LogHub

__all__ = ["LEVEL_MAP", "LINE_RE", "AsstLogTailer"]


LINE_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]"
    r"\[(?P<level>DBG|TRC|INF|WRN|ERR)\]"
    r"\[Px(?P<pid>\d+)\]\[Tx(?P<tid>\d+)\] ?(?P<msg>.*)$"
)
LEVEL_MAP = {
    "DBG": "DEBUG",
    "TRC": "DEBUG",
    "INF": "INFO",
    "WRN": "WARNING",
    "ERR": "ERROR",
}


class AsstLogTailer:
    """Incrementally read a native log with rotation and truncation detection."""

    def __init__(
        self,
        path: str | Path,
        hub: LogHub,
        interval: float = 0.2,
        min_level: str = "INF",
    ) -> None:
        self.path = Path(path)
        self.hub = hub
        self.interval = max(float(interval), 0.001)
        self.min_level = str(min_level).upper()
        self._fp = None
        self._inode: int | None = None
        self._offset: int = 0
        self._pending: LogRecord | None = None

    async def run(self) -> None:
        """Poll on a worker thread until cancelled; report errors without logging."""

        try:
            while True:
                try:
                    await asyncio.to_thread(self._poll_once)
                except Exception as exc:
                    print(f"[AsstLogTailer] {exc!r}", file=sys.stderr)
                    await asyncio.to_thread(self._close)
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            await asyncio.to_thread(self._close)
            raise

    def _poll_once(self) -> None:
        """Read newly complete lines and handle file identity changes."""

        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._close()
            return

        if self._fp is None:
            self._open(seek_to_end=True)
            self._drain()
            return

        if stat.st_ino != self._inode:
            # A rename keeps the old descriptor readable. Drain it before
            # switching to the new path so no bytes at the rotation edge vanish.
            self._drain()
            self._flush_pending()
            self._close_file()
            self._open(seek_to_end=False)
        elif stat.st_size < self._offset:
            # Truncation does not change inode. Preserve the old final record,
            # then restart the same file from byte zero.
            self._flush_pending()
            self._close_file()
            self._open(seek_to_end=False)

        self._drain()

    def _open(self, *, seek_to_end: bool) -> None:
        try:
            fp = self.path.open(
                "r", encoding="utf-8", errors="replace", newline=""
            )
        except FileNotFoundError:
            self._close_file()
            return
        self._fp = fp
        self._inode = os.fstat(fp.fileno()).st_ino
        if seek_to_end:
            fp.seek(0, os.SEEK_END)
        else:
            fp.seek(0)
        self._offset = fp.tell()

    def _close_file(self) -> None:
        fp, self._fp = self._fp, None
        self._inode = None
        self._offset = 0
        if fp is not None:
            fp.close()

    def _close(self) -> None:
        self._flush_pending()
        self._close_file()

    def _drain(self) -> None:
        fp = self._fp
        if fp is None:
            return
        while True:
            line_offset = fp.tell()
            line = fp.readline()
            if line == "":
                self._offset = fp.tell()
                return
            if not line.endswith("\n"):
                # Text-mode tell() returns the correct opaque seek cookie even
                # when errors="replace" expands a malformed byte sequence.
                fp.seek(line_offset)
                self._offset = line_offset
                return
            self._offset = fp.tell()
            self._consume(line.rstrip("\r\n"), offset=line_offset)

    def _consume(self, line: str, *, offset: int) -> None:
        match = LINE_RE.match(line)
        if match is None:
            if self._pending is None:
                self._offer_line(
                    content=line,
                    level="INFO",
                    raw=line,
                    offset=offset,
                    ts=datetime.now().timestamp(),
                )
            else:
                self._pending.content += "\n" + line
                assert self._pending.raw is not None
                self._pending.raw["raw"] += "\n" + line
            return

        # A new structured line closes the previous record, including any
        # continuation lines that followed it.
        self._flush_pending()
        native_level = match.group("level")
        level = LEVEL_MAP[native_level]
        if LEVEL_ORDER[level] < self._minimum_level():
            return
        parsed_at = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S.%f")
        self._pending = self._make_record(
            content=match.group("msg"),
            level=level,
            raw=line,
            offset=offset,
            ts=parsed_at.timestamp(),
        )

    def _minimum_level(self) -> int:
        level = LEVEL_MAP.get(self.min_level, self.min_level)
        return LEVEL_ORDER.get(level.upper(), LEVEL_ORDER["INFO"])

    def _make_record(
        self, *, content: str, level: str, raw: str, offset: int, ts: float
    ) -> LogRecord:
        return LogRecord(
            ts=ts,
            source="core",
            level=level,
            content=content,
            raw={"raw": raw, "file": self.path.name, "offset": offset},
        )

    def _offer_line(
        self, *, content: str, level: str, raw: str, offset: int, ts: float
    ) -> None:
        if LEVEL_ORDER[level] < self._minimum_level():
            return
        self.hub.offer(
            self._make_record(
                content=content, level=level, raw=raw, offset=offset, ts=ts
            )
        )

    def _flush_pending(self) -> None:
        pending, self._pending = self._pending, None
        if pending is not None:
            self.hub.offer(pending)

