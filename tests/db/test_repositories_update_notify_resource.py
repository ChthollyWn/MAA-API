"""``UpdateRepository`` / ``NotifyChannelRepository`` / ``ResourceAssetRepository``
的仓储行为测试（M2-11）。

覆盖 docs/04 §5.11 / §5.12 / §5.13 / §6 / §9 的硬约束：

- ``update_record``：部分唯一索引 ``uq_update_record_running_target`` 在数据库层
  拒绝同 target 的第二条 ``running``（``IntegrityError``），但允许不同 target 的
  running、也允许同 target 的多条历史；``current`` 只认 running；``update_progress``
  只写给到的列、``None`` 表示不改，终态记录拒绝迟到的进度；``mark_terminal`` 只
  接受终态且终态不可再变（``bool`` = 状态机是否接受）；
- ``notify_channel``：``config`` 是 JSON 原文存取、结构校验在服务层（数据库不拦）；
  ``(type, name)`` 唯一在数据库层兜底；``record_send`` 回写三列并在成功时清空
  ``last_error``，失败不停用通道；
- ``resource_asset``：CHECK 约束保证 ``content`` / ``path`` 至少有一个（insert 与
  update 两条路径都生效）；``(kind, name)`` 唯一；``upsert_by_kind_name`` 一条语句
  完成 insert-or-update、保留 ``id`` / ``created_at``、拒绝未知与业务键字段；
  ``last_checked_at`` 单独更新不刷新 ``updated_at``（两者不是一回事）；
- 三个仓储都不 ``commit()``：回滚后写入一起消失。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库与会话来自 ``tests/db/conftest.py``；约束类用例每次换新 session 并 rollback
（``IntegrityError`` 后原 session 的当前事务已失效）。
"""

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from maa_api.db.models import NotifyChannel, ResourceAsset, UpdateRecord, utcnow
from maa_api.db.repositories.notify import NotifyChannelRepository
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.db.repositories.update import UpdateRepository
from maa_api.domain.enums import (
    NotifyChannelType,
    NotifyEvent,
    ReleaseChannel,
    ResourceAssetKind,
    ResourceChannel,
    UpdatePhase,
    UpdateStatus,
    UpdateTarget,
)

PAST = datetime(2000, 1, 1, 0, 0, 0)


def _update(**overrides) -> UpdateRecord:
    fields = {
        "target": UpdateTarget.CORE,
        "triggered_by": "manual",
        "status": UpdateStatus.RUNNING,
    }
    fields.update(overrides)
    return UpdateRecord(**fields)


def _channel(**overrides) -> NotifyChannel:
    fields = {
        "type": NotifyChannelType.EMAIL,
        "name": "主邮箱",
        "config": {},
        "events": [],
    }
    fields.update(overrides)
    return NotifyChannel(**fields)


def _asset(**overrides) -> ResourceAsset:
    fields = {
        "kind": ResourceAssetKind.CUSTOM_TASK,
        "name": "我的作业",
        "content": {"task": "Custom_X"},
    }
    fields.update(overrides)
    return ResourceAsset(**fields)


async def _expect_integrity_error(coro) -> None:
    """断言 ``coro`` 在数据库层撞约束；没撞就是约束没生效，直接失败。"""
    try:
        await coro
    except IntegrityError:
        return
    raise AssertionError("期望数据库约束抛 IntegrityError，但没有")


async def _count(session, table: str, where: str = "", params: dict | None = None) -> int:
    sql = f"select count(*) from {table}" + (f" where {where}" if where else "")
    return (await session.execute(text(sql), params or {})).scalar_one()


async def _backdate(session, table: str, row_id: str) -> None:
    """把 ``updated_at`` 退到 2000 年，让「是否刷新」可判别（不依赖相邻 now 的差）。

    绑定字符串而不是 ``datetime`` 对象：``sqlite3`` 自 3.12 起弃用内建 datetime
    适配器，裸 ``text()`` 参数传 datetime 会发 DeprecationWarning（M2-09 已记录
    「裸 text() 不套类型处理器」，这里顺带避开适配器告警）。
    """
    await session.execute(
        text(f"update {table} set updated_at = :t where id = :i"),
        {"t": "2000-01-01 00:00:00", "i": row_id},
    )


# ---------------------------------------------------------------------------
# update_record：创建、部分唯一索引、current
# ---------------------------------------------------------------------------


