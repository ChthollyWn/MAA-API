"""M28 D3（基建方案绝对路径）与 Q45（按 period 自动选班次）。

规格 §2.7 D3、§8.3「修复」与 Q45：``mode=10000`` 的 Infrast 任务，``filename`` 由执行器
解析成 custom 层下的绝对路径再交给内核；没显式给 ``plan_index`` 时读方案文件、按本机
时区当前时间选第一个覆盖现在的班次（``period`` 支持跨午夜），都不覆盖就报
``TASK_PARAM_INVALID``「当前时间不在任何班次的 period 内」；显式给了 ``plan_index``
保持原语义（不读班次）。

上游协议：period 的 ``[["22:00", "23:59"], ["00:00", "06:00"]]`` 写法就是跨天
（https://docs.maa.plus/zh-cn/protocol/base-scheduling-schema.html ，2026-10-10 抓取）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.core.registry import DEFAULT_CORE_ID, CoreRegistry
from maa_api.db.models import Pipeline, Task
from maa_api.db.session import make_engine
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority, TaskStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.pipeline_runner import PipelineRunner, select_infrast_plan_index
from maa_api.services.resource_service import ResourceService
from tests.fakes.fake_asst import SUCCESS_SCRIPT, FakeAsst

TZ = timezone(timedelta(hours=8))
PLAN_FILENAME = "custom_infrast/three-shifts.json"

PLAN_CONTENT: dict[str, Any] = {
    "title": "243 三班",
    "planTimes": "3班",
    "plans": [
        {"name": "早班", "period": [["06:00", "14:00"]], "rooms": {"manufacture": []}},
        {"name": "晚班", "period": [["14:00", "22:00"]], "rooms": {"manufacture": []}},
        {"name": "夜班", "period": [["22:00", "06:00"]], "rooms": {"manufacture": []}},
    ],
}


# --------------------------------------------------------------------------- #
# 纯函数：按 period 选班次
# --------------------------------------------------------------------------- #
def moment(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 10, hour, minute, tzinfo=TZ)


def test_selects_the_first_plan_whose_period_covers_now() -> None:
    assert select_infrast_plan_index(PLAN_CONTENT, moment=moment(7)) == 0
    assert select_infrast_plan_index(PLAN_CONTENT, moment=moment(13, 59)) == 0
    assert select_infrast_plan_index(PLAN_CONTENT, moment=moment(22)) == 2
    assert select_infrast_plan_index(PLAN_CONTENT, moment=moment(23, 30)) == 2


def test_cross_midnight_period_covers_the_early_hours() -> None:
    content = {"plans": [{"name": "夜班", "period": [["22:00", "06:00"]]}]}
    assert select_infrast_plan_index(content, moment=moment(23)) == 0
    assert select_infrast_plan_index(content, moment=moment(0)) == 0
    assert select_infrast_plan_index(content, moment=moment(5, 59)) == 0
    assert select_infrast_plan_index(content, moment=moment(6)) is None
    assert select_infrast_plan_index(content, moment=moment(21, 59)) is None


def test_multiple_ranges_and_first_match_order() -> None:
    content = {
        "plans": [
            {"name": "共用", "period": [["22:00", "23:59"], ["00:00", "06:00"]]},
            {"name": "备份", "period": [["00:00", "23:59"]]},
        ]
    }
    assert select_infrast_plan_index(content, moment=moment(2)) == 0
    assert select_infrast_plan_index(content, moment=moment(12)) == 1


def test_no_covering_period_and_broken_periods_return_none() -> None:
    assert select_infrast_plan_index({"plans": []}, moment=moment(12)) is None
    assert select_infrast_plan_index({}, moment=moment(12)) is None
    assert (
        select_infrast_plan_index(
            {"plans": [{"name": "缺 period"}]}, moment=moment(12)
        )
        is None
    )
    # 畸形 period 跳过，不影响后面的班次。
    content = {
        "plans": [
            {"name": "坏", "period": [["8:00", "16:00"], "bad"]},
            {"name": "好", "period": [["06:00", "18:00"]]},
        ]
    }
    assert select_infrast_plan_index(content, moment=moment(12)) == 1
    # 起止相同按空区间处理（没有证据表明它表示全天）。
    same = {"plans": [{"name": "空", "period": [["08:00", "08:00"]]}]}
    assert select_infrast_plan_index(same, moment=moment(8)) is None


# --------------------------------------------------------------------------- #
# 执行器：D3 路径 + Q45 选班
# --------------------------------------------------------------------------- #
class _FakeCoreClient:
    """FakeAsst 的异步适配；记录 ``append_task`` 的原始参数。"""

    def __init__(self, asst: FakeAsst) -> None:
        self.asst = asst
        self.handlers: dict[str, Any] = {}
        self.loop = asyncio.get_running_loop()
        self.appended: list[tuple[str, dict[str, Any]]] = []
        asst.set_callback(self._on_callback)

    def on(self, event_type: str, handler: Any) -> None:
        self.handlers[event_type] = handler

    def _on_callback(self, message: int, details: bytes, _arg: Any) -> None:
        handler = self.handlers.get("CALLBACK")
        if handler is not None:
            payload = {"msg": message, "details": json.loads(details)}
            self.loop.call_soon_threadsafe(handler, payload)

    async def append_task(self, type_name: str, params: dict[str, Any]) -> int:
        self.appended.append((type_name, dict(params)))
        return self.asst.append_task(type_name, params)

    async def start(self) -> bool:
        return self.asst.start()

    async def stop(self) -> bool:
        return self.asst.stop()


class _Supervisor:
    state = "ready"
    generation = 1


async def _create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
def infrast_env(tmp_path: Path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'infrast-runner.db'}", poolclass=NullPool
    )
    asyncio.run(_create_schema(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    resource_root = tmp_path / "resource"
    resource_root.mkdir()
    yield factory, resource_root
    engine.sync_engine.dispose()


def write_plan(resource_root: Path, content: dict[str, Any] = PLAN_CONTENT) -> Path:
    target = resource_root / "maa-layers" / "custom" / "resource" / PLAN_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return target


async def seed_task(
    factory,
    *,
    params: dict[str, Any],
    raw_params: dict[str, Any],
) -> tuple[Pipeline, Task]:
    pipeline = Pipeline(
        id="pipeline-infrast",
        source=PipelineSource.AGENT,
        priority=Priority.AGENT,
        status=PipelineStatus.RUNNING,
        task_count=1,
    )
    task = Task(
        id="task-infrast",
        pipeline_id=pipeline.id,
        order_index=0,
        type_name="Infrast",
        task_name="基建换班",
        params=params,
        raw_params=raw_params,
        status=TaskStatus.PENDING,
    )
    async with factory() as session:
        session.add(pipeline)
        session.add(task)
        await session.commit()
    return pipeline, task


def make_runner(
    factory,
    resource_root: Path,
    *,
    now: datetime | None = None,
):
    asst = FakeAsst(script=SUCCESS_SCRIPT, delay_scale=0)
    core = _FakeCoreClient(asst)
    registry = CoreRegistry()
    registry.register(DEFAULT_CORE_ID, core)
    runner = PipelineRunner(
        registry,
        _Supervisor(),
        factory,
        resource_service=ResourceService(factory, resource_root=resource_root),
        now_provider=(lambda: now) if now is not None else None,
    )
    runner._running = True
    return runner, asst, core


async def run_task(runner, factory, resource_root, *, params, raw_params):
    pipeline, task = await seed_task(factory, params=params, raw_params=raw_params)
    await runner._execute_pipeline(pipeline, [task])
    async with factory() as session:
        stored = await session.get(Task, task.id)
    return stored


def test_explicit_plan_index_keeps_its_value_and_only_resolves_the_path(
    infrast_env,
) -> None:
    factory, resource_root = infrast_env
    expected = write_plan(resource_root)

    async def scenario() -> None:
        runner, _asst, core = make_runner(factory, resource_root, now=moment(12))
        stored = await run_task(
            runner,
            factory,
            resource_root,
            params={"mode": 10000, "filename": PLAN_FILENAME, "plan_index": 2},
            raw_params={"mode": 10000, "filename": PLAN_FILENAME, "plan_index": 2},
        )
        assert core.appended == [
            (
                "Infrast",
                {"mode": 10000, "filename": str(expected), "plan_index": 2},
            )
        ]
        assert stored.status == TaskStatus.COMPLETED
        # 存库的 params 保持相对 filename（只有投给内核的那份换成绝对路径）。
        assert stored.params["filename"] == PLAN_FILENAME
        await runner.stop()
        runner.registry.get(DEFAULT_CORE_ID).asst.stop()

    asyncio.run(scenario())


def test_missing_plan_index_picks_the_shift_covering_now(infrast_env) -> None:
    factory, resource_root = infrast_env
    expected = write_plan(resource_root)

    async def scenario() -> None:
        # 23:30 落在夜班（22:00-06:00，跨午夜）——第三个方案。
        runner, _asst, core = make_runner(factory, resource_root, now=moment(23, 30))
        stored = await run_task(
            runner,
            factory,
            resource_root,
            params={"mode": 10000, "filename": PLAN_FILENAME, "plan_index": 0},
            raw_params={"mode": 10000, "filename": PLAN_FILENAME},
        )
        assert core.appended == [
            ("Infrast", {"mode": 10000, "filename": str(expected), "plan_index": 2})
        ]
        assert stored.status == TaskStatus.COMPLETED
        await runner.stop()
        runner.registry.get(DEFAULT_CORE_ID).asst.stop()

    asyncio.run(scenario())


def test_missing_plan_index_but_no_period_covers_now_fails_the_task(infrast_env) -> None:
    factory, resource_root = infrast_env
    write_plan(
        resource_root,
        {"plans": [{"name": "只有早班", "period": [["06:00", "14:00"]], "rooms": {}}]},
    )

    async def scenario() -> None:
        runner, _asst, core = make_runner(factory, resource_root, now=moment(20))
        stored = await run_task(
            runner,
            factory,
            resource_root,
            params={"mode": 10000, "filename": PLAN_FILENAME, "plan_index": 0},
            raw_params={"mode": 10000, "filename": PLAN_FILENAME},
        )
        assert core.appended == []
        assert stored.status == TaskStatus.FAILED
        assert stored.error_code == ErrorCode.TASK_PARAM_INVALID.value
        assert "当前时间不在任何班次的 period 内" in stored.error_message
        await runner.stop()

    asyncio.run(scenario())


def test_missing_plan_file_reports_task_param_invalid_with_the_path(infrast_env) -> None:
    factory, resource_root = infrast_env
    resolved = resource_root / "maa-layers" / "custom" / "resource" / PLAN_FILENAME

    async def scenario() -> None:
        runner, _asst, core = make_runner(factory, resource_root, now=moment(12))
        stored = await run_task(
            runner,
            factory,
            resource_root,
            params={"mode": 10000, "filename": PLAN_FILENAME, "plan_index": 1},
            raw_params={"mode": 10000, "filename": PLAN_FILENAME, "plan_index": 1},
        )
        assert core.appended == []
        assert stored.status == TaskStatus.FAILED
        assert stored.error_code == ErrorCode.TASK_PARAM_INVALID.value
        assert str(resolved) in stored.error_message
        await runner.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "filename",
    [
        "/etc/passwd",
        "../../../../etc/passwd",
        "custom_infrast/../../../secret.json",
        "custom_infrast\\plan.json",
        "",
    ],
)
def test_escaping_or_unsafe_filenames_are_rejected(infrast_env, filename) -> None:
    factory, resource_root = infrast_env

    async def scenario() -> None:
        runner, _asst, core = make_runner(factory, resource_root, now=moment(12))
        stored = await run_task(
            runner,
            factory,
            resource_root,
            params={"mode": 10000, "filename": filename, "plan_index": 0},
            raw_params={"mode": 10000, "filename": filename, "plan_index": 0},
        )
        assert core.appended == []
        assert stored.status == TaskStatus.FAILED
        assert stored.error_code == ErrorCode.TASK_PARAM_INVALID.value
        await runner.stop()

    asyncio.run(scenario())


def test_default_mode_infrast_is_untouched(infrast_env) -> None:
    """只有 ``mode=10000`` 走 D3/Q45；默认换班照原样投给内核。"""

    factory, resource_root = infrast_env

    async def scenario() -> None:
        runner, _asst, core = make_runner(factory, resource_root, now=moment(12))
        stored = await run_task(
            runner,
            factory,
            resource_root,
            params={"mode": 0, "facility": ["Mfg", "Trade"], "threshold": 0.3},
            raw_params={"mode": 0},
        )
        assert core.appended == [
            ("Infrast", {"mode": 0, "facility": ["Mfg", "Trade"], "threshold": 0.3})
        ]
        assert stored.status == TaskStatus.COMPLETED
        await runner.stop()

    asyncio.run(scenario())


def test_resource_service_rejects_paths_outside_the_custom_layer(
    resource_root, retention_session_factory
) -> None:
    async def scenario() -> None:
        service = ResourceService(
            retention_session_factory, resource_root=resource_root
        )
        for bad in ("../secret.json", "/tmp/secret.json", "custom_infrast\\x.json", ""):
            with pytest.raises(AppError) as exc_info:
                await service.resolve_infrast_plan_path(bad)
            assert exc_info.value.code == ErrorCode.TASK_PARAM_INVALID

    asyncio.run(scenario())


def test_resource_service_reads_a_plan_and_reports_missing_files(
    resource_root, retention_session_factory
) -> None:
    expected = resource_root / "maa-layers" / "custom" / "resource" / PLAN_FILENAME
    expected.parent.mkdir(parents=True, exist_ok=True)
    expected.write_text(json.dumps(PLAN_CONTENT, ensure_ascii=False), encoding="utf-8")

    async def scenario() -> None:
        service = ResourceService(
            retention_session_factory, resource_root=resource_root
        )
        path = await service.resolve_infrast_plan_path(PLAN_FILENAME)
        assert path == expected
        assert await service.read_infrast_plan(PLAN_FILENAME) == PLAN_CONTENT
        with pytest.raises(AppError) as missing:
            await service.resolve_infrast_plan_path("custom_infrast/nope.json")
        assert missing.value.code == ErrorCode.TASK_PARAM_INVALID
        assert "nope.json" in missing.value.message
        # 目录不是文件、JSON 坏了都算读不出来，报 RESOURCE_LOAD_FAILED（带路径）。
        broken = expected.parent / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        with pytest.raises(AppError) as invalid:
            await service.read_infrast_plan("custom_infrast/broken.json")
        assert invalid.value.code == ErrorCode.RESOURCE_LOAD_FAILED
        assert "broken.json" in invalid.value.message

    asyncio.run(scenario())

