"""``ScheduleRepository`` / ``SettingRepository`` 的仓储行为测试（M2-09）。

覆盖 docs/04 §5.5 / §5.6 / §9 的硬约束：

- ``setting`` 只存显式覆盖项：表里没有 key = 用下层的值，``delete`` 后 ``get``
  回到 ``None``；仓储不灌默认值、不脱敏；
- ``setting.value`` 是 JSON 列：``25`` 读回 ``int``、``"25"`` 读回 ``str``
  （存储层可区分），upsert 走 ON CONFLICT 不产生第二行，并刷新
  ``updated_by`` / ``updated_at``；
- ``schedule.template`` 原样存请求体的 ``tasks`` 数组，不做任何预处理；
- ``has_unfinished`` = 「本 schedule 有没有 pending/running 实例」，随流水线
  终态翻转，且不把「系统里有别的流水线在跑」当成本 schedule 在跑；
- ``record_run`` 回写 ``last_run_at`` / ``next_run_at`` / ``last_pipeline_id`` /
  ``last_result`` 四列；``set_enabled`` / ``delete`` 用 ``bool`` 表示记录是否存在；
- ``delete`` 清空 ``pipeline.schedule_id``，不依赖调用方连接是否开了
  ``PRAGMA foreign_keys``；
- 仓储不 ``commit()``，回滚后写入一起消失。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库与会话来自 ``tests/db/conftest.py``。
"""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
from maa_api.db.models import Pipeline, Schedule, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.repositories.schedule import ScheduleRepository
from maa_api.db.repositories.setting import SettingRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority

TEMPLATE = [
    {"name": "Award"},
    {"name": "Fight", "params": {"stage": "1-7", "times": 3}},
]


def _schedule(**overrides) -> Schedule:
    fields = {"name": "每日常规", "cron": "0 7,19 * * *", "template": TEMPLATE}
    fields.update(overrides)
    return Schedule(**fields)


def _pipeline(**overrides) -> Pipeline:
    fields = {
        "source": PipelineSource.SCHEDULED,
        "priority": Priority.SCHEDULED,
        "task_count": 0,
    }
    fields.update(overrides)
    return Pipeline(**fields)


async def _count(session, table: str, where: str = "", params: dict | None = None) -> int:
    sql = f"select count(*) from {table}" + (f" where {where}" if where else "")
    return (await session.execute(text(sql), params or {})).scalar_one()


async def _raw(session, sql: str, params: dict | None = None):
    return (await session.execute(text(sql), params or {})).all()


# ---------------------------------------------------------------------------
# setting：JSON 值保型与覆盖层语义
# ---------------------------------------------------------------------------


def test_set_get_preserves_json_types(db_session_factory):
    """JSON 列保住原始类型：``25`` 是 int、``"25"`` 是 str，存储形态可区分。

    ``adb.screenshot_quality`` 必须是整数才能直接传给 PIL（docs/04 §5.6）；
    统一存字符串就得在读取侧维护类型表，这条用例就是防止那种退化的哨兵。
    """

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            await repo.set("adb.screenshot_quality", 25, updated_by="manual")
            await repo.set("log.level", "25", updated_by="agent")
            await repo.set("app.debug", True)
            await repo.set("adb.scale", 1.5)
            await repo.set("notify.events", ["pipeline_finished"])
            await repo.set("task.defaults", {"Award": {"times": 1}})
            await repo.set("app.max_retries", 0)
            await session.commit()

            quality = await repo.get("adb.screenshot_quality")
            level = await repo.get("log.level")
            assert quality == 25 and type(quality) is int
            assert level == "25" and type(level) is str
            assert type(await repo.get("app.debug")) is bool
            assert type(await repo.get("adb.scale")) is float
            # 假值也是合法覆盖：0 不等于「表里没有这个 key」
            assert await repo.get("app.max_retries") == 0
            assert await repo.get("app.max_retries") is not None
            assert await repo.get("notify.events") == ["pipeline_finished"]
            assert await repo.get("task.defaults") == {"Award": {"times": 1}}
            assert await repo.get("missing.key") is None

            # 存储层：整数存成 JSON 数字，字符串存成带引号的 JSON 文本 ——
            # 两者在库里就是不同的字节，不是靠读取侧的约定区分。
            raw = dict(await _raw(session, "select key, value from setting"))
            assert str(raw["adb.screenshot_quality"]) == "25"
            assert str(raw["log.level"]) == '"25"'

    asyncio.run(scenario())