def test_update_create_running_sets_started_at_and_stays_readable(db_session_factory):
    """``create`` 补 ``started_at``；提交后入参与返回副本的字段都仍可直接读。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            record = _update(
                channel=ReleaseChannel.STABLE, from_version="v4.0", to_version="v5.0"
            )
            stored = await repo.create(record)
            assert stored.id == record.id
            assert stored.status == UpdateStatus.RUNNING
            assert stored.started_at is not None  # running 记录的开始时刻
            assert stored.progress == 0 and stored.phase is None
            assert stored.finished_at is None and stored.bytes_done is None
            await session.commit()
            # merge() 的语义（M2-07 实测）：提交后字段仍可读，不需要再 await 刷新
            assert record.id and record.created_at is not None
            assert stored.started_at is not None

            got = await repo.get(stored.id)
            assert got is not None
            assert got.triggered_by == "manual"
            assert got.channel == ReleaseChannel.STABLE

            # pending 记录还没开始，不补 started_at
            pending = await repo.create(_update(target=UpdateTarget.GAME, status=UpdateStatus.PENDING))
            assert pending.started_at is None
            await session.commit()

        async with db_session_factory() as session:
            got = await UpdateRepository(session).get(pending.id)
            assert got.status == UpdateStatus.PENDING and got.started_at is None

    asyncio.run(scenario())


def test_update_running_target_is_unique_in_database(db_session_factory):
    """同 target 的第二条 running 由部分唯一索引拒绝，其他形态都放行。"""

    async def scenario():
        async with db_session_factory() as session:
            await UpdateRepository(session).create(_update())
            await session.commit()

        # 同 target 再开一次：数据库层拦截（服务层据此转 409 UPDATE_ALREADY_RUNNING）
        async with db_session_factory() as session:
            await _expect_integrity_error(UpdateRepository(session).create(_update()))
            await session.rollback()

        # 不同 target 的 running 互不干扰
        async with db_session_factory() as session:
            await UpdateRepository(session).create(
                _update(target=UpdateTarget.RESOURCE, channel=ResourceChannel.OTA)
            )
            await session.commit()

        # 同 target 的非 running 记录不受部分索引约束：历史可以有很多条
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            await repo.create(_update(status=UpdateStatus.SUCCESS))
            await repo.create(_update(status=UpdateStatus.FAILED, triggered_by="agent"))
            await session.commit()
            assert await _count(session, "update_record") == 4

        # 失败的那次没有把 running 行挤掉：同 target 仍只有一条 running
        async with db_session_factory() as session:
            assert (
                await _count(
                    session,
                    "update_record",
                    "target = :t and status = 'running'",
                    {"t": UpdateTarget.CORE},
                )
                == 1
            )

    asyncio.run(scenario())


def test_update_current_returns_only_running(db_session_factory):
    """``current`` 只认 running：终态记录（哪怕更新）返回 ``None``。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            assert await repo.current(UpdateTarget.CORE) is None

            first = await repo.create(_update())
            await session.commit()
            assert (await repo.current(UpdateTarget.CORE)).id == first.id
            # 别的 target 没有 running
            assert await repo.current(UpdateTarget.RESOURCE) is None

            # 同 target 存在一条更新的成功记录时，current 仍只返回 running 的那条
            await repo.create(_update(status=UpdateStatus.SUCCESS))
            await session.commit()
            current = await repo.current(UpdateTarget.CORE)
            assert current is not None and current.id == first.id
            assert current.status == UpdateStatus.RUNNING

            # 收口终态后 current 变 None，且能再开一条新的 running
            assert await repo.mark_terminal(first.id, UpdateStatus.SUCCESS) is True
            await session.commit()
            assert await repo.current(UpdateTarget.CORE) is None

            second = await repo.create(_update(from_version="v5.0", to_version="v6.0"))
            await session.commit()
            assert (await repo.current(UpdateTarget.CORE)).id == second.id

    asyncio.run(scenario())


