"""M89 示教服务状态机（spec §4.5、§5 T2、§6）。

异步用例一律「同步测试函数 + ``asyncio.run(scenario())``」；库落在 ``tmp_path``，
建表走 ``SQLModel.metadata.create_all``（测试专用，生产走 Alembic）。租约服务用
真实实现、把 ``wait_seconds`` 压到 0，录制闸门由 ``set_recording`` 直接驱动。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import maa_api.agent.demonstrations.service as demonstration_service_module
from maa_api.agent.demonstrations.service import (
    MAX_DEMONSTRATION_STEPS,
    DemonstrationService,
)
from maa_api.agent.lease import DeviceLeaseService
from maa_api.db.models import Demonstration, Pipeline, ResourceAsset
from maa_api.domain.enums import (
    DemonstrationStatus,
    PipelineSource,
    PipelineStatus,
    Priority,
    ScreenshotBackend,
    ScreenshotTrigger,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import LeaseSettings
from tests.fakes.v3_db import make_session_factory

Factory = async_sessionmaker[AsyncSession]


class FakeDeviceManager:
    """只实现 ``snapshot()``；状态与分辨率可控。"""

    def __init__(
        self,
        *,
        state: str = "connected",
        resolution: tuple[int, int] | None = (1280, 720),
    ) -> None:
        self.state = state
        self.resolution = resolution

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "resolution": (
                {"width": self.resolution[0], "height": self.resolution[1]}
                if self.resolution is not None
                else None
            ),
        }


class FakeResources:
    """``convert`` 的资源服务替身：结构校验可控，注册记录调用并可注入错误。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.content_errors: list[dict[str, str]] = []
        self.error: Exception | None = None

    def validate_custom_task_content(self, content: Any) -> list[dict[str, str]]:
        del content
        return [dict(item) for item in self.content_errors]

    async def register_custom_task(
        self,
        *,
        name: str,
        description: str | None,
        content: dict[str, Any] | None = None,
        tasks: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "name": name,
                "description": description,
                "content": content,
                "tasks": tasks,
            }
        )
        if self.error is not None:
            raise self.error
        names = list(tasks) if tasks is not None else [name]
        return {
            "entry": name,
            "task_names": names,
            "items": [
                {"id": f"asset-{index}", "name": item}
                for index, item in enumerate(names)
            ],
        }


@pytest.fixture
def demo_env(tmp_path: Path):
    """``(factory, device, lease, service)``；用例结束销毁同步引擎。"""
    engines: list[AsyncEngine] = []

    async def setup() -> Factory:
        engine, factory = await make_session_factory(tmp_path / "demonstrations.db")
        engines.append(engine)
        return factory

    factory = asyncio.run(setup())
    device = FakeDeviceManager()
    lease = DeviceLeaseService(
        factory,
        boot_id="boot-test",
        settings_provider=lambda: SimpleNamespace(
            lease=LeaseSettings(wait_seconds=0)
        ),
        poll_seconds=0.02,
    )
    service = DemonstrationService(
        factory,
        device_manager=device,
        lease=lease,
        resource_service=FakeResources(),
    )
    yield factory, device, lease, service
    engines[0].sync_engine.dispose()


async def _rows(factory: Factory) -> list[Demonstration]:
    async with factory() as db:
        result = await db.execute(select(Demonstration).order_by(Demonstration.created_at))
        return list(result.scalars().all())


async def _note(
    service: DemonstrationService,
    demonstration_id: str,
    *,
    started_at: datetime,
    action: str = "click",
    points: dict[str, int | None] | None = None,
) -> dict[str, Any] | None:
    return await service.note_step(
        demonstration_id,
        action=action,
        points=points or {"x": 640, "y": 360},
        duration_ms=None,
        started_at=started_at,
        before=None,
        after=None,
        backend="adb",
    )


