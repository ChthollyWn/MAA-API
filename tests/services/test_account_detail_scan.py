"""干员详情扫描的写回口径（M36 规格 §4.4、§4.5）。

只测 ``OperatorBoxService.apply_detail_scan`` 与 ``record_training_scan`` 这一层：
不点设备、不打网。扫描编排（名单、点卡、换屏）的用例另文件。

全部离线：临时 SQLite（``NullPool`` 便于一个用例里跑多段 ``asyncio.run``）、手工构造
的 naive UTC 时间（库内统一 naive UTC，docs/04 §3.2）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.models import Pipeline, Task
from maa_api.db.repositories.account import (
    RECOGNITION_KIND_TRAINING,
    AccountOperatorRepository,
    AccountRecognitionRepository,
)
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority
from maa_api.services.account.box import OperatorBoxService
from maa_api.settings import Settings

NOW = datetime(2026, 10, 11, 2, 0, 0)
YESTERDAY = NOW - timedelta(days=1)


@pytest.fixture
def account_session_factory(tmp_path):
    """临时库 + ``NullPool`` 会话工厂（照 ``tests/services/test_account_box.py``）。"""
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'account_detail_scan.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _service(factory) -> OperatorBoxService:
    settings = Settings()
    return OperatorBoxService(factory, settings_provider=lambda: settings)


def _seed(factory, char_id: str = "char_003_kalts", **fields) -> None:
    """直接经仓储写一行：默认是 M35 之后的真机库形态（练度三列 ``NULL``＝未识别）。"""
    defaults: dict[str, object] = dict(
        name="凯尔希",
        rarity=6,
        profession="医疗",
        own=True,
        elite=2,
        level=90,
        potential=6,
        skill_level=None,
        masteries=None,
        modules=None,
        recognized_values={"own": True, "elite": 2, "level": 90, "potential": 6},
        field_sources={
            "own": "recognition",
            "elite": "recognition",
            "level": "recognition",
            "potential": "recognition",
        },
        recognized_at=YESTERDAY,
        updated_at=YESTERDAY,
    )
    defaults.update(fields)

    async def scenario():
        async with factory() as session:
            repo = AccountOperatorRepository(session)
            row = repo.new_row(char_id)
            for field, value in defaults.items():
                setattr(row, field, value)
            await repo.upsert(row)
            await session.commit()

    asyncio.run(scenario())


def _row(factory, char_id: str):
    """读原始行（``get_operator`` 给的是 wire，看不到 ``recognized_values``）。"""

    async def scenario():
        async with factory() as session:
            return await AccountOperatorRepository(session).get(char_id)

    return asyncio.run(scenario())


def _pipeline(factory, pipeline_id: str) -> None:
    """造一条靠得住的 ``pipelines`` 行：``account_recognition.pipeline_id`` 有外键。"""

    async def scenario():
        async with factory() as session:
            pipeline = Pipeline(
                id=pipeline_id,
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
                status=PipelineStatus.RUNNING,
                task_count=1,
            )
            task = Task(
                id=f"{pipeline_id}-task",
                pipeline_id=pipeline.id,
                order_index=0,
                type_name="Custom",
                task_name="练度扫描",
                params={},
            )
            await PipelineRepository(session).create(pipeline, [task])
            await session.commit()

    asyncio.run(scenario())


def test_reads_all_three_columns_and_keeps_the_operbox_stamp(account_session_factory):
    _seed(account_session_factory)
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_detail_scan(
            "char_003_kalts",
            skill_level=7,
            masteries={1: 3, 2: 0, 3: 3},
            modules={"X": 3},
            recognized_at=NOW,
        )
        assert result == {
            "char_id": "char_003_kalts",
            "updated": ["masteries", "modules", "skill_level"],
            "skipped": [],
        }
        detail = await service.get_operator("char_003_kalts")
        assert detail["skill_level"] == 7
        assert detail["masteries"] == {"1": 3, "2": 0, "3": 3}
        assert detail["modules"] == {"X": 3}
        # 三列从此有来源，且来源是识别（详情页扫描就是识别通道）。
        assert detail["field_sources"]["skill_level"] == "recognition"
        assert detail["field_sources"]["masteries"] == "recognition"
        assert detail["field_sources"]["modules"] == "recognition"
        # 另外四列的口径不动：扫描不重读它们。
        assert detail["field_sources"]["elite"] == "recognition"
        assert detail["elite"] == 2
        # ``recognized_at`` 仍是 OperBox 那次的时间，扫描只动 ``updated_at``。
        assert detail["recognized_at"] == "2026-10-10T02:00:00Z"
        assert detail["updated_at"] == "2026-10-11T02:00:00Z"

    asyncio.run(scenario())

    row = _row(account_session_factory, "char_003_kalts")
    # 台账合并：四列的老值保留，三列补上这次读到的值。
    assert row.recognized_values == {
        "own": True,
        "elite": 2,
        "level": 90,
        "potential": 6,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 3},
        "modules": {"X": 3},
    }


def test_nothing_read_means_nothing_written(account_session_factory):
    _seed(account_session_factory)
    service = _service(account_session_factory)

    async def scenario():
        # 三个字段都是 ``None``：这次没读到（比如模组面板读不出、OCR 没命中）。
        result = await service.apply_detail_scan("char_003_kalts", recognized_at=NOW)
        assert result == {"char_id": "char_003_kalts", "updated": [], "skipped": []}
        detail = await service.get_operator("char_003_kalts")
        assert detail["skill_level"] is None
        assert detail["masteries"] is None
        assert detail["modules"] is None
        assert "skill_level" not in detail["field_sources"]
        # 一行都没改，时间戳也不该动。
        assert detail["updated_at"] == "2026-10-10T02:00:00Z"

    asyncio.run(scenario())


def test_unknown_operator_returns_none(account_session_factory):
    _seed(account_session_factory)
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_detail_scan(
            "char_999_nobody", skill_level=7, recognized_at=NOW
        )
        assert result is None

    asyncio.run(scenario())


def test_partial_read_only_writes_the_field_it_read(account_session_factory):
    _seed(account_session_factory)
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_detail_scan(
            "char_003_kalts", skill_level=5, recognized_at=NOW
        )
        assert result == {"char_id": "char_003_kalts", "updated": ["skill_level"], "skipped": []}
        detail = await service.get_operator("char_003_kalts")
        assert detail["skill_level"] == 5
        # 没读到的两列保持未识别，不许顺手兜底。
        assert detail["masteries"] is None
        assert detail["modules"] is None

    asyncio.run(scenario())


def test_merge_is_per_key_and_keeps_the_other_slots(account_session_factory):
    _seed(
        account_session_factory,
        skill_level=7,
        masteries={"1": 3, "2": 0, "3": 0},
        modules={"X": 3},
    )
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_detail_scan(
            "char_003_kalts", masteries={2: 3}, modules={"Y": 2}, recognized_at=NOW
        )
        assert result == {
            "char_id": "char_003_kalts",
            "updated": ["masteries", "modules"],
            "skipped": [],
        }
        detail = await service.get_operator("char_003_kalts")
        assert detail["masteries"] == {"1": 3, "2": 3, "3": 0}
        assert detail["modules"] == {"X": 3, "Y": 2}
        # 这次没读技能等级：既有值原样留着。
        assert detail["skill_level"] == 7

    asyncio.run(scenario())


def test_read_empty_mappings_turn_unknown_into_known(account_session_factory):
    _seed(account_session_factory)
    service = _service(account_session_factory)

    async def scenario():
        # 详情页明确读到「三个技能都没专精」与「没装模组」：``NULL`` 变已知。
        # 专精那一列按三个槽位补零（wire 形状），模组是空映射（＝确认没装）。
        result = await service.apply_detail_scan(
            "char_003_kalts",
            masteries={1: 0, 2: 0, 3: 0},
            modules={},
            recognized_at=NOW,
        )
        assert result == {
            "char_id": "char_003_kalts",
            "updated": ["masteries", "modules"],
            "skipped": [],
        }
        detail = await service.get_operator("char_003_kalts")
        assert detail["masteries"] == {"1": 0, "2": 0, "3": 0}
        assert detail["modules"] == {}

    asyncio.run(scenario())


def test_out_of_range_values_are_skipped_not_written(account_session_factory):
    _seed(account_session_factory)
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_detail_scan(
            "char_003_kalts",
            skill_level=9,  # 技能等级只有 1–7
            masteries={4: 1},  # 技能序号只有 1–3
            modules={"Z": 1, "X": 0},  # 模组类型只有 X/Y/D/A，等级只有 1–3
            recognized_at=NOW,
        )
        assert result == {
            "char_id": "char_003_kalts",
            "updated": [],
            "skipped": ["masteries", "modules", "skill_level"],
        }
        detail = await service.get_operator("char_003_kalts")
        # 越界不写也不抛：三列仍是未识别，没被脏值糊上。
        assert detail["skill_level"] is None
        assert detail["masteries"] is None
        assert detail["modules"] is None

    asyncio.run(scenario())


def test_garbled_shape_is_skipped_not_written(account_session_factory):
    _seed(account_session_factory)
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_detail_scan(
            "char_003_kalts",
            masteries="1:3",  # 不是映射
            modules=[("X", 2)],  # 不是映射
            recognized_at=NOW,
        )
        assert result == {
            "char_id": "char_003_kalts",
            "updated": [],
            "skipped": ["masteries", "modules"],
        }
        detail = await service.get_operator("char_003_kalts")
        assert detail["masteries"] is None
        assert detail["modules"] is None

    asyncio.run(scenario())


def test_detail_scan_never_touches_the_operbox_columns(account_session_factory):
    # 未识别状态的四列：扫描写三列时不许顺手把它们也改了（M35 §4.5）。
    _seed(
        account_session_factory,
        elite=None,
        level=None,
        potential=None,
        field_sources={"own": "recognition"},
    )
    service = _service(account_session_factory)

    async def scenario():
        await service.apply_detail_scan(
            "char_003_kalts",
            skill_level=7,
            masteries={1: 3},
            modules={"X": 1},
            recognized_at=NOW,
        )
        detail = await service.get_operator("char_003_kalts")
        assert detail["elite"] is None
        assert detail["level"] is None
        assert detail["potential"] is None
        assert detail["own"] is True
        assert detail["field_sources"] == {
            "own": "recognition",
            "skill_level": "recognition",
            "masteries": "recognition",
            "modules": "recognition",
        }

    asyncio.run(scenario())


def test_record_training_scan_writes_one_row_with_the_updated_count(account_session_factory):
    _pipeline(account_session_factory, "pipe-scan-2")
    service = _service(account_session_factory)

    async def scenario():
        await service.record_training_scan(updated=17, recognized_at=NOW)
        # 同一次扫描只写一条（``apply_detail_scan`` 是逐人调用的）：同一时间再来一次是
        # 补上下文，不新增行。
        await service.record_training_scan(updated=17, recognized_at=NOW)
        # 下一次扫描是新的一条。
        await service.record_training_scan(
            updated=4, recognized_at=NOW + timedelta(hours=1), pipeline_id="pipe-scan-2"
        )

        async with account_session_factory() as session:
            repo = AccountRecognitionRepository(session)
            latest = await repo.latest(RECOGNITION_KIND_TRAINING)
            assert latest is not None
            assert latest.item_count == 4
            assert latest.pipeline_id == "pipe-scan-2"
            assert await repo.last_at(RECOGNITION_KIND_TRAINING) == NOW + timedelta(hours=1)

    asyncio.run(scenario())