def test_set_upsert_keeps_single_row_and_refreshes_metadata(db_session_factory):
    """同一 key 连续 ``set`` 只有一行；``updated_by`` / ``updated_at`` 被刷新。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            await repo.set("log.level", "debug", updated_by="manual")
            await session.commit()
            # 把 updated_at 退到过去，让「刷新」可判别，不依赖相邻两次 now() 的差值
            await session.execute(
                text(
                    "update setting set updated_at = '2000-01-01 00:00:00' "
                    "where key = 'log.level'"
                )
            )
            await session.commit()

            await repo.set("log.level", "info", updated_by="agent")
            await session.commit()

            assert await _count(session, "setting") == 1
            allv = await repo.all()
            assert allv == {"log.level": "info"}
            row = (await _raw(
                session,
                "select updated_by, updated_at from setting where key = 'log.level'",
            ))[0]
            assert row.updated_by == "agent"
            # 裸 text() 查询不套 SQLAlchemy 的类型处理器，DATETIME 列拿到的是 ISO 串
            assert datetime.fromisoformat(row.updated_at).year > 2000

            # 不传 updated_by 时写入 NULL，而不是保留上一次的来源
            await repo.set("log.level", "warning")
            await session.commit()
            assert (
                await _raw(
                    session,
                    "select updated_by from setting where key = 'log.level'",
                )
            )[0][0] is None

    asyncio.run(scenario())


def test_set_rejects_none_value(db_session_factory):
    """``None`` 不是合法覆盖值：列 NOT NULL，且 JSON null 与「未覆盖」无法区分。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            with pytest.raises(ValueError):
                await repo.set("adb.address", None)
            await session.commit()
            assert await _count(session, "setting") == 0

    asyncio.run(scenario())


def test_all_returns_every_override_and_delete_falls_back(db_session_factory):
    """``all`` 返回全表覆盖项；``delete`` 后该 key 回到「用下层的值」。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = SettingRepository(session)
            await repo.set("adb.address", "127.0.0.1:5555", updated_by="manual")
            await repo.set("log.level", "info", updated_by="system")
            await session.commit()

            allv = await repo.all()
            assert allv == {"adb.address": "127.0.0.1:5555", "log.level": "info"}
            assert list(allv) == ["adb.address", "log.level"]  # 按 key 升序

            assert await repo.delete("log.level") is True
            assert await repo.delete("log.level") is False  # 重复删除幂等
            await session.commit()
            assert await repo.get("log.level") is None
            assert await repo.all() == {"adb.address": "127.0.0.1:5555"}

        # 跨会话确认落库：覆盖项只有显式写过的那些，没有被灌入默认值
        async with db_session_factory() as session:
            assert await SettingRepository(session).all() == {
                "adb.address": "127.0.0.1:5555"
            }

    asyncio.run(scenario())


def test_setting_repository_does_not_commit(db_session_factory):
    """仓储不 ``commit()``：回滚后覆盖项消失（事务边界归调用方）。"""

    async def scenario():
        async with db_session_factory() as session:
            await SettingRepository(session).set("adb.address", "127.0.0.1:5555")
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "setting") == 0

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# schedule：创建、装载、启停
# ---------------------------------------------------------------------------


def test_create_get_and_model_defaults(db_session_factory):
    """写入即可读；默认值、``template`` 原样存取、入参提交后仍可读。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ScheduleRepository(session)
            schedule = _schedule()
            next_run = utcnow() + timedelta(hours=1)
            schedule.next_run_at = next_run
            stored = await repo.create(schedule)
            await session.commit()

            assert stored is not schedule  # merge：副本才是会话管理的对象
            schedule_id = schedule.id
            assert schedule_id and len(schedule_id) == 36

            got = await repo.get(schedule_id)
            assert got is not None
            assert (got.name, got.cron) == ("每日常规", "0 7,19 * * *")
            assert got.timezone == "Asia/Shanghai"
            assert got.enabled is True
            assert got.priority == Priority.SCHEDULED
            assert got.misfire_grace_seconds == 300
            assert got.catch_up is False
            assert got.skip_if_running is True
            assert got.last_run_at is None and got.last_result is None
            assert got.last_pipeline_id is None
            assert got.next_run_at == next_run
            assert got.created_at is not None and got.updated_at is not None
            assert await repo.get("no-such-id") is None

            # template 原样存 tasks 数组，没有预处理、没有展开成 task 行
            assert got.template == TEMPLATE
            raw_template = (
                await _raw(
                    session, "select template from schedule where id = :i", {"i": schedule_id}
                )
            )[0][0]
            assert json.loads(raw_template) == TEMPLATE
            assert await _count(session, "task") == 0

            # M2-07 纪律：入参保持 transient，expire_all() 后字段仍可直接读
            session.expire_all()
            assert schedule.id == schedule_id and schedule.name == "每日常规"

    asyncio.run(scenario())