def test_start_conflicts_with_existing_recording(demo_env) -> None:
    factory, _device, lease, service = demo_env

    async def scenario() -> None:
        first = await service.start(title="第一次示教")
        assert first["status"] == DemonstrationStatus.RECORDING
        assert first["screen"] == [1280, 720]
        assert service.is_recording() is True
        assert service.active_id == first["id"]
        assert lease.recording() is True

        with pytest.raises(AppError) as caught:
            await service.start(title="第二次示教")
        assert caught.value.code == ErrorCode.DEMONSTRATION_STATE_INVALID
        assert caught.value.details == {
            "id": first["id"],
            "status": DemonstrationStatus.RECORDING,
            "action": "start",
        }

        rows = await _rows(factory)
        assert len(rows) == 1
        assert rows[0].id == first["id"]
        assert rows[0].title == "第一次示教"
        assert rows[0].status == DemonstrationStatus.RECORDING

    asyncio.run(scenario())


def test_start_rejects_running_pipeline(demo_env) -> None:
    factory, _device, lease, service = demo_env

    async def scenario() -> None:
        async with factory() as db:
            db.add(
                Pipeline(
                    id="pipeline-running",
                    source=PipelineSource.MANUAL,
                    priority=Priority.MANUAL,
                    status=PipelineStatus.RUNNING,
                    task_count=0,
                )
            )
            await db.commit()

        with pytest.raises(AppError) as caught:
            await service.start(title="示教")
        assert caught.value.code == ErrorCode.PIPELINE_ALREADY_RUNNING
        assert caught.value.details == {"pipeline_id": "pipeline-running"}
        assert lease.recording() is False
        assert service.is_recording() is False
        assert await _rows(factory) == []

    asyncio.run(scenario())


def test_start_rejects_disconnected_device(demo_env) -> None:
    factory, device, lease, service = demo_env

    async def scenario() -> None:
        device.state = "disconnected"
        with pytest.raises(AppError) as caught:
            await service.start(title="示教")
        assert caught.value.code == ErrorCode.DEVICE_NOT_CONNECTED

        device.state = "connected"
        device.resolution = None
        with pytest.raises(AppError) as caught:
            await service.start(title="示教")
        assert caught.value.code == ErrorCode.DEVICE_NOT_CONNECTED
        assert lease.recording() is False
        assert await _rows(factory) == []

    asyncio.run(scenario())


def test_concurrent_start_only_one_row(demo_env) -> None:
    factory, _device, lease, service = demo_env

    async def scenario() -> None:
        results = await asyncio.gather(
            service.start(title="甲"),
            service.start(title="乙"),
            return_exceptions=True,
        )
        succeeded = [result for result in results if isinstance(result, dict)]
        failed = [result for result in results if isinstance(result, AppError)]
        assert len(succeeded) == 1
        assert len(failed) == 1
        assert failed[0].code == ErrorCode.DEMONSTRATION_STATE_INVALID

        rows = await _rows(factory)
        assert [row.id for row in rows] == [succeeded[0]["id"]]
        assert lease.recording() is True

    asyncio.run(scenario())


def test_stop_transitions(demo_env) -> None:
    factory, _device, lease, service = demo_env
    started_at = datetime(2026, 11, 10, 8, 0, 0)

    async def scenario() -> None:
        # 零步停止 → discarded。
        zero = await service.start(title="零步")
        stopped = await service.stop(zero["id"])
        assert stopped["status"] == DemonstrationStatus.DISCARDED
        assert stopped["steps"] == []
        assert lease.recording() is False
        assert service.active_id is None

        # 至少一步 → recorded。
        recorded = await service.start(title="有步骤")
        await _note(service, recorded["id"], started_at=started_at)
        stopped = await service.stop(recorded["id"])
        assert stopped["status"] == DemonstrationStatus.RECORDED
        assert len(stopped["steps"]) == 1
        assert lease.recording() is False

        # 显式丢弃 → discarded，即使有步骤。
        discarded = await service.start(title="丢弃")
        await _note(service, discarded["id"], started_at=started_at)
        stopped = await service.stop(discarded["id"], discard=True)
        assert stopped["status"] == DemonstrationStatus.DISCARDED
        assert len(stopped["steps"]) == 1
        assert lease.recording() is False

        assert [row.status for row in await _rows(factory)] == [
            DemonstrationStatus.DISCARDED,
            DemonstrationStatus.RECORDED,
            DemonstrationStatus.DISCARDED,
        ]

    asyncio.run(scenario())


