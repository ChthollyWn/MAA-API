"""产物服务：外置输出、笔记与报告的存取（M75 规格 §4.5、v3 06 §6.2）。

正文不超过 256 KiB 时直接进 ``artifact.content``；更大的写到
``<resource>/artifacts/<id>``，先写 ``.part`` 再 ``os.replace``，库里只留相对
``resource/`` 的路径。单个产物上限 16 MiB，超过报 413 ``ARTIFACT_TOO_LARGE``。

``list`` 只取元数据（正文列延迟加载），列表与工单详情不应把正文带出去；``get``
返回正文并支持按字符数截断，HTTP 层再决定要不要用 64 KiB 的上限。
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from maa_api.db import session as db_session
from maa_api.db.models import Artifact, utcnow
from maa_api.db.repositories.base import BaseRepository, Page
from maa_api.domain.enums import ArtifactKind, ArtifactStorage
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "DB_STORAGE_MAX_BYTES",
    "MAX_ARTIFACT_BYTES",
    "SPILL_RETENTION_DAYS",
    "ArtifactDetail",
    "ArtifactService",
]

logger = logging.getLogger(__name__)

#: 正文进 ``content`` 列的上限：256 KiB。
DB_STORAGE_MAX_BYTES = 256 * 1024
#: 单个产物的硬上限：16 MiB（超过 413）。
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
#: 未置顶 spill 的保留天数（v3 03 §14）。
SPILL_RETENTION_DAYS = 30
#: 文件存储的相对目录（相对 ``resource/``）。
ARTIFACTS_DIR = "artifacts"


@dataclass(frozen=True, slots=True)
class ArtifactDetail:
    """一份产物与它的正文；``truncated`` 表示按 ``max_chars`` 截过。"""

    artifact: Artifact
    content: str
    truncated: bool


def _storage_value(value: Any) -> str:
    return value.value if isinstance(value, ArtifactStorage) else str(value)


def _kind_value(value: Any) -> str:
    return value.value if isinstance(value, ArtifactKind) else str(value)


class ArtifactService:
    """产物表的读写与保留清理；不持有全局状态，库与资源根由装配方注入。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        resource_root: Path | str | None = None,
        broadcast: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._resource_root = (
            None if resource_root is None else Path(resource_root)
        )
        #: ``ws_manager.broadcast`` 的注入；测试可传记录器。
        self._broadcast = broadcast

    @property
    def resource_root(self) -> Path:
        """``resource/`` 根；未显式注入时与保留服务同一口径，按库路径推导。"""
        if self._resource_root is not None:
            return self._resource_root
        return Path(db_session.DB_PATH).parent

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    async def put(
        self,
        *,
        kind: ArtifactKind | str,
        title: str,
        body: bytes,
        mime: str = "text/markdown",
        session_id: str | None = None,
        ticket_id: str | None = None,
        run_id: str | None = None,
        meta: dict[str, Any] | None = None,
        pinned: bool = False,
    ) -> Artifact:
        """写一份产物；超过 16 MiB 抛 ``ARTIFACT_TOO_LARGE``。"""
        size_bytes = len(body)
        if size_bytes > MAX_ARTIFACT_BYTES:
            raise AppError(
                ErrorCode.ARTIFACT_TOO_LARGE,
                f"产物超过 {MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB 上限",
                {"size_bytes": size_bytes, "limit": MAX_ARTIFACT_BYTES},
            )
        artifact = Artifact(
            kind=_kind_value(kind),
            title=title,
            mime=mime,
            size_bytes=size_bytes,
            sha256=hashlib.sha256(body).hexdigest(),
            session_id=session_id,
            ticket_id=ticket_id,
            run_id=run_id,
            meta=dict(meta or {}),
            pinned=pinned,
        )
        written: Path | None = None
        if size_bytes <= DB_STORAGE_MAX_BYTES:
            artifact.storage = _storage_value(ArtifactStorage.DB)
            artifact.content = body.decode("utf-8", "replace")
            artifact.path = None
        else:
            artifact.storage = _storage_value(ArtifactStorage.FILE)
            artifact.content = None
            artifact.path = f"{ARTIFACTS_DIR}/{artifact.id}"
            written = self._write_file(artifact.id, body)
        try:
            async with self._session_factory() as db:
                db.add(artifact)
                await db.commit()
        except BaseException:
            if written is not None:
                written.unlink(missing_ok=True)
            raise
        await self._broadcast_created(artifact)
        return artifact

    async def _broadcast_created(self, artifact: Artifact) -> None:
        """广播 ``agent_event`` / ``artifact_created``；广播失败只记日志。"""
        if self._broadcast is None:
            return
        payload = {
            "session_id": artifact.session_id,
            "event": "artifact_created",
            "payload": {
                "run_id": artifact.run_id,
                "artifact_id": str(artifact.id),
                "kind": _kind_value(artifact.kind),
                "title": artifact.title,
            },
        }
        try:
            result = self._broadcast("agent_event", payload)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 产物已落库，通知失败不能回滚写入
            logger.warning(
                "产物创建事件广播失败：artifact=%s", artifact.id, exc_info=True
            )

    def _write_file(self, artifact_id: str, body: bytes) -> Path:
        """先写 ``.part`` 再 ``os.replace``；返回最终路径。"""
        directory = self.resource_root / ARTIFACTS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        part = directory / f"{artifact_id}.part"
        final = directory / artifact_id
        try:
            part.write_bytes(body)
            os.replace(part, final)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return final

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    async def has(self, artifact_id: str) -> bool:
        async with self._session_factory() as db:
            return await db.get(Artifact, artifact_id) is not None

    async def get(
        self,
        artifact_id: str,
        *,
        max_chars: int | None = None,
    ) -> ArtifactDetail:
        """按 id 取元数据与正文；``max_chars`` 设了上限时截断并置 ``truncated``。"""
        async with self._session_factory() as db:
            artifact = await db.get(Artifact, artifact_id)
            if artifact is None:
                raise _not_found()
            text = await self._read_body(artifact)
        if max_chars is not None:
            max_chars = max(int(max_chars), 0)
        truncated = max_chars is not None and len(text) > max_chars
        if truncated and max_chars is not None:
            text = text[:max_chars]
        return ArtifactDetail(artifact=artifact, content=text, truncated=truncated)

    async def download(self, artifact_id: str) -> tuple[bytes, str]:
        """原始字节与 MIME；文件存储直接读盘。"""
        async with self._session_factory() as db:
            artifact = await db.get(Artifact, artifact_id)
            if artifact is None:
                raise _not_found()
            if _storage_value(artifact.storage) == ArtifactStorage.DB.value:
                return (artifact.content or "").encode("utf-8"), artifact.mime
            path = self._resolve_file(artifact)
            if path is None or not path.is_file():
                raise _not_found()
            return path.read_bytes(), artifact.mime

    async def list(
        self,
        *,
        ticket_id: str | None = None,
        session_id: str | None = None,
        kind: ArtifactKind | str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[Artifact]:
        """按工单/会话/种类分页列出元数据；正文列延迟加载，不随列表带出。"""
        conditions = []
        if ticket_id is not None:
            conditions.append(Artifact.ticket_id == ticket_id)
        if session_id is not None:
            conditions.append(Artifact.session_id == session_id)
        if kind is not None:
            conditions.append(Artifact.kind == _kind_value(kind))
        items = (
            select(Artifact)
            .options(defer(Artifact.content))
            .where(*conditions)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
        count = select(func.count()).select_from(Artifact).where(*conditions)
        async with self._session_factory() as db:
            return await BaseRepository(db).paginate(
                items, count, page=page, size=size
            )

    # ------------------------------------------------------------------
    # 更新与保留
    # ------------------------------------------------------------------
    async def pin(self, artifact_id: str, *, pinned: bool) -> Artifact:
        """置顶/取消置顶；置顶的 spill 不被保留策略删除。"""
        async with self._session_factory() as db:
            artifact = await db.get(Artifact, artifact_id)
            if artifact is None:
                raise _not_found()
            artifact.pinned = bool(pinned)
            await db.commit()
            return artifact

    async def update_note(
        self,
        artifact_id: str,
        *,
        content: str,
        title: str | None = None,
    ) -> Artifact:
        """覆盖一条笔记的正文、摘要与更新时间；只接受 ``kind=note``。"""
        async with self._session_factory() as db:
            artifact = await db.get(Artifact, artifact_id)
            if artifact is None or _kind_value(artifact.kind) != ArtifactKind.NOTE.value:
                raise _not_found()
            old_path = artifact.path
            body = content.encode("utf-8")
            artifact.content = content
            artifact.size_bytes = len(body)
            artifact.sha256 = hashlib.sha256(body).hexdigest()
            artifact.storage = _storage_value(ArtifactStorage.DB)
            artifact.path = None
            if title is not None:
                artifact.title = title
            artifact.updated_at = utcnow()
            await db.commit()
            if old_path:
                # 笔记永远存库；历史上若曾落文件，顺手清掉旧文件。
                path = self._resolve_path(old_path)
                if path is not None:
                    path.unlink(missing_ok=True)
            return artifact

    async def cleanup_spilled(
        self,
        *,
        now: datetime | None = None,
        retention_days: int = SPILL_RETENTION_DAYS,
    ) -> int:
        """删除 ``kind=spill``、未置顶且早于保留期的行与文件，返回删除条数。"""
        cutoff = (now or utcnow()) - timedelta(days=retention_days)
        async with self._session_factory() as db:
            result = await db.execute(
                select(Artifact)
                .where(
                    Artifact.kind == ArtifactKind.SPILL.value,
                    Artifact.pinned.is_(False),
                    Artifact.created_at < cutoff,
                )
                .execution_options(populate_existing=True)
            )
            rows = list(result.scalars().all())
            paths = [(artifact, self._resolve_file(artifact)) for artifact in rows]
            for artifact, _path in paths:
                await db.delete(artifact)
            await db.commit()
        for artifact, path in paths:
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "产物文件删除失败：artifact=%s path=%s",
                    artifact.id,
                    path,
                    exc_info=True,
                )
        return len(rows)

    # ------------------------------------------------------------------
    # 正文与路径
    # ------------------------------------------------------------------
    async def _read_body(self, artifact: Artifact) -> str:
        if _storage_value(artifact.storage) == ArtifactStorage.DB.value:
            return artifact.content or ""
        path = self._resolve_file(artifact)
        if path is None or not path.is_file():
            raise _not_found()
        return path.read_bytes().decode("utf-8", "replace")

    def _resolve_file(self, artifact: Artifact) -> Path | None:
        if not artifact.path:
            return None
        return self._resolve_path(artifact.path)

    def _resolve_path(self, relative: str) -> Path | None:
        """把库里的相对路径解析到资源根之下；越界（``..``/绝对路径）返回 ``None``。"""
        root = self.resource_root.resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            return None
        return candidate


def _not_found() -> AppError:
    return AppError(ErrorCode.ARTIFACT_NOT_FOUND, "产物不存在")