def test_create_duplicate_name_raises_integrity_error(db_session_factory):
    """``schedule.name`` UNIQUE 由数据库兜底（``uq_schedule_name``），仓储不先查后写。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ScheduleRepository(session)
            await repo.create(_schedule(name="每日常规"))
            await session.commit()
            with pytest.raises(IntegrityError):
                await repo.create(_schedule(name="每日常规", cron="0 8 * * *"))
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "schedule") == 1

    asyncio.run(scenario())


def test_list_enabled_filters_and_orders_by_next_run_at(db_session_factory):
    """``list_enabled`` 只返回启用的记录，按 ``next_run_at`` 升序；NULL 也返回。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ScheduleRepository(session)
            now = utcnow()
            late = _schedule(name="晚场", next_run_at=now + timedelta(hours=12))
            early = _schedule(name="早场", next_run_at=now + timedelta(hours=1))
            never = _schedule(name="未排期", next_run_at=None)
            off = _schedule(name="已停用", next_run_at=now, enabled=False)
            for item in (late, early, never, off):
                await repo.create(item)
            await session.commit()

            enabled_ids = [x.id for x in await repo.list_enabled()]
            assert enabled_ids == [never.id, early.id, late.id]
            assert off.id not in enabled_ids

            assert await repo.set_enabled(off.id, True) is True
            await session.commit()
            assert off.id in [x.id for x in await repo.list_enabled()]

    asyncio.run(scenario())