def test_update_list_filters_and_paginates(db_session_factory):
    """更新历史按 ``created_at DESC`` 分页，``target`` 过滤后 ``total`` 是过滤后的总数。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            await repo.create(
                _update(status=UpdateStatus.SUCCESS, created_at=datetime(2026, 1, 1))
            )
            await repo.create(
                _update(
                    target=UpdateTarget.RESOURCE,
                    channel=ResourceChannel.REPO,
                    status=UpdateStatus.SUCCESS,
                    created_at=datetime(2026, 1, 2),
                )
            )
            await repo.create(
                _update(status=UpdateStatus.CANCELLED, created_at=datetime(2026, 1, 3))
            )
            await repo.create(
                _update(
                    target=UpdateTarget.GAME,
                    status=UpdateStatus.FAILED,
                    created_at=datetime(2026, 1, 4),
                )
            )
            await repo.create(
                _update(from_version="v5.0", created_at=datetime(2026, 1, 5))
            )
            await session.commit()

            page = await repo.list(page=1, size=2)
            assert page.total == 5 and page.page == 1 and page.size == 2
            assert [row.created_at for row in page.items] == [
                datetime(2026, 1, 5),
                datetime(2026, 1, 4),
            ]
            assert page.items[0].status == UpdateStatus.RUNNING

            second = await repo.list(page=2, size=2)
            assert [row.created_at for row in second.items] == [
                datetime(2026, 1, 3),
                datetime(2026, 1, 2),
            ]
            assert second.total == 5

            core = await repo.list(target=UpdateTarget.CORE, page=1, size=20)
            assert core.total == 3
            assert [row.created_at for row in core.items] == [
                datetime(2026, 1, 5),
                datetime(2026, 1, 3),
                datetime(2026, 1, 1),
            ]
            assert all(row.target == UpdateTarget.CORE for row in core.items)

            # Target + status compose identically in the items query and its
            # count query; rows of other statuses do not inflate total.
            core_success = await repo.list(
                target=UpdateTarget.CORE,
                status=UpdateStatus.SUCCESS,
                page=1,
                size=20,
            )
            assert core_success.total == 1
            assert len(core_success.items) == 1
            assert core_success.items[0].status == UpdateStatus.SUCCESS

            no_second_page = await repo.list(
                target=UpdateTarget.CORE,
                status=UpdateStatus.SUCCESS,
                page=2,
                size=1,
            )
            assert no_second_page.total == 1 and no_second_page.items == []

            failed = await repo.list(status="failed", page=1, size=20)
            assert failed.total == 1 and failed.items[0].status == UpdateStatus.FAILED

            # 越界分页参数被夹到合法区间（负 offset 在 SQLite 上不报错但结果错）
            clamped = await repo.list(page=0, size=0)
            assert clamped.page == 1 and clamped.size == 1
            assert len(clamped.items) == 1

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# update_record：进度写入与终态流转
# ---------------------------------------------------------------------------


def test_update_progress_writes_only_provided_columns(db_session_factory):
    """``update_progress`` 只写给到的列；``None`` 不是「置 NULL」而是「不改」。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            record = await repo.create(
                _update(
                    phase=UpdatePhase.CHECKING, progress=0, bytes_total=1024
                )
            )
            await session.commit()

            assert (
                await repo.update_progress(record.id, phase=UpdatePhase.DOWNLOADING)
                is True
            )
            await session.commit()
            got = await repo.get(record.id)
            assert got.phase == UpdatePhase.DOWNLOADING
            # 未提供的列保持原值：progress=0（显式写过的 0）没被 None 抹掉
            assert got.progress == 0
            assert got.bytes_total == 1024 and got.bytes_done is None

            assert (
                await repo.update_progress(record.id, progress=37, bytes_done=379)
                is True
            )
            await session.commit()
            got = await repo.get(record.id)
            assert got.progress == 37 and got.bytes_done == 379
            assert got.phase == UpdatePhase.DOWNLOADING and got.bytes_total == 1024

            # 一个可写列都没给：不发语句、返回 False
            assert await repo.update_progress(record.id) is False
            # 记录不存在
            assert await repo.update_progress("no-such-id", progress=1) is False
            # 枚举值写错立刻 ValueError（不是静默落库）
            with pytest.raises(ValueError):
                await repo.update_progress(record.id, phase="downloading!")

    asyncio.run(scenario())


