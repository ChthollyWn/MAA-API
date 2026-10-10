"""M27 T3：账号数据四个仓储的行为（规格 §7.3、附录 E、docs/04 §9）。

覆盖 docs/04 §9 的纪律与规格 §7.3 的四项能力：

- **干员**（``AccountOperatorRepository``）：``new_row`` 的建行默认值、按 ``char_id``
  读、全表读取与筛选（名字片段 / 是否拥有 / 稀有度 / 职业 / 精英化）、``upsert`` 单条、
  ``upsert_many`` 批量写识别值、``last_recognized_at``；
- **仓库**（``AccountDepotItemRepository``）：``replace_all`` 整表替换（同 id 取最后
  一条、空表清空）、按分类与名字读取、``count``；
- **识别记录**（``AccountRecognitionRepository``）：``record`` 写入 + 补上下文、
  ``latest`` / ``last_at`` 最近一次（没有记录时 ``None``）；
- **差异**（``AccountOperatorDiffRepository``）：``create``、``list``（未处理 / 含已知
  / 限条数，带干员名）、``count_pending``、``dismiss``（选中或全部，幂等）。

仓储不 ``commit()``（事务边界由调用方决定）：最后一条用例在回滚后断言库里什么都没
留下。用例形态是同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库来自 ``tests/services/conftest.py``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from maa_api.db.models import AccountOperator, Pipeline, Task
from maa_api.db.repositories.account import (
    RECOGNITION_KIND_DEPOT,
    RECOGNITION_KIND_OPERBOX,
    AccountDepotItemRepository,
    AccountOperatorDiffRepository,
    AccountOperatorRepository,
    AccountRecognitionRepository,
    OperatorDiffRow,
)
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority

STAMP = datetime(2026, 10, 2, 8, 30, 0)
LATER = datetime(2026, 10, 3, 9, 0, 0)


def _operator(char_id: str, name: str, **overrides) -> AccountOperator:
    fields: dict = {"char_id": char_id, "name": name, "rarity": 5}
    fields.update(overrides)
    return AccountOperator(**fields)


def _depot_row(item_id: str, name: str, count: int, **overrides) -> dict:
    fields: dict = {
        "item_id": item_id,
        "name": name,
        "count": count,
        "category": "MATERIAL",
        "recognized_at": STAMP,
    }
    fields.update(overrides)
    return fields


def test_new_row_defaults_match_the_wire_shape(retention_session_factory) -> None:
    async def scenario() -> None:
        async with retention_session_factory() as session:
            repo = AccountOperatorRepository(session)
            row = repo.new_row("char_002_amiya")
            assert (row.char_id, row.name, row.rarity, row.profession) == (
                "char_002_amiya",
                None,
                None,
                None,
            )
            assert (row.own, row.elite, row.level, row.potential) == (False, 0, 0, 0)
            assert row.skill_level == 1
            # 专精是三个 0 槽而不是空映射：页面与编辑校验都按它读。
            assert row.masteries == {"1": 0, "2": 0, "3": 0}
            assert row.modules == {}
            assert row.recognized_values is None
            assert row.field_sources == {}
            assert row.recognized_at is None
            assert isinstance(row.updated_at, datetime)

            other = repo.new_row("char_010_chen", name="陈", rarity=6, profession="近卫", own=True)
            assert (other.name, other.rarity, other.profession, other.own) == (
                "陈",
                6,
                "近卫",
                True,
            )

    asyncio.run(scenario())


def test_operator_repository_reads_filters_and_upserts(retention_session_factory) -> None:
    async def scenario() -> None:
        async with retention_session_factory() as session:
            repo = AccountOperatorRepository(session)
            written = await repo.upsert_many(
                [
                    _operator("char_002_amiya", "阿米娅", rarity=5, own=True, elite=2, level=80),
                    _operator("char_123_frost", "霜星", rarity=6, own=False, profession="术师"),
                    _operator(
                        "char_010_chen", "陈", rarity=6, own=True, elite=2, level=90,
                        profession="近卫", recognized_at=STAMP,
                    ),
                    _operator("char_009_12f", "12F", rarity=2, own=True, profession="术师"),
                ]
            )
            assert written == 4
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountOperatorRepository(session)
            # 稀有度降序 → 等级降序 → char_id。
            assert [row.char_id for row in await repo.list()] == [
                "char_010_chen",
                "char_123_frost",
                "char_002_amiya",
                "char_009_12f",
            ]
            assert [row.char_id for row in await repo.list(owned_only=True)] == [
                "char_010_chen",
                "char_002_amiya",
                "char_009_12f",
            ]
            # owned_only=None 是「不过滤」（工具层的 owned_only=false 走 None）。
            assert len(await repo.list(owned_only=None)) == 4
            assert [row.char_id for row in await repo.list(owned_only=False)] == [
                "char_123_frost"
            ]
            assert [row.char_id for row in await repo.list(rarity_min=6)] == [
                "char_010_chen",
                "char_123_frost",
            ]
            assert [row.char_id for row in await repo.list(elite_min=2, owned_only=True)] == [
                "char_010_chen",
                "char_002_amiya",
            ]
            assert [row.char_id for row in await repo.list(profession="术师")] == [
                "char_123_frost",
                "char_009_12f",
            ]
            assert [row.char_id for row in await repo.list(query="阿米")] == [
                "char_002_amiya"
            ]
            assert [row.char_id for row in await repo.list(query="  ")] == [
                "char_010_chen",
                "char_123_frost",
                "char_002_amiya",
                "char_009_12f",
            ]
            assert [row.char_id for row in await repo.list(limit=2, offset=1)] == [
                "char_123_frost",
                "char_002_amiya",
            ]
            assert await repo.count() == 4
            assert await repo.count(owned_only=True) == 3
            assert await repo.count(query="12F") == 1
            assert await repo.last_recognized_at() == STAMP

            first = await repo.get("char_002_amiya")
            assert first is not None and first.level == 80
            assert await repo.get("char_missing") is None

            # upsert：同 char_id 再写一次是整行覆盖，不新增行。
            await repo.upsert(_operator("char_002_amiya", "阿米娅", rarity=5, own=True, level=85))
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountOperatorRepository(session)
            assert await repo.count() == 4
            updated = await repo.get("char_002_amiya")
            assert updated is not None
            assert (updated.level, updated.elite, updated.name) == (85, 0, "阿米娅")

    asyncio.run(scenario())


def test_operator_repository_last_recognized_at_is_none_when_never_recognized(
    retention_session_factory,
) -> None:
    async def scenario() -> None:
        async with retention_session_factory() as session:
            repo = AccountOperatorRepository(session)
            assert await repo.last_recognized_at() is None
            await repo.upsert(_operator("char_002_amiya", "阿米娅"))
            await session.commit()
        async with retention_session_factory() as session:
            # 手动补录（没有 recognized_at）不算「识别过」。
            assert await AccountOperatorRepository(session).last_recognized_at() is None

    asyncio.run(scenario())


def test_depot_repository_replaces_all_and_filters(retention_session_factory) -> None:
    async def scenario() -> None:
        async with retention_session_factory() as session:
            repo = AccountDepotItemRepository(session)
            assert await repo.count() == 0
            written = await repo.replace_all(
                [
                    _depot_row("30012", "固源岩", 120),
                    _depot_row("30011", "源岩", 8),
                    _depot_row("4001", "龙门币", 15, category="NORMAL"),
                    _depot_row("99999", "未知物品", 3, category=None),
                ]
            )
            assert written == 4
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountDepotItemRepository(session)
            # 分类 → item_id；分类为 NULL 的排最前（SQLite 默认）。
            assert [row.item_id for row in await repo.list()] == [
                "99999",
                "30011",
                "30012",
                "4001",
            ]
            assert [row.item_id for row in await repo.list(category="MATERIAL")] == [
                "30011",
                "30012",
            ]
            assert [row.item_id for row in await repo.list(query="固源")] == ["30012"]
            # 映射不到的物品 name 就是 item_id，按 id 也能搜到。
            assert [row.item_id for row in await repo.list(query="99999")] == ["99999"]
            assert await repo.count() == 4
            assert await repo.count(category="NORMAL") == 1

            # 整表替换：旧行不残留（数量归零的物品从表里消失）；同 id 取最后一条。
            replaced = await repo.replace_all(
                [
                    {"item_id": "30013", "name": "固源岩组", "count": 1, "recognized_at": LATER},
                    {"item_id": "30013", "name": "固源岩组", "count": 3, "recognized_at": LATER},
                    {"item_id": "", "name": "丢弃", "count": 9, "recognized_at": LATER},
                ]
            )
            assert replaced == 1
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountDepotItemRepository(session)
            rows = await repo.list()
            assert [(row.item_id, row.count, row.category, row.recognized_at) for row in rows] == [
                ("30013", 3, None, LATER)
            ]

            # 空列表是合法的整表替换：识别到空仓库＝清空。
            assert await repo.replace_all([]) == 0
            await session.commit()

        async with retention_session_factory() as session:
            assert await AccountDepotItemRepository(session).count() == 0

    asyncio.run(scenario())


def test_depot_repository_lists_categories_of_the_snapshot(
    retention_session_factory,
) -> None:
    """分类清单取整份快照的 distinct 分类：按名字排序，NULL 与空白不出现。"""

    async def scenario() -> None:
        async with retention_session_factory() as session:
            repo = AccountDepotItemRepository(session)
            # 从未识别过时是空清单，页面据此显示「全部分类」一项。
            assert await repo.categories() == []
            assert (
                await repo.replace_all(
                    [
                        _depot_row("30012", "固源岩", 120, category="MATERIAL"),
                        _depot_row("30011", "源岩", 8, category="MATERIAL"),
                        _depot_row("4001", "龙门币", 15, category="NORMAL"),
                        _depot_row("30061", "无分类物品", 1, category=None),
                        _depot_row("30062", "空白分类", 1, category="   "),
                    ]
                )
                == 5
            )
            await session.commit()

        async with retention_session_factory() as session:
            assert await AccountDepotItemRepository(session).categories() == [
                "MATERIAL",
                "NORMAL",
            ]

    asyncio.run(scenario())


def test_recognition_repository_records_and_reads_the_latest(
    retention_session_factory,
) -> None:
    async def scenario() -> None:
        async with retention_session_factory() as session:
            pipeline = Pipeline(
                id="pipeline-1",
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
                status=PipelineStatus.COMPLETED,
                task_count=1,
            )
            task = Task(
                id="task-1",
                pipeline_id=pipeline.id,
                order_index=0,
                type_name="OperBox",
                task_name="干员识别",
                params={},
            )
            await PipelineRepository(session).create(pipeline, [task])

            repo = AccountRecognitionRepository(session)
            assert await repo.latest() is None
            assert await repo.last_at(RECOGNITION_KIND_OPERBOX) is None
            await repo.record(
                kind=RECOGNITION_KIND_OPERBOX,
                item_count=312,
                recognized_at=STAMP,
                client_type="Official",
            )
            await repo.record(
                kind=RECOGNITION_KIND_DEPOT,
                item_count=48,
                recognized_at=LATER,
            )
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountRecognitionRepository(session)
            operbox = await repo.latest(RECOGNITION_KIND_OPERBOX)
            assert operbox is not None
            assert (operbox.item_count, operbox.client_type) == (312, "Official")
            assert await repo.last_at(RECOGNITION_KIND_OPERBOX) == STAMP
            assert await repo.last_at(RECOGNITION_KIND_DEPOT) == LATER
            # 不带 kind 的 latest 取全局最近一条。
            assert (await repo.latest()).kind == RECOGNITION_KIND_DEPOT

            # 同 kind 同识别时刻：只补上下文，不插第二条（capture 层随后带
            # 流水线 / 任务 / 渠道再调一次）。
            enriched = await repo.record(
                kind=RECOGNITION_KIND_OPERBOX,
                item_count=312,
                recognized_at=STAMP,
                pipeline_id="pipeline-1",
                task_id="task-1",
                client_type="Bilibili",
            )
            await session.commit()
            assert enriched.pipeline_id == "pipeline-1"
            assert enriched.task_id == "task-1"
            assert enriched.client_type == "Bilibili"

        async with retention_session_factory() as session:
            repo = AccountRecognitionRepository(session)
            # 补上下文没有插第二条：换一个识别时刻才是新的一条。
            latest = await repo.latest(RECOGNITION_KIND_OPERBOX)
            assert latest is not None and latest.pipeline_id == "pipeline-1"
            await repo.record(
                kind=RECOGNITION_KIND_OPERBOX,
                item_count=320,
                recognized_at=LATER,
            )
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountRecognitionRepository(session)
            newest = await repo.latest(RECOGNITION_KIND_OPERBOX)
            assert newest is not None
            assert (newest.item_count, newest.recognized_at) == (320, LATER)
            assert newest.pipeline_id is None

    asyncio.run(scenario())


def test_diff_repository_lists_and_dismisses(retention_session_factory) -> None:
    async def scenario() -> None:
        async with retention_session_factory() as session:
            session.add(AccountOperator(char_id="char_002_amiya", name="阿米娅", rarity=5))
            repo = AccountOperatorDiffRepository(session)
            await repo.create(
                char_id="char_002_amiya", field="level", manual_value=90,
                recognized_value=80, created_at=STAMP,
            )
            await repo.create(
                char_id="char_002_amiya", field="elite", manual_value=2,
                recognized_value=1, created_at=LATER,
            )
            await repo.create(
                char_id="char_010_chen", field="potential", manual_value=6,
                recognized_value=5, created_at=LATER,
            )
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountOperatorDiffRepository(session)
            assert await repo.count_pending() == 3
            assert await repo.count_pending(char_id="char_002_amiya") == 2
            listed = await repo.list()
            assert all(isinstance(row, OperatorDiffRow) for row in listed)
            # 时间倒序：最新的在前面。
            assert [(row.char_id, row.field) for row in listed] == [
                ("char_010_chen", "potential"),
                ("char_002_amiya", "elite"),
                ("char_002_amiya", "level"),
            ]
            # 干员名一起带出来（页面不必再查一次）；查不到的干员为 None。
            assert listed[0].name is None
            assert listed[-1].name == "阿米娅"
            assert [row.field for row in await repo.list(char_id="char_002_amiya")] == [
                "elite",
                "level",
            ]
            assert len(await repo.list(limit=1)) == 1

            first_id = listed[-1].id
            assert await repo.dismiss([first_id], now=LATER) == 1
            # 幂等：已经标记过的不再计数。
            assert await repo.dismiss([first_id], now=LATER) == 0
            assert await repo.dismiss([], now=LATER) == 0
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountOperatorDiffRepository(session)
            assert await repo.count_pending() == 2
            assert [row.field for row in await repo.list()] == ["potential", "elite"]
            assert len(await repo.list(include_dismissed=True)) == 3
            # ids=None = 全部未处理的一起「知道了」。
            assert await repo.dismiss(None, now=LATER) == 2
            await session.commit()

        async with retention_session_factory() as session:
            repo = AccountOperatorDiffRepository(session)
            assert await repo.count_pending() == 0
            dismissed = await repo.list(include_dismissed=True)
            assert all(row.dismissed_at == LATER for row in dismissed)

    asyncio.run(scenario())


def test_repositories_never_commit_on_their_own(retention_session_factory) -> None:
    async def scenario() -> None:
        async with retention_session_factory() as session:
            await AccountOperatorRepository(session).upsert(
                _operator("char_002_amiya", "阿米娅")
            )
            await AccountDepotItemRepository(session).replace_all(
                [_depot_row("30012", "固源岩", 1)]
            )
            await AccountRecognitionRepository(session).record(
                kind=RECOGNITION_KIND_OPERBOX, item_count=1, recognized_at=STAMP
            )
            await AccountOperatorDiffRepository(session).create(
                char_id="char_002_amiya", field="level",
                manual_value=90, recognized_value=80, created_at=STAMP,
            )
            await session.rollback()

        async with retention_session_factory() as session:
            assert await AccountOperatorRepository(session).count() == 0
            assert await AccountDepotItemRepository(session).count() == 0
            assert await AccountRecognitionRepository(session).latest() is None
            assert await AccountOperatorDiffRepository(session).count_pending() == 0

    asyncio.run(scenario())
