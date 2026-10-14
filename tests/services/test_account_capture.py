"""M27 T2/T3：账号识别回调的采集（规格 §7.2 T2、§7.3）。

**M47 起只剩内核仓库识别这一条通道**（docs/13 §8.70）：干员数据的唯一来源是森空岛
练度同步（``SklandSyncService`` → ``OperatorBoxService.apply_skland_sync``），内核
``OperBoxInfo`` 的解析与落库路径连同 ``OperBox`` 任务类型一起下线——本文件保留一条
回归用例把「不再认它」钉住。

夹具是**假回调**（不打真机），物品名映射注入假目录（另有一条用例读真
``item_index.json``）：

- ``DepotInfo``：``data`` 是 JSON 字符串 ``{"物品ID": 数量}``，整表替换仓库数量，
  映射不到的物品保留 id；``done=false`` 是过程回调（忽略）、``done=true`` 才收尾写入；
- 坏 JSON / 结构不符只降级（保留旧数据、不写识别记录），采集异常不得冒泡到流水线。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from maa_api.core.enums import Message
from maa_api.db.models import AccountOperator, Pipeline, Task
from maa_api.db.repositories.account import (
    RECOGNITION_KIND_DEPOT,
    AccountDepotItemRepository,
    AccountOperatorRepository,
    AccountRecognitionRepository,
)
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority
from maa_api.services.account.capture import (
    AccountCaptureService,
    ItemMeta,
    load_item_catalog,
    load_operator_catalog,
    parse_depot_data,
)
from maa_api.services.account.depot import DepotService
from maa_api.settings import Settings

RECOGNIZED_AT = datetime(2026, 10, 2, 8, 30, 0)
LATER = datetime(2026, 10, 3, 9, 0, 0)

#: 假的物品目录（真目录读盘，见文件末尾那两条用例）。
ITEM_CATALOG = {
    "30012": ItemMeta(name="固源岩", category="MATERIAL"),
    "30011": ItemMeta(name="源岩", category="MATERIAL"),
    "4001": ItemMeta(name="龙门币", category="NORMAL"),
}


def _operbox() -> dict:
    """内核干员识别回调的信封：M47 起采集层只回 ``skipped="unsupported"``。"""
    return {
        "what": "OperBoxInfo",
        "details": {
            "done": True,
            "all_opers": [
                {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": True}
            ],
            "own_opers": [
                {
                    "id": "char_002_amiya",
                    "name": "阿米娅",
                    "rarity": 5,
                    "own": True,
                    "elite": 2,
                    "level": 80,
                    "potential": 6,
                }
            ],
        },
    }


def _depot(data, done: bool = True) -> dict:
    return {"what": "DepotInfo", "details": {"data": data, "done": done}}


def _depot_service_without_guard(factory) -> DepotService:
    """真 ``DepotService``，只把 M44 的下限护栏关掉（``depot_min_items=0``）。"""
    settings = Settings()
    settings.agent.depot_min_items = 0
    return DepotService(factory, settings_provider=lambda: settings)


def _service(factory, **kwargs) -> AccountCaptureService:
    kwargs.setdefault("item_catalog", ITEM_CATALOG)
    # M44 起仓库整表替换默认有下限护栏（``agent.depot_min_items`` = 20）：本文件测的是
    # 回调链路与整表替换语义、只塞 2 件小表，所以在装配处显式关掉护栏（护栏本身由
    # ``test_account_depot.py::test_min_items_guard_blocks_a_partial_recognition`` 覆盖）。
    kwargs.setdefault("depot_service", _depot_service_without_guard(factory))
    return AccountCaptureService(factory, **kwargs)


async def _capture(factory, callback, *, created_at: datetime = RECOGNIZED_AT, **kwargs):
    return await _service(factory).handle_callback(
        callback,
        message=Message.SubTaskExtraInfo,
        created_at=created_at,
        **kwargs,
    )


def test_operbox_info_is_unsupported_and_writes_nothing(
    retention_session_factory, caplog
) -> None:
    """M47：内核 ``OperBoxInfo`` 不再有落库路径（docs/13 §8.70）。

    载荷本身是「能落库的形状」（``done=true`` + ``all_opers`` / ``own_opers``），
    唯一挡住它的是采集层的 ``what`` 白名单——所以这条断言不会因为载荷变瘦而空转。
    """
    import logging

    async def scenario() -> None:
        async with retention_session_factory() as session:
            pipeline = Pipeline(
                id="pipeline-operbox",
                source=PipelineSource.MANUAL,
                priority=Priority.MANUAL,
                status=PipelineStatus.COMPLETED,
                task_count=1,
            )
            task = Task(
                id="task-operbox",
                pipeline_id=pipeline.id,
                order_index=0,
                type_name="OperBox",
                task_name="干员识别",
                params={},
            )
            await PipelineRepository(session).create(pipeline, [task])
            await session.commit()

        with caplog.at_level(logging.WARNING):
            result = await _capture(
                retention_session_factory,
                _operbox(),
                pipeline_id="pipeline-operbox",
                task_id="task-operbox",
                client_type="Official",
            )

        assert (result.written, result.skipped, result.kind) == (
            False,
            "unsupported",
            None,
        )
        assert result.item_count == 0

        # 干员表、识别记录、差异表都没被碰过；warning 也没记（不是错误，是已知的旁路）。
        async with retention_session_factory() as session:
            assert await AccountOperatorRepository(session).count() == 0
            assert await AccountRecognitionRepository(session).latest() is None

    asyncio.run(scenario())


def test_depot_done_true_replaces_the_whole_table(retention_session_factory) -> None:
    """``done=true`` 的整表替换语义（含「少了的那件必须消失」）。

    只有 2 件，低于 M44 的默认下限护栏（20）；这里显式关掉护栏测的是替换语义，
    不是「半截识别不许覆盖」——后者见 ``test_account_depot.py`` 的护栏用例。
    """

    async def scenario() -> None:
        async with retention_session_factory() as session:
            session.add(AccountOperator(char_id="char_002_amiya", name="阿米娅"))
            await session.commit()

        first = await _capture(
            retention_session_factory,
            _depot('{"30012": 120, "4001": 15}'),
            client_type="Official",
        )
        assert (first.written, first.kind) == (True, RECOGNITION_KIND_DEPOT)
        assert first.item_count == 2

        # 第二次识别少了 4001（数量归零的物品不出现在 data 里）：整表替换，旧行不残留。
        second = await _capture(
            retention_session_factory, _depot('{"30012": 60, "99999": 3}'), created_at=LATER
        )
        assert (second.written, second.item_count) == (True, 2)

        async with retention_session_factory() as session:
            rows = await AccountDepotItemRepository(session).list()
            # 顺序：分类 → item_id；分类为 NULL 的（映射不到的）按 SQLite 默认排最前。
            assert [(row.item_id, row.name, row.count, row.category) for row in rows] == [
                # 映射不到的物品保留 id 当名字，分类留空而不是编一个。
                ("99999", "99999", 3, None),
                ("30012", "固源岩", 60, "MATERIAL"),
            ]
            assert {row.recognized_at for row in rows} == {LATER}

            latest = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_DEPOT
            )
            assert latest is not None
            assert latest.item_count == 2
            assert latest.client_type is None  # 第二次没给渠道
            assert latest.recognized_at == LATER
            # 别的表没被仓库识别碰到。
            assert await AccountOperatorRepository(session).count() == 1

    asyncio.run(scenario())


def test_depot_bad_json_is_degraded_without_wiping_the_table(
    retention_session_factory,
) -> None:
    """坏载荷降级：不落库、不覆盖（同样显式关掉 M44 下限护栏，见 ``_service``）。"""

    async def scenario() -> None:
        await _capture(retention_session_factory, _depot('{"30012": 120}'))

        result = await _capture(retention_session_factory, _depot("{不是 JSON"))
        assert (result.written, result.skipped) == (False, "bad_payload")
        assert result.kind == RECOGNITION_KIND_DEPOT

        # 坏载荷既不落库也不覆盖：旧数据与旧识别时间都留着。
        async with retention_session_factory() as session:
            rows = await AccountDepotItemRepository(session).list()
            assert [(row.item_id, row.count) for row in rows] == [("30012", 120)]
            recognition = await AccountRecognitionRepository(session).last_at(
                RECOGNITION_KIND_DEPOT
            )
            assert recognition == RECOGNIZED_AT

        # data 不是字符串、不是对象、一个有效条目都没有：同样按坏载荷处理。
        for payload in ("[1, 2]", '{"30012": "很多"}', "{}"):
            degraded = await _capture(retention_session_factory, _depot(payload))
            assert degraded.skipped == "bad_payload", payload

        async with retention_session_factory() as session:
            assert len(await AccountDepotItemRepository(session).list()) == 1

    asyncio.run(scenario())


def test_depot_write_path_writes_no_source_or_detail(retention_session_factory) -> None:
    """仓库写库只有内核那一趟：来源表 ``sources`` 与识别记录 ``detail`` 两列都不写。

    M45 的自识别已于 2026-10-13 关停、2026-10-14 连实现一起删除（`docs/13` §8.68），
    所以这条形状现在是**唯一**的写库路径（自识别关停前那两条「关掉开关」「装配但失败」
    的对照用例已随实现删除）。判据落在两列上：``sources`` 列留 NULL、``detail`` 留空，
    与 0021 之前的旧行同形。
    """

    async def scenario() -> None:
        result = await _capture(retention_session_factory, _depot('{"30012": 120, "4001": 15}'))

        assert (result.written, result.item_count) == (True, 2)
        async with retention_session_factory() as session:
            rows = await AccountDepotItemRepository(session).list()
            assert [(row.item_id, row.count) for row in rows] == [
                ("30012", 120),
                ("4001", 15),
            ]
            assert {row.sources for row in rows} == {None}
            latest = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_DEPOT
            )
            assert latest is not None
            assert latest.detail is None
            assert latest.item_count == 2

    asyncio.run(scenario())


def test_progress_callbacks_are_ignored(retention_session_factory) -> None:
    async def scenario() -> None:
        for callback in (
            _depot('{"30012": 3}', done=False),
            _depot('{"30012": 3, "4001": 1}', done=False),
            _depot('{"30012": 3}', done=False),
        ):
            result = await _capture(retention_session_factory, callback)
            assert result.written is False
            assert result.skipped == "incomplete"

        async with retention_session_factory() as session:
            assert await AccountOperatorRepository(session).count() == 0
            assert await AccountDepotItemRepository(session).count() == 0
            assert await AccountRecognitionRepository(session).latest() is None

    asyncio.run(scenario())


def test_only_the_final_callback_of_a_sequence_writes(retention_session_factory) -> None:
    """真实回调是一条序列：多次 ``done=false`` + 一次 ``done=true``，只有最后那次落库。"""

    async def scenario() -> None:
        sequence = [
            _depot(None, done=False),
            _depot('{"30012": 1}', done=False),
            _depot('{"30012": 2, "4001": 1}', done=False),
            _depot('{"30012": 120, "4001": 15}', done=True),
        ]
        results = [
            await _capture(retention_session_factory, callback, created_at=RECOGNIZED_AT)
            for callback in sequence
        ]
        assert [item.skipped for item in results] == [
            "incomplete",
            "incomplete",
            "incomplete",
            None,
        ]

        async with retention_session_factory() as session:
            rows = await AccountDepotItemRepository(session).list()
            # 过程回调里的数量没有落库，落库的是收尾那次的值。
            assert sorted((row.item_id, row.count) for row in rows) == [
                ("30012", 120),
                ("4001", 15),
            ]
            recognition = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_DEPOT
            )
            assert recognition is not None and recognition.item_count == 2

    asyncio.run(scenario())


def test_capture_failures_never_reach_the_callback_pipeline() -> None:
    async def scenario() -> None:
        def broken_factory():
            raise RuntimeError("database is gone")

        service = _service(broken_factory)
        result = await service.handle_callback(
            _depot('{"30012": 120}'),
            message=Message.SubTaskExtraInfo,
            created_at=RECOGNIZED_AT,
        )
        assert result.written is False
        assert result.skipped == "error"
        assert result.kind == RECOGNITION_KIND_DEPOT
        assert "database is gone" in (result.error or "")

        # 回调不是 SubTaskExtraInfo、what 不认识（含已下线的 OperBoxInfo）、载荷结构不对：
        # 安静跳过，不算错误。
        for message, callback in (
            (Message.SubTaskCompleted, _depot('{"30012": 120}')),
            (Message.SubTaskExtraInfo, {"what": "StageDrops", "details": {"done": True}}),
            (Message.SubTaskExtraInfo, _operbox()),
            (Message.SubTaskExtraInfo, {"what": "DepotInfo"}),
            (Message.SubTaskExtraInfo, {"what": "DepotInfo", "details": {"done": True}}),
            (Message.SubTaskExtraInfo, "not a mapping"),
            (Message.SubTaskExtraInfo, _depot(None)),
        ):
            skipped = await _service(broken_factory).handle_callback(
                callback, message=message, created_at=RECOGNIZED_AT
            )
            assert skipped.written is False
            assert skipped.skipped != "error", callback

    asyncio.run(scenario())


def test_parse_depot_data_is_lenient_about_bad_entries() -> None:
    """``parse_depot_data`` 跳过坏条目；一个有效条目都没有时返回 ``None``。"""
    assert parse_depot_data({"data": '{"30012": 5, "4001": "nope"}'}) == {"30012": 5}
    assert parse_depot_data({"data": '{"30012": "7"}'}) == {"30012": 7}
    assert parse_depot_data({"data": "not json"}) is None
    assert parse_depot_data({"data": "{}"}) is None
    assert parse_depot_data({"data": '{"30012": -1}'}) is None
    assert parse_depot_data({"data": "[]"}) is None
    assert parse_depot_data({"data": "[1, 2]"}) is None
    assert parse_depot_data({}) is None


def test_operator_task_types_are_gone_from_the_capture_surface() -> None:
    """M47：采集层不再暴露干员识别的解析入口（``OperBox`` 整条通道下线）。"""
    from maa_api.services.account import capture

    assert not hasattr(capture, "parse_operbox")
    assert not hasattr(capture, "OPERBOX_WHAT")
    assert not hasattr(capture, "OperBoxSnapshot")
    assert not hasattr(capture, "RECOGNITION_KIND_OPERBOX")


def test_local_item_index_maps_real_materials() -> None:
    catalog = load_item_catalog()
    if not catalog:
        pytest.skip("本机没有 item_index.json")
    assert catalog["30012"].name == "固源岩"
    assert catalog["30012"].category == "MATERIAL"


def test_local_operator_catalog_maps_real_professions() -> None:
    """``load_operator_catalog`` 仍在（森空岛映射与游戏资料侧共用），照旧验证。"""
    catalog = load_operator_catalog()
    if not catalog:
        pytest.skip("本机没有 battle_data.json")
    amiya = catalog["char_002_amiya"]
    assert amiya.name == "阿米娅"
    assert amiya.rarity == 5
    # 本机存的是英文枚举（CASTER），表里统一存中文。
    assert amiya.profession == "术师"