def test_stop_on_recorded_is_state_invalid(demo_env) -> None:
    _factory, _device, _lease, service = demo_env

    async def scenario() -> None:
        out = await service.start(title="示教")
        await _note(
            service,
            out["id"],
            started_at=datetime(2026, 11, 10, 8, 0, 0),
        )
        stopped = await service.stop(out["id"])
        assert stopped["status"] == DemonstrationStatus.RECORDED

        with pytest.raises(AppError) as caught:
            await service.stop(out["id"])
        assert caught.value.code == ErrorCode.DEMONSTRATION_STATE_INVALID
        assert caught.value.details == {
            "id": out["id"],
            "status": DemonstrationStatus.RECORDED,
            "action": "stop",
        }

    asyncio.run(scenario())


def test_stop_unknown_id_is_not_found(demo_env) -> None:
    _factory, _device, _lease, service = demo_env

    async def scenario() -> None:
        with pytest.raises(AppError) as caught:
            await service.stop("missing-id")
        assert caught.value.code == ErrorCode.DEMONSTRATION_NOT_FOUND
        assert caught.value.details == {"id": "missing-id"}

    asyncio.run(scenario())


def test_recover_and_abort_discard(demo_env) -> None:
    factory, _device, lease, service = demo_env
    started_at = datetime(2026, 11, 10, 8, 0, 0)

    async def scenario() -> None:
        async with factory() as db:
            db.add(
                Demonstration(
                    id="leftover-recording",
                    title="重启前遗留",
                    status=DemonstrationStatus.RECORDING,
                )
            )
            db.add(
                Demonstration(
                    id="already-recorded",
                    title="已停止",
                    status=DemonstrationStatus.RECORDED,
                    steps=[{"seq": 1, "action": "click"}],
                )
            )
            await db.commit()

        assert await service.recover() == 1
        by_id = {row.id: row for row in await _rows(factory)}
        assert by_id["leftover-recording"].status == DemonstrationStatus.DISCARDED
        assert by_id["already-recorded"].status == DemonstrationStatus.RECORDED
        assert lease.recording() is False
        assert service.active_id is None

        # 设备在录制中断开：abort 丢弃活动行并释放闸门。
        out = await service.start(title="设备断开")
        await _note(service, out["id"], started_at=started_at)
        await service.abort("device_lost")
        by_id = {row.id: row for row in await _rows(factory)}
        assert by_id[out["id"]].status == DemonstrationStatus.DISCARDED
        assert lease.recording() is False
        assert service.active_id is None

    asyncio.run(scenario())


