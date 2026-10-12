"""M27 T2/T3：账号识别回调的采集与合并规则（规格 §7.2 T2、§7.3）。

夹具是**假回调**（不打真机），物品名与职业映射注入假目录（另有一条用例读真
``item_index.json``）：

- ``OperBoxInfo``：``all_opers[]`` 给「是否拥有」，``own_opers[]`` 给精英化 / 等级 /
  潜能，``done=false`` 是过程回调（忽略）、``done=true`` 才收尾写入；
- ``DepotInfo``：``data`` 是 JSON 字符串 ``{"物品ID": 数量}``，整表替换仓库数量，
  映射不到的物品保留 id；
- 坏 JSON / 结构不符只降级（保留旧数据、不写识别记录），采集异常不得冒泡到流水线。

合并规则三条（规格 §7.3，本文件逐个用例）：识别结果覆盖 own / elite / level /
potential；技能等级、专精、模组识别不动；被覆盖的字段在上次识别之后被手动改过且
值不同时写一条 ``account_operator_diff``。合并实现在
:class:`~maa_api.services.account.box.OperatorBoxService`，这里验证的是「回调进来之后
的行为」，即采集层与合并层的接缝。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pytest

from maa_api.core.enums import Message
from maa_api.db.models import AccountOperator, Pipeline, Task
from maa_api.db.repositories.account import (
    RECOGNITION_KIND_DEPOT,
    RECOGNITION_KIND_OPERBOX,
    AccountDepotItemRepository,
    AccountOperatorDiffRepository,
    AccountOperatorRepository,
    AccountRecognitionRepository,
)
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority
from maa_api.services.account.capture import (
    AccountCaptureService,
    ItemMeta,
    OperatorMeta,
    load_item_catalog,
    load_operator_catalog,
    parse_depot_data,
    parse_operbox,
)
from maa_api.services.account.depot import DepotService
from maa_api.settings import Settings

RECOGNIZED_AT = datetime(2026, 10, 2, 8, 30, 0)
LATER = datetime(2026, 10, 3, 9, 0, 0)

#: 假的物品与干员目录（真目录读盘，见文件末尾那两条用例）。
ITEM_CATALOG = {
    "30012": ItemMeta(name="固源岩", category="MATERIAL"),
    "30011": ItemMeta(name="源岩", category="MATERIAL"),
    "4001": ItemMeta(name="龙门币", category="NORMAL"),
}
OPERATOR_CATALOG = {
    "char_002_amiya": OperatorMeta(name="阿米娅", rarity=5, profession="术师"),
    "char_103_angel": OperatorMeta(name="能天使", rarity=6, profession="狙击"),
    "char_010_chen": OperatorMeta(name="陈", rarity=6, profession="近卫"),
}

#: 回调里给的阿米娅：精二 80 级、满潜。
AMIYA = {
    "id": "char_002_amiya",
    "name": "阿米娅",
    "rarity": 5,
    "own": True,
    "elite": 2,
    "level": 80,
    "potential": 6,
}


def _operbox(done: bool = True, *, all_opers=None, own_opers=None) -> dict:
    details: dict = {"done": done}
    if all_opers is not None:
        details["all_opers"] = all_opers
    if own_opers is not None:
        details["own_opers"] = own_opers
    return {"what": "OperBoxInfo", "details": details}


def _depot(data, done: bool = True) -> dict:
    return {"what": "DepotInfo", "details": {"data": data, "done": done}}


def _depot_service_without_guard(factory) -> DepotService:
    """真 ``DepotService``，只把 M44 的下限护栏关掉（``depot_min_items=0``）。"""
    settings = Settings()
    settings.agent.depot_min_items = 0
    return DepotService(factory, settings_provider=lambda: settings)


def _service(factory, **kwargs) -> AccountCaptureService:
    kwargs.setdefault("item_catalog", ITEM_CATALOG)
    kwargs.setdefault("operator_catalog", OPERATOR_CATALOG)
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


def test_operbox_done_true_writes_operators_and_a_recognition_record(
    retention_session_factory,
) -> None:
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

        result = await _capture(
            retention_session_factory,
            _operbox(
                all_opers=[
                    {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": True},
                    {"id": "char_103_angel", "name": "能天使", "rarity": 6, "own": False},
                ],
                own_opers=[AMIYA],
            ),
            pipeline_id="pipeline-operbox",
            task_id="task-operbox",
            client_type="Official",
        )

        assert result.written is True
        assert result.kind == RECOGNITION_KIND_OPERBOX
        assert (result.item_count, result.diff_count, result.skipped) == (1, 0, None)

        async with retention_session_factory() as session:
            repo = AccountOperatorRepository(session)
            amiya = await repo.get("char_002_amiya")
            assert amiya is not None
            assert (amiya.name, amiya.rarity, amiya.own) == ("阿米娅", 5, True)
            assert (amiya.elite, amiya.level, amiya.potential) == (2, 80, 6)
            # 职业只有本机资料有，识别回调给不出——由采集层补上。
            assert amiya.profession == "术师"
            assert amiya.recognized_values == {
                "own": True,
                "elite": 2,
                "level": 80,
                "potential": 6,
            }
            assert set(amiya.field_sources.values()) == {"recognition"}
            assert amiya.recognized_at == RECOGNIZED_AT

            angel = await repo.get("char_103_angel")
            assert angel is not None
            assert angel.own is False
            # all_opers 里的未拥有干员也要有名字与职业，页面按名字筛选才有意义。
            assert (angel.name, angel.rarity, angel.profession) == ("能天使", 6, "狙击")
            # 这次只认得「未拥有」：练度三列与技能相关列都是 NULL＝未识别（M35）。
            assert angel.recognized_values == {"own": False}
            assert (angel.elite, angel.level, angel.potential) == (None, None, None)
            assert angel.skill_level is None
            assert angel.masteries is None
            assert angel.modules is None

            recognition = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_OPERBOX
            )
            assert recognition is not None
            assert (recognition.item_count, recognition.client_type) == (1, "Official")
            assert (recognition.pipeline_id, recognition.task_id) == (
                "pipeline-operbox",
                "task-operbox",
            )
            assert recognition.recognized_at == RECOGNIZED_AT

            assert await AccountOperatorDiffRepository(session).count_pending() == 0

    asyncio.run(scenario())


def test_operbox_empty_recognition_is_skipped_and_keeps_old_data(
    retention_session_factory, caplog
) -> None:
    """M33 护栏 G1（规格 §4.3）：游戏没启动时内核照样发 ``done=true``。

    实测载荷（16:26–16:29 那四次重试）：``all_opers`` 里一个 ``own=true`` 都没有。
    这种载荷落库会把整表标成未拥有，采集层必须在**写库之前**整条跳过。
    """

    async def scenario() -> None:
        await _capture(
            retention_session_factory,
            _operbox(
                all_opers=[
                    {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": True}
                ],
                own_opers=[AMIYA],
            ),
        )

        empty = [
            {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": False},
            {"id": "char_103_angel", "name": "能天使", "rarity": 6, "own": False},
        ]
        with caplog.at_level(logging.WARNING):
            result = await _capture(
                retention_session_factory, _operbox(all_opers=empty, own_opers=[])
            )
        assert result.kind == RECOGNITION_KIND_OPERBOX
        assert (result.written, result.skipped) == (False, "no_owned_operators")
        assert "空识别保护" in caplog.text

        async with retention_session_factory() as session:
            repo = AccountOperatorRepository(session)
            amiya = await repo.get("char_002_amiya")
            assert amiya is not None and amiya.own is True
            # 跳过发生在落库之前：这次载荷里的新 id 不会被建成「未拥有」行。
            assert await repo.get("char_103_angel") is None
            # 识别记录也没有新增，latest 仍是第一次那条。
            latest = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_OPERBOX
            )
            assert latest is not None and latest.item_count == 1

    asyncio.run(scenario())


def test_operbox_shrink_protection_keeps_the_missing_operators(
    retention_session_factory, caplog
) -> None:
    """M33 护栏 G2（规格 §4.3，裁决 A「识别只加不减」）。

    载荷按 17:20 那次的形状：``own_opers`` 少一名库里已拥有的干员（380 vs 383），
    采集层保留它的已拥有状态、练度沿用行里既有值，并记一条 warning 提示。
    """

    async def scenario() -> None:
        await _capture(
            retention_session_factory,
            _operbox(
                all_opers=[
                    {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": True},
                    {"id": "char_103_angel", "name": "能天使", "rarity": 6, "own": True},
                ],
                own_opers=[
                    AMIYA,
                    {
                        "id": "char_103_angel",
                        "name": "能天使",
                        "rarity": 6,
                        "own": True,
                        "elite": 2,
                        "level": 90,
                        "potential": 6,
                    },
                ],
            ),
        )

        with caplog.at_level(logging.WARNING):
            result = await _capture(
                retention_session_factory,
                _operbox(
                    all_opers=[
                        {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": True},
                        {"id": "char_103_angel", "name": "能天使", "rarity": 6, "own": False},
                    ],
                    own_opers=[AMIYA],
                ),
            )
        assert result.written is True
        assert result.kept_count == 1
        # item_count 记的是「本次识别到的已拥有数」（阿米娅），与收缩保护无关。
        assert result.item_count == 1
        assert "识别只加不减" in caplog.text and "能天使" in caplog.text

        async with retention_session_factory() as session:
            angel = await AccountOperatorRepository(session).get("char_103_angel")
            assert angel is not None and angel.own is True
            # 这次没给练度详情：沿用行里既有的值，不写回默认值。
            assert (angel.elite, angel.level, angel.potential) == (2, 90, 6)

    asyncio.run(scenario())


def test_progress_callbacks_are_ignored(retention_session_factory) -> None:
    async def scenario() -> None:
        for callback in (
            _operbox(False, all_opers=[AMIYA], own_opers=[AMIYA]),
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
            _operbox(False, all_opers=[AMIYA]),
            _operbox(False, all_opers=[AMIYA], own_opers=[]),
            _operbox(False, all_opers=[AMIYA], own_opers=[{**AMIYA, "level": 1}]),
            _operbox(True, all_opers=[AMIYA], own_opers=[AMIYA]),
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
            rows = await AccountOperatorRepository(session).list()
            # 过程回调里的 level=1 没有落库，落库的是收尾那次的值。
            assert [(row.char_id, row.level, row.own) for row in rows] == [
                ("char_002_amiya", 80, True)
            ]
            recognitions = await AccountRecognitionRepository(session).latest(
                RECOGNITION_KIND_OPERBOX
            )
            assert recognitions is not None and recognitions.item_count == 1

    asyncio.run(scenario())


def test_recognition_keeps_manual_skill_masteries_and_modules(
    retention_session_factory,
) -> None:
    """保留：技能等级 / 专精 / 模组只能手动或经 Agent 写入，识别不动它们。"""

    async def scenario() -> None:
        async with retention_session_factory() as session:
            session.add(
                AccountOperator(
                    char_id="char_002_amiya",
                    name="阿米娅",
                    rarity=5,
                    profession="术师",
                    own=True,
                    elite=2,
                    level=80,
                    potential=6,
                    skill_level=7,
                    masteries={"1": 3, "2": 1},
                    modules={"X": 2},
                    recognized_values={
                        "own": True,
                        "elite": 2,
                        "level": 80,
                        "potential": 6,
                    },
                    field_sources={
                        "own": "recognition",
                        "elite": "recognition",
                        "level": "recognition",
                        "potential": "recognition",
                        "skill_level": "manual",
                        "masteries": "manual",
                        "modules": "agent",
                    },
                    recognized_at=RECOGNIZED_AT,
                )
            )
            await session.commit()

        result = await _capture(
            retention_session_factory,
            _operbox(
                all_opers=[AMIYA],
                own_opers=[{**AMIYA, "elite": 1, "level": 50, "potential": 3}],
            ),
        )
        assert (result.written, result.diff_count) == (True, 0)

        async with retention_session_factory() as session:
            row = await AccountOperatorRepository(session).get("char_002_amiya")
            assert row is not None
            # 覆盖：识别认得的四个字段按识别值落库，来源改回识别。
            assert (row.elite, row.level, row.potential) == (1, 50, 3)
            assert row.field_sources["level"] == "recognition"
            # 保留：识别范围之外的三个字段原样不动，来源也不动。
            assert (row.skill_level, row.masteries, row.modules) == (
                7,
                {"1": 3, "2": 1},
                {"X": 2},
            )
            assert row.field_sources["skill_level"] == "manual"
            assert row.field_sources["masteries"] == "manual"
            assert row.field_sources["modules"] == "agent"
            assert await AccountOperatorDiffRepository(session).count_pending() == 0

    asyncio.run(scenario())


def test_manual_override_that_differs_from_recognition_writes_a_diff(
    retention_session_factory,
) -> None:
    """差异：上次识别后手动改过、值与识别值不同的字段写一条差异。"""

    async def scenario() -> None:
        async with retention_session_factory() as session:
            session.add(
                AccountOperator(
                    char_id="char_002_amiya",
                    name="阿米娅",
                    rarity=5,
                    own=True,
                    elite=2,
                    level=90,  # 手动改成了 90，上一次识别值是 80
                    potential=6,
                    recognized_values={"own": True, "elite": 2, "level": 80, "potential": 6},
                    field_sources={
                        "own": "recognition",
                        "elite": "recognition",
                        "level": "manual",
                        "potential": "recognition",
                    },
                    recognized_at=RECOGNIZED_AT,
                )
            )
            await session.commit()

        result = await _capture(
            retention_session_factory,
            _operbox(all_opers=[AMIYA], own_opers=[{**AMIYA, "level": 85}]),
        )
        assert (result.written, result.diff_count) == (True, 1)

        async with retention_session_factory() as session:
            row = await AccountOperatorRepository(session).get("char_002_amiya")
            assert row is not None
            assert row.level == 85
            assert row.field_sources["level"] == "recognition"

            diffs = await AccountOperatorDiffRepository(session).list()
            assert len(diffs) == 1
            diff = diffs[0]
            assert (diff.char_id, diff.field) == ("char_002_amiya", "level")
            assert (diff.manual_value, diff.recognized_value) == (90, 85)
            assert diff.created_at == RECOGNIZED_AT
            assert diff.dismissed_at is None
            assert diff.name == "阿米娅"

    asyncio.run(scenario())


def test_no_diff_when_the_manual_value_already_matches_the_recognition(
    retention_session_factory,
) -> None:
    """手动值恰好等于这次识别值：来源变了，但不该打扰用户。"""

    async def scenario() -> None:
        async with retention_session_factory() as session:
            session.add(
                AccountOperator(
                    char_id="char_002_amiya",
                    name="阿米娅",
                    rarity=5,
                    own=True,
                    level=85,
                    recognized_values={"own": True, "level": 80},
                    field_sources={"own": "recognition", "level": "manual"},
                    recognized_at=RECOGNIZED_AT,
                )
            )
            await session.commit()

        result = await _capture(
            retention_session_factory,
            _operbox(all_opers=[AMIYA], own_opers=[{**AMIYA, "level": 85}]),
        )
        assert (result.written, result.diff_count) == (True, 0)

        async with retention_session_factory() as session:
            row = await AccountOperatorRepository(session).get("char_002_amiya")
            assert row is not None and row.level == 85
            assert await AccountOperatorDiffRepository(session).count_pending() == 0

    asyncio.run(scenario())


def test_operbox_without_all_opers_does_not_clear_ownership(
    retention_session_factory,
) -> None:
    """旧版内核的 ``OperBoxInfo`` 没有 ``all_opers``：不在本次结果里的行原样保留。"""

    async def scenario() -> None:
        async with retention_session_factory() as session:
            session.add(
                AccountOperator(
                    char_id="char_010_chen", name="陈", rarity=6, own=True, elite=2, level=90
                )
            )
            await session.commit()

        result = await _capture(
            retention_session_factory,
            _operbox(own_opers=[AMIYA]),
        )
        assert result.written is True

        async with retention_session_factory() as session:
            chen = await AccountOperatorRepository(session).get("char_010_chen")
            assert chen is not None
            assert (chen.own, chen.elite, chen.level) == (True, 2, 90)

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
        assert (first.item_count, first.diff_count) == (2, 0)

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


def test_capture_failures_never_reach_the_callback_pipeline() -> None:
    async def scenario() -> None:
        def broken_factory():
            raise RuntimeError("database is gone")

        service = _service(broken_factory)
        result = await service.handle_callback(
            _operbox(all_opers=[AMIYA], own_opers=[AMIYA]),
            message=Message.SubTaskExtraInfo,
            created_at=RECOGNIZED_AT,
        )
        assert result.written is False
        assert result.skipped == "error"
        assert result.kind == RECOGNITION_KIND_OPERBOX
        assert "database is gone" in (result.error or "")

        # 回调不是 SubTaskExtraInfo、what 不认识、载荷结构不对：安静跳过，不算错误。
        for message, callback in (
            (Message.SubTaskCompleted, _operbox(all_opers=[AMIYA])),
            (Message.SubTaskExtraInfo, {"what": "StageDrops", "details": {"done": True}}),
            (Message.SubTaskExtraInfo, {"what": "OperBoxInfo"}),
            (Message.SubTaskExtraInfo, {"what": "OperBoxInfo", "details": {"done": True}}),
            (Message.SubTaskExtraInfo, "not a mapping"),
            (Message.SubTaskExtraInfo, _depot(None)),
        ):
            skipped = await _service(broken_factory).handle_callback(
                callback, message=message, created_at=RECOGNIZED_AT
            )
            assert skipped.written is False
            assert skipped.skipped != "error", callback

    asyncio.run(scenario())


def test_parsers_are_lenient_about_missing_and_out_of_range_fields() -> None:
    snapshots = parse_operbox(
        {
            "all_opers": [
                {"id": "char_002_amiya", "name": "阿米娅", "rarity": 5, "own": True},
                {"id": "char_103_angel", "name": "能天使", "rarity": 99, "own": "yes"},
                {"name": "没有 id"},
            ],
            "own_opers": [
                {
                    "id": "char_002_amiya",
                    "name": "阿米娅",
                    "own": True,
                    "elite": 7,
                    "level": 80,
                    "potential": 0,
                }
            ],
        }
    )
    assert snapshots is not None
    by_id = {snapshot.char_id: snapshot for snapshot in snapshots}
    assert set(by_id) == {"char_002_amiya", "char_103_angel"}
    amiya = by_id["char_002_amiya"]
    assert (amiya.own, amiya.level, amiya.listed_in_all) == (True, 80, True)
    # 越界的 elite / potential 与非法 own 不写：宁可不覆盖，也不落一个假值。
    assert (amiya.elite, amiya.potential) == (None, None)
    assert by_id["char_103_angel"].own is None
    assert by_id["char_103_angel"].rarity is None
    assert by_id["char_103_angel"].listed_in_all is True
    assert parse_operbox({}) is None
    assert parse_operbox({"all_opers": "nope"}) is None
    assert parse_operbox({"all_opers": []}) is None

    assert parse_depot_data({"data": '{"30012": 5, "4001": "nope"}'}) == {"30012": 5}
    assert parse_depot_data({"data": '{"30012": "7"}'}) == {"30012": 7}
    assert parse_depot_data({"data": "not json"}) is None
    assert parse_depot_data({"data": "{}"}) is None
    assert parse_depot_data({"data": '{"30012": -1}'}) is None
    assert parse_depot_data({}) is None


def test_local_item_index_maps_real_materials() -> None:
    catalog = load_item_catalog()
    if not catalog:
        pytest.skip("本机没有 item_index.json")
    assert catalog["30012"].name == "固源岩"
    assert catalog["30012"].category == "MATERIAL"


def test_local_operator_catalog_maps_real_professions() -> None:
    catalog = load_operator_catalog()
    if not catalog:
        pytest.skip("本机没有 battle_data.json")
    amiya = catalog["char_002_amiya"]
    assert amiya.name == "阿米娅"
    assert amiya.rarity == 5
    # 本机存的是英文枚举（CASTER），表里统一存中文。
    assert amiya.profession == "术师"