def test_update_progress_rejected_after_terminal(db_session_factory):
    """迟到的进度回调不得写回终态记录（终态不可再变）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            record = await repo.create(
                _update(phase=UpdatePhase.DOWNLOADING, progress=42)
            )
            await session.commit()

            assert (
                await repo.mark_terminal(
                    record.id,
                    UpdateStatus.FAILED,
                    error_code="UPDATE_DOWNLOAD_FAILED",
                    error_message="连接超时",
                )
                is True
            )
            await session.commit()

            assert (
                await repo.update_progress(
                    record.id, phase=UpdatePhase.VERIFYING, progress=99
                )
                is False
            )
            await session.commit()

            got = await repo.get(record.id)
            assert got.status == UpdateStatus.FAILED
            assert got.phase == UpdatePhase.DOWNLOADING and got.progress == 42

    asyncio.run(scenario())


def test_update_mark_terminal_state_machine(db_session_factory):
    """``mark_terminal`` 只接受终态、写入错误信息与 ``finished_at``、不可二次流转。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            record = await repo.create(_update())
            await session.commit()

            # 非终态与不存在的记录都拒绝
            assert await repo.mark_terminal(record.id, UpdateStatus.RUNNING) is False
            assert await repo.mark_terminal(record.id, UpdateStatus.PENDING) is False
            assert await repo.mark_terminal("no-such-id", UpdateStatus.SUCCESS) is False

            assert (
                await repo.mark_terminal(
                    record.id,
                    UpdateStatus.FAILED,
                    error_code="UPDATE_VERIFY_FAILED",
                    error_message="checksum 不匹配",
                )
                is True
            )
            await session.commit()
            failed = await repo.get(record.id)
            assert failed.status == UpdateStatus.FAILED
            assert failed.error_code == "UPDATE_VERIFY_FAILED"
            assert failed.error_message == "checksum 不匹配"
            assert failed.finished_at is not None

            # 终态不可再变：第二次流转被 WHERE 子句拒绝
            assert await repo.mark_terminal(record.id, UpdateStatus.SUCCESS) is False
            await session.commit()
            assert (await repo.get(record.id)).status == UpdateStatus.FAILED

            # 其余终态与纯字符串入参
            resource = await repo.create(_update(target=UpdateTarget.RESOURCE))
            game = await repo.create(_update(target=UpdateTarget.GAME, channel=None))
            await session.commit()
            assert await repo.mark_terminal(resource.id, "skipped") is True
            assert await repo.mark_terminal(game.id, UpdateStatus.CANCELLED) is True
            await session.commit()
            assert (await repo.get(resource.id)).status == UpdateStatus.SKIPPED
            assert (await repo.get(game.id)).status == UpdateStatus.CANCELLED
            assert (await repo.get(game.id)).error_code is None

    asyncio.run(scenario())


def test_update_repository_does_not_commit(db_session_factory):
    """仓储不 ``commit()``：回滚后创建的记录与进度一起消失。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = UpdateRepository(session)
            record = await repo.create(_update())
            assert await repo.update_progress(record.id, progress=10) is True
            assert await _count(session, "update_record") == 1
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "update_record") == 0

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# notify_channel：配置存取、唯一约束、启停与发送结果
# ---------------------------------------------------------------------------


def test_notify_create_get_roundtrip_preserves_json(db_session_factory):
    """``config`` / ``events`` 按 JSON 原文存取，收件人等结构原样读回。"""

    async def scenario():
        config = {
            "server": "smtp.example.com",
            "port": 465,
            "use_tls": True,
            "username": "bot@example.com",
            "password": "secret",
            "to": ["me@example.com"],
        }
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            channel = _channel(
                config=config,
                events=[NotifyEvent.PIPELINE_COMPLETED, NotifyEvent.PIPELINE_FAILED],
            )
            stored = await repo.create(channel)
            await session.commit()
            assert stored.id == channel.id
            assert stored.enabled is True
            assert stored.created_at is not None and stored.updated_at is not None
            assert stored.last_sent_at is None
            assert stored.last_status is None and stored.last_error is None

            got = await repo.get(stored.id)
            assert got.config == config
            assert got.config["port"] == 465 and got.config["to"] == ["me@example.com"]
            assert got.events == [
                NotifyEvent.PIPELINE_COMPLETED,
                NotifyEvent.PIPELINE_FAILED,
            ]

            # 未配置的通道与不存在的 id
            assert await repo.get("no-such-id") is None

    asyncio.run(scenario())


def test_notify_config_structure_is_not_a_database_constraint(db_session_factory):
    """五种通道的 ``config`` 结构校验在服务层：数据库只存 JSON，不拦空配置。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            # 空 config、空 events、甚至没有对应枚举的 type 都能落库 ——
            # 这不是缺陷，是「校验按 type 分发的 Pydantic 模型在服务层」的边界。
            await repo.create(
                _channel(
                    type=NotifyChannelType.WECOM,
                    name="企业微信",
                    config={},
                    events=[],
                )
            )
            await session.commit()
            assert await _count(session, "notify_channel") == 1

    asyncio.run(scenario())