def test_note_step_sets_at_utc_z(demo_env) -> None:
    _factory, _device, _lease, service = demo_env
    base = datetime(2026, 11, 10, 8, 30, 0)

    async def scenario() -> None:
        out = await service.start(title="时间戳")
        first = await _note(service, out["id"], started_at=base)
        assert first is not None
        at = first["steps"][-1]["at"]
        assert at == "2026-11-10T08:30:00Z"
        parsed = datetime.fromisoformat(at.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.astimezone(UTC).replace(tzinfo=None) == base
        # 第一步的 delay_ms 恒为 0。
        assert first["steps"][-1]["delay_ms"] == 0

        second = await _note(
            service,
            out["id"],
            started_at=base + timedelta(milliseconds=1250),
        )
        assert second is not None
        assert second["steps"][-1]["delay_ms"] == 1250

        # 时间倒退夹 0，超过 10 秒夹 10000。
        early = await _note(
            service,
            out["id"],
            started_at=base + timedelta(milliseconds=500),
        )
        assert early is not None
        assert early["steps"][-1]["delay_ms"] == 0
        late = await _note(
            service,
            out["id"],
            started_at=base + timedelta(seconds=20),
        )
        assert late is not None
        assert late["steps"][-1]["delay_ms"] == 10000

        # 步骤形状：不适用的字段写 None，screen 取设备快照。
        step = late["steps"][-1]
        assert step["seq"] == 4
        assert step["action"] == "click"
        assert step["x"] == 640 and step["y"] == 360
        assert step["x1"] is None and step["y2"] is None
        assert step["screen"] == [1280, 720]
        assert step["screenshot_id"] is None
        assert step["result_screenshot_id"] is None

    asyncio.run(scenario())


def test_note_step_archives_screenshots_and_tolerates_failure(
    demo_env, monkeypatch
) -> None:
    """M89 §4.5：两张图在同一次追加里归档；单张失败 id 写 ``None``，步骤仍在。"""
    _factory, _device, _lease, service = demo_env
    before = Image.new("RGB", (8, 8), "red")
    after = Image.new("RGB", (8, 8), "blue")
    archived: list[Image.Image] = []

    async def fake_archive(session, image, *, trigger, backend):
        archived.append(image)
        assert trigger == ScreenshotTrigger.MANUAL
        assert backend == ScreenshotBackend.ADB
        if image is after:
            raise RuntimeError("归档磁盘写满")
        return object(), {"screenshot_id": "shot-before"}

    monkeypatch.setattr(
        demonstration_service_module, "archive_screenshot", fake_archive
    )

    async def scenario() -> None:
        out = await service.start(title="截图")
        result = await service.note_step(
            out["id"],
            action="click",
            points={"x": 1, "y": 2},
            duration_ms=None,
            started_at=datetime(2026, 11, 10, 10, 0, 0),
            before=before,
            after=after,
            backend="adb",
        )
        assert result is not None
        step = result["steps"][-1]
        assert archived == [before, after]
        assert step["screenshot_id"] == "shot-before"
        assert step["result_screenshot_id"] is None

    asyncio.run(scenario())


def test_note_step_stops_at_100(demo_env) -> None:
    _factory, _device, lease, service = demo_env
    base = datetime(2026, 11, 10, 9, 0, 0)

    async def scenario() -> None:
        out = await service.start(title="上限")
        latest: dict[str, Any] | None = None
        for index in range(MAX_DEMONSTRATION_STEPS):
            latest = await _note(
                service,
                out["id"],
                started_at=base + timedelta(seconds=index),
            )
            assert latest is not None
            if index < MAX_DEMONSTRATION_STEPS - 1:
                assert latest["status"] == DemonstrationStatus.RECORDING

        assert latest is not None
        assert latest["status"] == DemonstrationStatus.RECORDED
        assert len(latest["steps"]) == MAX_DEMONSTRATION_STEPS
        assert latest["steps"][-1]["seq"] == MAX_DEMONSTRATION_STEPS
        assert lease.recording() is False
        assert service.active_id is None

        # 第 100 步之后状态是 recorded，再追加是状态错误。
        with pytest.raises(AppError) as caught:
            await _note(
                service,
                out["id"],
                started_at=base + timedelta(seconds=MAX_DEMONSTRATION_STEPS),
            )
        assert caught.value.code == ErrorCode.DEMONSTRATION_STATE_INVALID

    asyncio.run(scenario())


def test_list_filters_and_paginates(demo_env) -> None:
    factory, _device, _lease, service = demo_env

    async def scenario() -> None:
        out = await service.start(title="录制中")
        async with factory() as db:
            db.add(
                Demonstration(
                    id="recorded-1",
                    title="已停止",
                    status=DemonstrationStatus.RECORDED,
                    steps=[],
                    created_at=datetime(2000, 1, 1, 8, 0, 0),
                    updated_at=datetime(2000, 1, 1, 8, 0, 0),
                )
            )
            await db.commit()

        page = await service.list(status=None, page=1, size=20)
        assert page["total"] == 2
        assert [item["id"] for item in page["items"]] == [out["id"], "recorded-1"]
        filtered = await service.list(status="recorded", page=1, size=20)
        assert filtered["total"] == 1
        assert filtered["items"][0]["id"] == "recorded-1"

        with pytest.raises(AppError) as caught:
            await service.list(status="unknown", page=1, size=20)
        assert caught.value.code == ErrorCode.INVALID_PARAMETER

    asyncio.run(scenario())


# --------------------------------------------------------------------------- convert


async def _recorded(service: DemonstrationService, *, title: str = "示教") -> str:
    """起一条有一格步骤的 ``recorded`` 示教，返回 id。"""
    out = await service.start(title=title)
    await _note(service, out["id"], started_at=datetime(2026, 11, 10, 8, 0, 0))
    await service.stop(out["id"])
    return out["id"]


async def _assets(factory: Factory) -> list[ResourceAsset]:
    async with factory() as db:
        result = await db.execute(select(ResourceAsset))
        return list(result.scalars().all())


def test_service_convert_preview_keeps_recorded(demo_env) -> None:
    """``register=false``：编译并返回，状态仍是 recorded，不写资产、不写 converted_to。"""
    factory, _device, _lease, service = demo_env

    async def scenario() -> None:
        demonstration_id = await _recorded(service)

        result = await service.convert(demonstration_id, register=False)

        assert result["status"] == DemonstrationStatus.RECORDED
        assert result["converted_to"] is None
        compiled = result["compile"]
        assert compiled["valid"] is True
        assert compiled["register"] is False
        assert compiled["registered"] == []
        assert compiled["entry"] == f"Custom_Demo_{demonstration_id[:8]}_1"
        assert compiled["chain"][-1].endswith("_Done")
        assert "action" in compiled["tasks"][compiled["chain"][-1]]

        rows = await _rows(factory)
        assert rows[0].status == DemonstrationStatus.RECORDED
        assert rows[0].converted_to is None
        assert await _assets(factory) == []

    asyncio.run(scenario())


def test_service_convert_register_marks_converted(demo_env) -> None:
    """``register=true``：一次注册整条链，写 converted_to 四个键并置 converted。"""
    factory, _device, lease, service = demo_env

    async def scenario() -> None:
        demonstration_id = await _recorded(service, title="新活动示教")
        resources = service._resource_service

        result = await service.convert(demonstration_id, register=True)

        assert result["status"] == DemonstrationStatus.CONVERTED
        converted = result["converted_to"]
        assert set(converted) == {"entry", "task_names", "asset_ids", "registered_at"}
        assert converted["entry"] == result["compile"]["entry"]
        assert converted["entry"] == f"Custom_Demo_{demonstration_id[:8]}_1"
        assert len(converted["task_names"]) == len(converted["asset_ids"]) == 2
        assert converted["asset_ids"] == ["asset-0", "asset-1"]
        assert converted["registered_at"].endswith("Z")
        assert result["compile"]["registered"] == [
            {"id": "asset-0", "name": converted["task_names"][0]},
            {"id": "asset-1", "name": converted["task_names"][1]},
        ]
        # 服务只调一次注册，name 是入口、tasks 是整条链、描述取自标题。
        assert len(resources.calls) == 1
        assert resources.calls[0]["name"] == converted["entry"]
        assert resources.calls[0]["tasks"] == result["compile"]["tasks"]
        assert resources.calls[0]["description"] == "示教 新活动示教"

        rows = await _rows(factory)
        assert rows[0].status == DemonstrationStatus.CONVERTED
        assert lease.recording() is False

        # 已转换是终态：再转 409。
        with pytest.raises(AppError) as caught:
            await service.convert(demonstration_id, register=True)
        assert caught.value.code == ErrorCode.DEMONSTRATION_STATE_INVALID

    asyncio.run(scenario())


def test_service_convert_register_failure_keeps_recorded(demo_env) -> None:
    """注册抛 ``RESOURCE_LOAD_FAILED``：行保持 recorded，converted_to 为空，无部分资产。"""
    factory, _device, _lease, service = demo_env

    async def scenario() -> None:
        demonstration_id = await _recorded(service)
        resources = service._resource_service
        resources.error = AppError(
            ErrorCode.RESOURCE_LOAD_FAILED, "自定义任务链已回滚"
        )

        with pytest.raises(AppError) as caught:
            await service.convert(demonstration_id, register=True)

        assert caught.value.code == ErrorCode.RESOURCE_LOAD_FAILED
        rows = await _rows(factory)
        assert rows[0].status == DemonstrationStatus.RECORDED
        assert rows[0].converted_to is None
        assert await _assets(factory) == []

    asyncio.run(scenario())
