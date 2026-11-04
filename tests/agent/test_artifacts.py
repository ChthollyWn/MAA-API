"""M75 T2：产物服务与笔记工具（规格 §4.5、§6）。

服务层用例覆盖存储阈值、``.part`` + ``os.replace``、sha256、413、pin、
``update_note`` 与 spill 保留；工具层覆盖 ``write_note`` 的新建、覆盖、
20 000 字限制、工单范围与 ``durable.notes`` 更新。``read_artifact`` 的续读
用例在 ``test_spill.py``（§6 的五条）。
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect as sa_inspect

from maa_api.agent.artifacts.service import (
    DB_STORAGE_MAX_BYTES,
    MAX_ARTIFACT_BYTES,
    ArtifactService,
)
from maa_api.agent.harness.context import CURRENT_RUN, RunContext
from maa_api.agent.registry import ToolContext
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tools.workspace import (
    NOTE_MAX_CHARS,
    WriteNoteParams,
    write_note,
)
from maa_api.db.models import Artifact, Ticket, utcnow
from maa_api.domain.enums import CallerType, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session


def _service(factory, root) -> ArtifactService:
    return ArtifactService(factory, resource_root=root / "resource")


def _spec(*tools: str):
    base = builtin_spec("general")
    return base.model_copy(update={"tools": (*base.tools, *tools)})


class _current_run:
    def __init__(self, run: RunContext) -> None:
        self._run = run
        self._token = None

    def __enter__(self) -> None:
        self._token = CURRENT_RUN.set(self._run)

    def __exit__(self, *exc_info: object) -> None:
        CURRENT_RUN.reset(self._token)


async def _seed_ticket(factory, title: str) -> str:
    ticket = Ticket(
        title=title,
        kind="general",
        status=TicketStatus.OPEN.value,
        origin="user",
    )
    async with factory() as db:
        db.add(ticket)
        await db.commit()
    return str(ticket.id)


def _run(
    *,
    session_id: str,
    ticket_id: str | None,
    app: object,
    tools: tuple[str, ...] = ("read_artifact", "write_note"),
) -> RunContext:
    return RunContext(
        run_id="run-artifacts",
        root_run_id="run-artifacts",
        parent_run_id=None,
        session_id=session_id,
        ticket_id=ticket_id,
        spec=_spec(*tools),
        app=app,
    )


def _tool_context(run: RunContext) -> ToolContext:
    return ToolContext(CallerType.INTERNAL, run.session_id, None, None, None)


# ---------------------------------------------------------------------------
# 服务层
# ---------------------------------------------------------------------------


def test_put_small_body_stores_content_and_sha256(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "small.db")
        try:
            service = _service(factory, tmp_path)
            body = "中文正文\n".encode()
            artifact = await service.put(
                kind="note", title="笔记", body=body, session_id=None, ticket_id=None
            )
            assert artifact.storage == "db"
            assert artifact.content == body.decode()
            assert artifact.size_bytes == len(body)
            assert artifact.sha256 == hashlib.sha256(body).hexdigest()
            assert artifact.path is None

            detail = await service.get(str(artifact.id))
            assert detail.content == body.decode()
            assert detail.truncated is False

            truncated = await service.get(str(artifact.id), max_chars=3)
            assert truncated.content == body.decode()[:3]
            assert truncated.truncated is True
            assert not (tmp_path / "resource" / "artifacts").exists()
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_put_over_256_kib_writes_file_atomically(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "file.db")
        try:
            service = _service(factory, tmp_path)
            body = b"x" * (DB_STORAGE_MAX_BYTES + 1)
            artifact = await service.put(
                kind="spill",
                title="大输出",
                body=body,
                mime="application/json",
                session_id=None,
                ticket_id=None,
            )
            assert artifact.storage == "file"
            assert artifact.content is None
            assert artifact.path == f"artifacts/{artifact.id}"
            stored = tmp_path / "resource" / artifact.path
            assert stored.read_bytes() == body
            # 先写 .part 再 os.replace：最终目录里不留下临时文件。
            assert not list((tmp_path / "resource" / "artifacts").glob("*.part"))

            data, mime = await service.download(str(artifact.id))
            assert data == body
            assert mime == "application/json"
            detail = await service.get(str(artifact.id))
            assert detail.content == body.decode()

            page = await service.list()
            assert page.total == 1
            # 列表只取元数据，正文列是延迟加载的。
            assert "content" in sa_inspect(page.items[0]).unloaded
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_put_over_16_mib_is_413(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "large.db")
        try:
            service = _service(factory, tmp_path)
            with pytest.raises(AppError) as caught:
                await service.put(
                    kind="spill",
                    title="太大",
                    body=b"x" * (MAX_ARTIFACT_BYTES + 1),
                )
            assert caught.value.code == ErrorCode.ARTIFACT_TOO_LARGE
            assert caught.value.http_status == 413
            page = await service.list()
            assert page.total == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_pin_and_update_note_refresh_sha_and_time(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "note.db")
        try:
            service = _service(factory, tmp_path)
            note = await service.put(kind="note", title="草稿", body=b"v1")
            before = note.updated_at

            pinned = await service.pin(str(note.id), pinned=True)
            assert pinned.pinned is True

            updated = await service.update_note(
                str(note.id), content="v2 新内容", title="新标题"
            )
            assert updated.title == "新标题"
            assert updated.content == "v2 新内容"
            assert updated.sha256 == hashlib.sha256("v2 新内容".encode()).hexdigest()
            assert updated.updated_at >= before
            detail = await service.get(str(note.id))
            assert detail.content == "v2 新内容"

            spill = await service.put(kind="spill", title="输出", body=b"x")
            with pytest.raises(AppError) as caught:
                await service.update_note(str(spill.id), content="不能改")
            assert caught.value.code == ErrorCode.ARTIFACT_NOT_FOUND
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_list_filters_and_paginates_without_body(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "list.db")
        try:
            session_id = await seed_session(factory, kind="worker")
            service = _service(factory, tmp_path)
            for index in range(3):
                await service.put(
                    kind="note" if index < 2 else "spill",
                    title=f"产物 {index}",
                    body=f"正文 {index}".encode(),
                    session_id=session_id,
                )
            service_page = await service.list(
                session_id=session_id, kind="note", page=2, size=1
            )
            assert service_page.total == 2
            assert (service_page.page, service_page.size) == (2, 1)
            assert len(service_page.items) == 1
            assert "content" in sa_inspect(service_page.items[0]).unloaded
            assert (await service.list(session_id="other-session")).total == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_cleanup_spilled_removes_only_old_unpinned_and_files(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "retention.db")
        try:
            service = _service(factory, tmp_path)
            old = await service.put(
                kind="spill", title="旧的大输出", body=b"y" * (DB_STORAGE_MAX_BYTES + 1)
            )
            pinned = await service.put(kind="spill", title="置顶", body=b"keep")
            await service.pin(str(pinned.id), pinned=True)
            recent = await service.put(kind="spill", title="最近", body=b"keep")
            note = await service.put(kind="note", title="笔记", body=b"keep")

            cutoff = utcnow() - timedelta(days=31)
            async with factory() as db:
                for artifact_id in (old.id, pinned.id, note.id):
                    row = await db.get(Artifact, artifact_id)
                    assert row is not None
                    row.created_at = cutoff
                await db.commit()

            deleted = await service.cleanup_spilled()
            assert deleted == 1
            assert not (tmp_path / "resource" / old.path).exists()
            page = await service.list()
            assert {item.id for item in page.items} == {pinned.id, recent.id, note.id}
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 工具层
# ---------------------------------------------------------------------------


def test_write_note_creates_updates_and_tracks_durable_notes(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "write.db")
        try:
            ticket_id = await _seed_ticket(factory, "写笔记")
            session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
            run_id = await seed_run(factory, session_id=session_id, ticket_id=ticket_id)
            service = _service(factory, tmp_path)
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            run = _run(session_id=session_id, ticket_id=ticket_id, app=app)
            run.run_id = run_id
            ctx = _tool_context(run)

            with _current_run(run):
                created = await write_note(
                    WriteNoteParams(title="排查笔记", content="第一版"), ctx
                )
                artifact_id = str(created["artifact_id"])
                assert created["updated"] is False
                assert run.state["durable"].notes == [
                    {"id": artifact_id, "title": "排查笔记"}
                ]
                detail = await service.get(artifact_id)
                assert detail.artifact.kind == "note"
                assert detail.artifact.mime == "text/markdown"
                assert detail.artifact.session_id == session_id
                assert detail.artifact.ticket_id == ticket_id
                first_sha = detail.artifact.sha256

                updated = await write_note(
                    WriteNoteParams(
                        title="排查笔记（改）",
                        content="第二版",
                        artifact_id=artifact_id,
                    ),
                    ctx,
                )
                assert updated["artifact_id"] == artifact_id
                assert updated["updated"] is True
                assert run.state["durable"].notes == [
                    {"id": artifact_id, "title": "排查笔记（改）"}
                ]
                detail = await service.get(artifact_id)
                assert detail.content == "第二版"
                assert detail.artifact.sha256 != first_sha

                before = (await service.list(kind="note")).total
                too_long = await write_note(
                    WriteNoteParams(
                        title="太长", content="x" * (NOTE_MAX_CHARS + 1)
                    ),
                    ctx,
                )
                assert too_long["error"]["code"] == "note_too_long"
                assert (await service.list(kind="note")).total == before
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_write_note_cannot_overwrite_another_ticket(tmp_path) -> None:
    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "note-scope.db")
        try:
            ticket_a = await _seed_ticket(factory, "A")
            ticket_b = await _seed_ticket(factory, "B")
            session_a = await seed_session(factory, kind="worker", ticket_id=ticket_a)
            session_b = await seed_session(factory, kind="worker", ticket_id=ticket_b)
            run_a = await seed_run(factory, session_id=session_a, ticket_id=ticket_a)
            run_b = await seed_run(factory, session_id=session_b, ticket_id=ticket_b)
            service = _service(factory, tmp_path)
            app = SimpleNamespace(state=SimpleNamespace(artifact_service=service))
            owner = _run(session_id=session_a, ticket_id=ticket_a, app=app)
            owner.run_id = run_a
            with _current_run(owner):
                note = await write_note(
                    WriteNoteParams(title="A 的笔记", content="内容"),
                    _tool_context(owner),
                )

            stranger = _run(session_id=session_b, ticket_id=ticket_b, app=app)
            stranger.run_id = run_b
            with _current_run(stranger), pytest.raises(AppError) as caught:
                await write_note(
                    WriteNoteParams(
                        title="偷改",
                        content="内容",
                        artifact_id=str(note["artifact_id"]),
                    ),
                    _tool_context(stranger),
                )
            assert caught.value.code == ErrorCode.ARTIFACT_NOT_FOUND
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_put_broadcasts_artifact_created(tmp_path) -> None:
    """写入成功后广播 ``agent_event`` / ``artifact_created``（spec §4.6）。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "event.db")
        try:
            ticket_id = await _seed_ticket(factory, "事件")
            session_id = await seed_session(factory, kind="worker", ticket_id=ticket_id)
            run_id = await seed_run(factory, session_id=session_id, ticket_id=ticket_id)
            events: list[tuple[str, dict]] = []

            async def broadcast(message_type: str, data: dict) -> None:
                events.append((message_type, data))

            service = ArtifactService(
                factory, resource_root=tmp_path / "resource", broadcast=broadcast
            )
            artifact = await service.put(
                kind="note",
                title="事件笔记",
                body="正文".encode(),
                session_id=session_id,
                ticket_id=ticket_id,
                run_id=run_id,
            )
            assert events == [
                (
                    "agent_event",
                    {
                        "session_id": session_id,
                        "event": "artifact_created",
                        "payload": {
                            "run_id": run_id,
                            "artifact_id": str(artifact.id),
                            "kind": "note",
                            "title": "事件笔记",
                        },
                    },
                )
            ]
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