def test_notify_type_name_unique_in_database(db_session_factory):
    """``(type, name)`` 唯一：同名可在不同类型下各配一个，同类型重名被拒。"""

    async def scenario():
        async with db_session_factory() as session:
            await NotifyChannelRepository(session).create(_channel())
            await session.commit()

        async with db_session_factory() as session:
            await _expect_integrity_error(NotifyChannelRepository(session).create(_channel()))
            await session.rollback()

        # 同名不同 type 合法
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            await repo.create(
                _channel(type=NotifyChannelType.BARK, name="主邮箱", config={"device_key": "k"})
            )
            # 同 type 不同 name 合法
            await repo.create(_channel(name="备用邮箱"))
            await session.commit()
            assert await _count(session, "notify_channel") == 3

    asyncio.run(scenario())


def test_notify_list_filters_and_orders(db_session_factory):
    """``list`` 按创建时间升序，``type`` / ``enabled`` 过滤不改变顺序。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            email = await repo.create(
                _channel(created_at=datetime(2026, 1, 1))
            )
            bark = await repo.create(
                _channel(
                    type=NotifyChannelType.BARK,
                    name="主通道",
                    config={"device_key": "k"},
                    created_at=datetime(2026, 1, 2),
                )
            )
            dingtalk = await repo.create(
                _channel(
                    type=NotifyChannelType.DINGTALK,
                    name="主通道",
                    config={"webhook": "https://example.com"},
                    created_at=datetime(2026, 1, 3),
                )
            )
            await session.commit()

            assert [c.id for c in await repo.list()] == [email.id, bark.id, dingtalk.id]
            assert [c.id for c in await repo.list(type=NotifyChannelType.BARK)] == [bark.id]
            assert [c.id for c in await repo.list(type="dingtalk")] == [dingtalk.id]
            assert await repo.list(type=NotifyChannelType.WEBHOOK) == []

            assert await repo.set_enabled(dingtalk.id, False) is True
            await session.commit()
            assert [c.id for c in await repo.list(enabled=True)] == [email.id, bark.id]
            assert [c.id for c in await repo.list(enabled=False)] == [dingtalk.id]
            assert [
                c.id
                for c in await repo.list(type=NotifyChannelType.BARK, enabled=False)
            ] == []
            # 回读的 enabled 是库里的真值（set_enabled 之后立刻 list）
            assert (await repo.list(enabled=True))[1].enabled is True

    asyncio.run(scenario())


def test_notify_set_enabled_refreshes_updated_at(db_session_factory):
    """``set_enabled`` 用 ``bool`` 表示记录是否存在，并刷新 ``updated_at``。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            channel = await repo.create(_channel())
            await session.commit()

            await _backdate(session, "notify_channel", channel.id)
            await session.commit()

            assert await repo.set_enabled(channel.id, False) is True
            await session.commit()
            got = await repo.get(channel.id)
            assert got.enabled is False
            assert got.updated_at > PAST

            # 幂等：改成同一个值仍然算「记录存在」
            assert await repo.set_enabled(channel.id, False) is True
            assert await repo.set_enabled(channel.id, True) is True
            assert await repo.set_enabled("no-such-id", False) is False
            await session.commit()
            assert (await repo.get(channel.id)).enabled is True

    asyncio.run(scenario())


def test_notify_record_send_writes_last_result(db_session_factory):
    """``record_send`` 回写 ``last_sent_at`` / ``last_status`` / ``last_error``。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            channel = await repo.create(_channel())
            await session.commit()

            assert (
                await repo.record_send(channel.id, status="failed", error="SMTP timeout")
                is True
            )
            await session.commit()
            got = await repo.get(channel.id)
            assert got.last_status == "failed" and got.last_error == "SMTP timeout"
            assert got.last_sent_at is not None
            # 一次失败不停用通道（enabled 只由 set_enabled 改）
            assert got.enabled is True

            # 成功时清空上一次的失败原因：设置页展示的是「最近一次」的结果
            assert await repo.record_send(channel.id, status="success") is True
            await session.commit()
            got = await repo.get(channel.id)
            assert got.last_status == "success" and got.last_error is None
            assert got.last_sent_at is not None

            assert await repo.record_send("no-such-id", status="success") is False

    asyncio.run(scenario())


def test_notify_delete_returns_bool(db_session_factory):
    """``delete`` 返回是否真的删掉了；删一个不影响其他通道。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            first = await repo.create(_channel())
            second = await repo.create(_channel(name="备用邮箱"))
            await session.commit()

            assert await repo.delete(first.id) is True
            await session.commit()
            assert await repo.get(first.id) is None
            assert [c.id for c in await repo.list()] == [second.id]

            assert await repo.delete(first.id) is False

    asyncio.run(scenario())