def test_set_enabled_toggles_and_reports_existence(db_session_factory):
    """``set_enabled`` 用 ``bool`` 表示记录是否存在，并同步刷新 ``updated_at``。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ScheduleRepository(session)
            schedule = _schedule()
            await repo.create(schedule)
            await session.commit()
            assert len(await repo.list_enabled()) == 1

            assert await repo.set_enabled(schedule.id, False) is True
            await session.commit()
            assert await repo.list_enabled() == []
            assert (await repo.get(schedule.id)).enabled is False
            # 幂等：改成同一个值仍然算「记录存在」
            assert await repo.set_enabled(schedule.id, False) is True
            assert await repo.set_enabled("no-such-id", False) is False

            assert await repo.set_enabled(schedule.id, True) is True
            await session.commit()
            assert [x.id for x in await repo.list_enabled()] == [schedule.id]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# schedule：skip_if_running 判定与运行结果回写
# ---------------------------------------------------------------------------


def test_has_unfinished_tracks_pipeline_terminal_state(db_session_factory):
    """``has_unfinished`` 随本 schedule 的实例在 pending/running ↔ 终态之间翻转。"""

    async def scenario():
        async with db_session_factory() as session:
            srepo, prepo = ScheduleRepository(session), PipelineRepository(session)
            schedule = _schedule()
            await srepo.create(schedule)
            await session.commit()
            assert await srepo.has_unfinished(schedule.id) is False

            pipeline = _pipeline(schedule_id=schedule.id)
            await prepo.create(pipeline, [])
            await session.commit()
            assert await srepo.has_unfinished(schedule.id) is True  # pending

            await prepo.claim_next()
            await session.commit()
            assert await srepo.has_unfinished(schedule.id) is True  # running

            await prepo.mark_terminal(pipeline.id, PipelineStatus.COMPLETED)
            await session.commit()
            assert await srepo.has_unfinished(schedule.id) is False

            cancelled = _pipeline(schedule_id=schedule.id)
            await prepo.create(cancelled, [])
            await session.commit()
            assert await srepo.has_unfinished(schedule.id) is True
            await prepo.mark_terminal(cancelled.id, PipelineStatus.CANCELLED)
            await session.commit()
            assert await srepo.has_unfinished(schedule.id) is False

    asyncio.run(scenario())


def test_has_unfinished_is_per_schedule_not_system_busy(db_session_factory):
    """判定口径是「本 schedule 有没有未完成实例」，不是「整个系统是否忙」。

    手动提交的流水线、别的 schedule 的实例都不得让本 schedule 的
    ``has_unfinished`` 变真（docs/04 §5.5 对 ``skip_if_running`` 的语义修正）。
    """

    async def scenario():
        async with db_session_factory() as session:
            srepo, prepo = ScheduleRepository(session), PipelineRepository(session)
            mine = _schedule(name="我的")
            other = _schedule(name="别人的")
            await srepo.create(mine)
            await srepo.create(other)
            await session.commit()

            manual = _pipeline(source=PipelineSource.MANUAL, priority=Priority.MANUAL)
            others = _pipeline(schedule_id=other.id)
            await prepo.create(manual, [])
            await prepo.create(others, [])
            await session.commit()

            assert await srepo.has_unfinished(mine.id) is False
            assert await srepo.has_unfinished(other.id) is True
            assert await srepo.has_unfinished("no-such-id") is False

    asyncio.run(scenario())


def test_record_run_writes_four_columns(db_session_factory):
    """``record_run`` 回写 ``last_run_at`` / ``next_run_at`` / ``last_pipeline_id`` /
    ``last_result``，并区分「记录不存在」。"""

    async def scenario():
        async with db_session_factory() as session:
            srepo, prepo = ScheduleRepository(session), PipelineRepository(session)
            schedule = _schedule()
            await srepo.create(schedule)
            pipeline = _pipeline(schedule_id=schedule.id)
            await prepo.create(pipeline, [])
            await session.commit()

            now = utcnow()
            next_run = now + timedelta(hours=12)
            assert (
                await srepo.record_run(
                    schedule.id,
                    last_run_at=now,
                    next_run_at=next_run,
                    pipeline_id=pipeline.id,
                    result=PipelineStatus.COMPLETED,
                )
                is True
            )
            await session.commit()

            got = await srepo.get(schedule.id)
            assert got.last_run_at == now
            assert got.next_run_at == next_run
            assert got.last_pipeline_id == pipeline.id
            assert got.last_result == PipelineStatus.COMPLETED == "completed"

            # 本次只做了跳过判定、没有对应流水线时，四列可以整体清空
            assert (
                await srepo.record_run(
                    schedule.id,
                    last_run_at=now,
                    next_run_at=next_run,
                    pipeline_id=None,
                    result=None,
                )
                is True
            )
            await session.commit()
            cleared = await srepo.get(schedule.id)
            assert cleared.last_pipeline_id is None and cleared.last_result is None

            # 普通字符串也能落库（服务层可能从 Pipeline 行上取到纯字符串）
            assert (
                await srepo.record_run(
                    schedule.id,
                    last_run_at=now,
                    next_run_at=next_run,
                    pipeline_id=pipeline.id,
                    result="failed",
                )
                is True
            )
            await session.commit()
            assert (await srepo.get(schedule.id)).last_result == PipelineStatus.FAILED

            assert (
                await srepo.record_run(
                    "no-such-id", last_run_at=now, next_run_at=next_run
                )
                is False
            )

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# schedule：删除与事务纪律
# ---------------------------------------------------------------------------


def test_delete_returns_bool_and_removes_record(db_session_factory):
    """``delete`` 返回记录是否存在；删掉的 schedule 不再出现在装载集合里。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ScheduleRepository(session)
            schedule = _schedule()
            await repo.create(schedule)
            await session.commit()

            assert await repo.delete(schedule.id) is True
            await session.commit()
            assert await repo.get(schedule.id) is None
            assert await repo.list_enabled() == []
            assert await repo.delete(schedule.id) is False

    asyncio.run(scenario())


def test_delete_nulls_pipeline_reference_without_foreign_keys_pragma(tmp_path):
    """``delete`` 不依赖调用方连接是否开了 ``PRAGMA foreign_keys``。

    裸 ``create_async_engine``（verify 脚本的形态）默认 ``foreign_keys=0``，
    ``ON DELETE SET NULL`` 静默失效；仓储显式置空 ``pipeline.schedule_id``，
    历史实例不会留下悬空引用。生产 ``db/session.py`` 开了 PRAGMA，同一段代码
    在那里只是多一次空操作。
    """

    async def scenario():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'no-fk.db'}", poolclass=NullPool
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
                assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 0
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                srepo, prepo = ScheduleRepository(session), PipelineRepository(session)
                schedule = _schedule()
                await srepo.create(schedule)
                pipeline = _pipeline(schedule_id=schedule.id)
                await prepo.create(pipeline, [])
                await session.commit()

                assert await srepo.delete(schedule.id) is True
                await session.commit()

            async with factory() as session:
                remaining = (
                    await session.execute(
                        text("select schedule_id from pipeline where id = :i"),
                        {"i": pipeline.id},
                    )
                ).scalar_one()
                assert remaining is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_schedule_repository_does_not_commit(db_session_factory):
    """仓储不 ``commit()``：回滚后 schedule 与其变更一起消失。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ScheduleRepository(session)
            schedule = _schedule()
            await repo.create(schedule)
            assert await _count(session, "schedule") == 1
            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "schedule") == 0

    asyncio.run(scenario())