def test_notify_repository_does_not_commit(db_session_factory):
    """仓储不 ``commit()``：回滚后通道与其发送结果一起消失。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = NotifyChannelRepository(session)
            channel = await repo.create(_channel())
            assert await repo.record_send(channel.id, status="success") is True
            assert await _count(session, "notify_channel") == 1
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "notify_channel") == 0

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# resource_asset：CHECK、唯一约束、CRUD
# ---------------------------------------------------------------------------


def test_resource_create_content_and_path_rows(db_session_factory):
    """``content`` 行与 ``path`` 行都能建；另一列留 NULL。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            content_row = _asset(
                name="剿灭作业", content={"task": "Custom_X"}, meta={"stage": "1-7"}
            )
            stored = await repo.create(content_row)
            path_row = await repo.create(
                _asset(
                    kind=ResourceAssetKind.COPILOT,
                    name="大作业",
                    content=None,
                    path="copilot/big.json",
                    checksum="a" * 64,
                )
            )
            await session.commit()

            assert stored.id == content_row.id
            assert stored.enabled is True
            assert stored.created_at is not None and stored.updated_at is not None
            assert stored.path is None and stored.checksum is None

            got = await repo.get(stored.id)
            assert got.content == {"task": "Custom_X"}
            assert got.meta == {"stage": "1-7"}

            # (kind, name) 是业务键：走唯一索引读回
            by_name = await repo.get_by_kind_name(ResourceAssetKind.CUSTOM_TASK, "剿灭作业")
            assert by_name.id == stored.id
            assert await repo.get_by_kind_name(ResourceAssetKind.CUSTOM_TASK, "不存在") is None
            assert await repo.get("no-such-id") is None

            big = await repo.get_by_kind_name(ResourceAssetKind.COPILOT, "大作业")
            assert big.id == path_row.id
            assert big.content is None and big.path == "copilot/big.json"
            assert big.checksum == "a" * 64

    asyncio.run(scenario())


def test_resource_content_or_path_check_in_database(db_session_factory):
    """CHECK 约束在 insert 层强制「至少有一个」；两个都给不违反 OR 语义。"""

    async def scenario():
        async with db_session_factory() as session:
            await _expect_integrity_error(
                ResourceAssetRepository(session).create(_asset(name="坏行", content=None, path=None))
            )
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "resource_asset") == 0
            # CHECK 是 OR：两列都给也能落库，是否拒绝由服务层决定
            row = await ResourceAssetRepository(session).create(
                _asset(name="两列都给", content={"a": 1}, path="custom/both.json")
            )
            await session.commit()
            assert row.id

    asyncio.run(scenario())


def test_resource_kind_name_unique_in_database(db_session_factory):
    """``(kind, name)`` 唯一：同类重名被拒，跨类同名合法。"""

    async def scenario():
        async with db_session_factory() as session:
            await ResourceAssetRepository(session).create(_asset())
            await session.commit()

        async with db_session_factory() as session:
            await _expect_integrity_error(
                ResourceAssetRepository(session).create(_asset())
            )
            await session.rollback()

        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            await repo.create(
                _asset(kind=ResourceAssetKind.COPILOT, name="我的作业", content={"copilot": 1})
            )
            await session.commit()
            assert await _count(session, "resource_asset") == 2

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# resource_asset：upsert_by_kind_name
# ---------------------------------------------------------------------------


def test_resource_upsert_creates_remote_baseline_row(db_session_factory):
    """通道 B 的版本基准行由 upsert 创建，``remote_version`` 是唯一权威来源。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            stored = await repo.upsert_by_kind_name(
                ResourceAssetKind.REPO_RESOURCE,
                "MaaResource",
                path="maa-layers/repo",
                remote_version="2026-09-14 04:36:14.000",
                last_checked_at=utcnow(),
            )
            await session.commit()

            # Core INSERT 没有走 SQLModel 的 default_factory：created_at /
            # updated_at / id / enabled 必须是仓储显式补上的
            assert stored.id
            assert stored.created_at is not None and stored.updated_at is not None
            assert stored.enabled is True
            assert stored.content is None
            assert stored.path == "maa-layers/repo"

            got = await repo.get_by_kind_name(
                ResourceAssetKind.REPO_RESOURCE, "MaaResource"
            )
            assert got.remote_version == "2026-09-14 04:36:14.000"
            assert got.last_checked_at is not None

    asyncio.run(scenario())


def test_resource_upsert_updates_single_row_and_keeps_identity(db_session_factory):
    """重复 upsert 只有一行：``id`` / ``created_at`` 不变，只改给到的列。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            first = await repo.upsert_by_kind_name(
                ResourceAssetKind.OTA_RESOURCE,
                "resource/tasks.json",
                path="maa-layers/cache/tasks.json",
                etag='"v1"',
                checksum="b" * 64,
            )
            await session.commit()
            first_id, first_created = first.id, first.created_at

        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            updated = await repo.upsert_by_kind_name(
                ResourceAssetKind.OTA_RESOURCE,
                "resource/tasks.json",
                remote_version="1740000000",
                etag='"v2"',
                checksum="c" * 64,
            )
            await session.commit()
            assert updated.id == first_id
            assert updated.created_at == first_created
            assert updated.etag == '"v2"' and updated.checksum == "c" * 64
            # 没给 content：原有的 NULL 保持，path 也原样保留
            assert updated.content is None
            assert updated.path == "maa-layers/cache/tasks.json"
            assert await _count(session, "resource_asset") == 1

    asyncio.run(scenario())


def test_resource_upsert_last_checked_only_does_not_touch_updated_at(db_session_factory):
    """只带 ``last_checked_at`` 的 upsert 是「问了一次远端」，不刷新 ``updated_at``。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            row = await repo.upsert_by_kind_name(
                ResourceAssetKind.OTA_RESOURCE,
                "resource/tasks.json",
                path="maa-layers/cache/tasks.json",
                checksum="d" * 64,
            )
            await session.commit()
            await _backdate(session, "resource_asset", row.id)
            await session.commit()

        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            checked_at = utcnow()
            await repo.upsert_by_kind_name(
                ResourceAssetKind.OTA_RESOURCE,
                "resource/tasks.json",
                last_checked_at=checked_at,
            )
            await session.commit()

        async with db_session_factory() as session:
            got = await ResourceAssetRepository(session).get_by_kind_name(
                ResourceAssetKind.OTA_RESOURCE, "resource/tasks.json"
            )
            assert got.last_checked_at == checked_at
            # 内容没变：updated_at 仍是 2000 年（docs/04 §5.13 的两列区分）
            assert got.updated_at == PAST

    asyncio.run(scenario())


def test_resource_upsert_content_change_refreshes_updated_at(db_session_factory):
    """内容字段出现时刷新 ``updated_at``；调用方显式传入的值优先。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            row = await repo.upsert_by_kind_name(
                ResourceAssetKind.CUSTOM_TASK,
                "我的作业",
                content={"task": "Custom_X"},
            )
            await session.commit()
            await _backdate(session, "resource_asset", row.id)
            await session.commit()

        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            await repo.upsert_by_kind_name(
                ResourceAssetKind.CUSTOM_TASK,
                "我的作业",
                content={"task": "Custom_Y"},
                checksum="e" * 64,
            )
            await session.commit()

        async with db_session_factory() as session:
            got = await ResourceAssetRepository(session).get_by_kind_name(
                ResourceAssetKind.CUSTOM_TASK, "我的作业"
            )
            assert got.content == {"task": "Custom_Y"} and got.checksum == "e" * 64
            assert got.updated_at > PAST

        # 显式 updated_at 覆盖「自动刷新」
        explicit = datetime(2020, 5, 5, 12, 0, 0)
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            stored = await repo.upsert_by_kind_name(
                ResourceAssetKind.CUSTOM_TASK,
                "我的作业",
                description="备注",
                updated_at=explicit,
            )
            await session.commit()
            assert stored.updated_at == explicit

    asyncio.run(scenario())


def test_resource_upsert_rejects_unknown_and_business_key_fields(db_session_factory):
    """未知列名与 ``id`` / ``created_at`` 一律 ``ValueError``（业务键是位置参数）。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            with pytest.raises(ValueError):
                await repo.upsert_by_kind_name(
                    ResourceAssetKind.CUSTOM_TASK, "我的作业", bogus=1
                )
            with pytest.raises(ValueError):
                await repo.upsert_by_kind_name(
                    ResourceAssetKind.CUSTOM_TASK, "我的作业", id="fixed-id"
                )
            with pytest.raises(ValueError):
                await repo.upsert_by_kind_name(
                    ResourceAssetKind.CUSTOM_TASK, "我的作业", created_at=utcnow()
                )
            await session.rollback()

    asyncio.run(scenario())


def test_resource_upsert_enforces_check_on_insert_and_update(db_session_factory):
    """CHECK 在 upsert 的 insert 与 update 两条路径上都生效。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            await repo.upsert_by_kind_name(
                ResourceAssetKind.CUSTOM_TASK, "我的作业", content={"task": "Custom_X"}
            )
            await session.commit()

        # insert 路径：既没 content 也没 path
        async with db_session_factory() as session:
            await _expect_integrity_error(
                ResourceAssetRepository(session).upsert_by_kind_name(
                    ResourceAssetKind.CUSTOM_TASK, "新作业", description="只有备注"
                )
            )
            await session.rollback()

        # update 路径：把已有行的 content 置空且没有 path 可兜底
        async with db_session_factory() as session:
            await _expect_integrity_error(
                ResourceAssetRepository(session).upsert_by_kind_name(
                    ResourceAssetKind.CUSTOM_TASK, "我的作业", content=None
                )
            )
            await session.rollback()

        async with db_session_factory() as session:
            got = await ResourceAssetRepository(session).get_by_kind_name(
                ResourceAssetKind.CUSTOM_TASK, "我的作业"
            )
            assert got.content == {"task": "Custom_X"}
            assert await _count(session, "resource_asset") == 1

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# resource_asset：列表、启停、删除
# ---------------------------------------------------------------------------


def test_resource_list_by_kind_filters_enabled_and_orders_by_name(db_session_factory):
    """``list_by_kind`` 按 ``name`` 升序，``enabled`` 过滤命中 ``ix_..._kind_enabled``。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            a = await repo.create(_asset(name="A作业"))
            b = await repo.create(_asset(name="B作业"))
            c = await repo.create(_asset(name="C作业"))
            await repo.create(_asset(kind=ResourceAssetKind.INFRAST_PLAN, name="基建方案", content={}))
            await session.commit()

            assert await repo.set_enabled(b.id, False) is True
            await session.commit()

            assert [x.name for x in await repo.list_by_kind(ResourceAssetKind.CUSTOM_TASK)] == [
                "A作业",
                "B作业",
                "C作业",
            ]
            assert [
                x.id for x in await repo.list_by_kind(ResourceAssetKind.CUSTOM_TASK, enabled=True)
            ] == [a.id, c.id]
            assert [
                x.id for x in await repo.list_by_kind(ResourceAssetKind.CUSTOM_TASK, enabled=False)
            ] == [b.id]
            assert [
                x.name
                for x in await repo.list_by_kind(ResourceAssetKind.INFRAST_PLAN, enabled=True)
            ] == ["基建方案"]
            assert await repo.list_by_kind(ResourceAssetKind.OTA_RESOURCE) == []

    asyncio.run(scenario())


def test_resource_set_enabled_and_delete(db_session_factory):
    """``set_enabled`` / ``delete`` 用 ``bool`` 表示记录是否存在；重复删除幂等。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            kept = await repo.create(_asset(name="保留"))
            removed = await repo.create(_asset(name="删除"))
            await session.commit()

            await _backdate(session, "resource_asset", removed.id)
            await session.commit()

            assert await repo.set_enabled(removed.id, False) is True
            await session.commit()
            got = await repo.get(removed.id)
            assert got.enabled is False and got.updated_at > PAST
            # 幂等：同一个值再设一次也算成功
            assert await repo.set_enabled(removed.id, False) is True
            assert await repo.set_enabled("no-such-id", True) is False

            assert await repo.delete(removed.id) is True
            await session.commit()
            assert await repo.get(removed.id) is None
            assert [x.id for x in await repo.list_by_kind(ResourceAssetKind.CUSTOM_TASK)] == [
                kept.id
            ]
            assert await repo.delete(removed.id) is False

    asyncio.run(scenario())


def test_resource_repository_does_not_commit(db_session_factory):
    """仓储不 ``commit()``：回滚后 upsert 的行一起消失。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ResourceAssetRepository(session)
            await repo.upsert_by_kind_name(
                ResourceAssetKind.REPO_RESOURCE,
                "MaaResource",
                path="maa-layers/repo",
                remote_version="v1",
            )
            assert await _count(session, "resource_asset") == 1
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "resource_asset") == 0

    asyncio.run(scenario())
