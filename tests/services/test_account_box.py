"""干员表服务契约（M27 规格 §7.3、§7.5）：合并、覆盖、保留、差异、编辑约束、过期。

全部离线：临时 SQLite（``make_engine`` + ``create_all``，``NullPool`` 以便用例里多个
``asyncio.run`` 共用一个 engine），不打网、不打真机。时间用手工构造的 naive UTC 值
（库内统一 naive UTC，docs/04 §3.2），因此过期判定不需要冻结时钟。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.repositories.account import (
    AccountOperatorDiffRepository,
    AccountOperatorRepository,
)
from maa_api.db.session import make_engine
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.box import OperatorBoxService
from maa_api.settings import Settings

import pytest

#: 依赖并行任务的落地物：仓储模块与五张表。还没落地时整模块 skip，落地后自动变绿
#: （见本里程碑的交接说明）；不把 ImportError 伪装成用例失败。
pytest.importorskip(
    "maa_api.db.repositories.account",
    reason="M27 的表与仓储（db/models.py、db/repositories/account.py）尚未落地",
)


NOW = datetime(2026, 10, 10, 3, 0, 0)


def _utc(*, days_ago: int = 0) -> datetime:
    return NOW - timedelta(days=days_ago)


@pytest.fixture
def account_session_factory(tmp_path):
    """临时库 + ``NullPool`` 会话工厂（跨多个 ``asyncio.run`` 复用同一个 engine）。"""
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'account_box.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _service(factory, *, stale_days: int = 14) -> OperatorBoxService:
    """固定 ``agent.box_stale_days`` 的服务实例（不读真实 config.yaml）。"""
    settings = Settings()
    settings.agent.box_stale_days = stale_days
    return OperatorBoxService(factory, settings_provider=lambda: settings)


def _seed(factory, char_id: str, **fields) -> None:
    """直接经仓储写一行（模拟上一次识别或手动编辑留下的状态）。

    ``recognized_level`` 是给测试用的糖：把「上次识别到的等级」与当前的 ``level``
    分开设，用来构造「识别覆盖了手动值」的场景。``elite < 2`` 时不写专精与模组
    （编辑约束不允许那种状态存在）。
    """
    elite = int(fields.get("elite", 2) or 0)
    defaults: dict[str, object] = dict(
        name="测试干员",
        rarity=6,
        profession="近卫",
        own=True,
        elite=elite,
        level=90,
        potential=6,
        skill_level=7,
        masteries={"1": 3, "2": 0, "3": 0} if elite >= 2 else {"1": 0, "2": 0, "3": 0},
        modules={"X": 3} if elite >= 2 else {},
        recognized_values={
            "own": True,
            "elite": 2,
            "level": fields.get("recognized_level", 90),
            "potential": 6,
        },
        field_sources={
            "own": "recognition",
            "elite": "recognition",
            "level": "recognition",
            "potential": "recognition",
            "skill_level": "manual",
            "masteries": "manual",
            "modules": "manual",
        },
        recognized_at=_utc(),
        updated_at=_utc(),
    )
    defaults.update(fields)
    defaults.pop("recognized_level", None)

    async def scenario():
        async with factory() as session:
            repo = AccountOperatorRepository(session)
            # ``new_row(char_id, **fields)`` 是仓储的建行入口（这里逐字段赋值，
            # 避开对关键字形参名的依赖）。
            row = repo.new_row(char_id)
            for field, value in defaults.items():
                setattr(row, field, value)
            await repo.upsert(row)
            await session.commit()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 识别合并：覆盖 / 保留 / 差异
# ---------------------------------------------------------------------------


def test_first_recognition_creates_rows_and_records_fields(account_session_factory):
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_recognition(
            {
                "char_002_amiya": {
                    "name": "阿米娅",
                    "rarity": 5,
                    "profession": "术师",
                    "own": True,
                    "elite": 2,
                    "level": 80,
                    "potential": 6,
                }
            },
            now=NOW,
            recognized_at=NOW,
        )
        assert result == {"created": 1, "updated": 0, "diffs": 0}
        detail = await service.get_operator("char_002_amiya")
        assert detail["name"] == "阿米娅"
        assert detail["rarity"] == 5
        assert detail["profession"] == "术师"
        assert detail["elite"] == 2
        assert detail["level"] == 80
        assert detail["potential"] == 6
        # 识别只写它能识别的四个字段的来源；技能相关仍是未知来源。
        assert detail["field_sources"] == {
            "own": "recognition",
            "elite": "recognition",
            "level": "recognition",
            "potential": "recognition",
        }
        assert detail["recognized_at"] == "2026-10-10T03:00:00Z"
        assert detail["updated_at"] == "2026-10-10T03:00:00Z"
        assert detail["masteries"] == {"1": 0, "2": 0, "3": 0}
        assert detail["modules"] == {}

    asyncio.run(scenario())


def test_recognition_overwrites_only_its_fields_and_keeps_manual_ones(
    account_session_factory,
):
    """识别覆盖「是否拥有/精英化/等级/潜能」，技能等级、专精、模组原样保留。"""
    _seed(account_session_factory, "char_002_amiya", level=90, elite=2)
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_recognition(
            {
                "char_002_amiya": {
                    "name": "阿米娅",
                    "rarity": 6,
                    "own": True,
                    "elite": 1,
                    "level": 55,
                    "potential": 3,
                }
            },
            now=NOW,
            recognized_at=NOW,
        )
        # 手动改过的技能等级/专精/模组与识别值无交集，不产生差异。
        assert result["diffs"] == 0
        detail = await service.get_operator("char_002_amiya")
        assert (detail["elite"], detail["level"], detail["potential"]) == (1, 55, 3)
        assert detail["skill_level"] == 7
        assert detail["masteries"] == {"1": 3, "2": 0, "3": 0}
        assert detail["modules"] == {"X": 3}
        assert detail["field_sources"]["masteries"] == "manual"
        assert detail["field_sources"]["level"] == "recognition"

    asyncio.run(scenario())


def test_recognition_records_a_diff_when_it_overwrites_a_manual_value(
    account_session_factory,
):
    """上次识别之后手动改了等级、这次识别给了不同值 → 记一条差异并覆盖。"""
    _seed(
        account_session_factory,
        "char_002_amiya",
        level=55,
        recognized_level=80,
        field_sources={
            "own": "recognition",
            "elite": "recognition",
            "level": "manual",
            "potential": "recognition",
        },
    )
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_recognition(
            {"char_002_amiya": {"own": True, "elite": 2, "level": 90, "potential": 6}},
            now=NOW,
            recognized_at=NOW,
        )
        assert result["diffs"] == 1
        # 识别值生效（页面显示的是最新识别），手动值留在差异里等用户裁决。
        detail = await service.get_operator("char_002_amiya")
        assert detail["level"] == 90
        assert detail["field_sources"]["level"] == "recognition"

        diffs = await service.list_diffs()
        assert diffs["total"] == 1
        item = diffs["items"][0]
        assert item["char_id"] == "char_002_amiya"
        assert item["field"] == "level"
        assert item["manual_value"] == 55
        assert item["recognized_value"] == 90
        assert item["dismissed_at"] is None
        assert item["created_at"] == "2026-10-10T03:00:00Z"

    asyncio.run(scenario())


def test_recognition_does_not_record_a_diff_for_a_value_the_user_already_matched(
    account_session_factory,
):
    """手动值恰好等于这次识别值：来源变了，但不该打扰用户。"""
    _seed(
        account_session_factory,
        "char_002_amiya",
        level=90,
        recognized_level=80,
        field_sources={
            "own": "recognition",
            "elite": "recognition",
            "level": "manual",
            "potential": "recognition",
        },
    )
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_recognition(
            {"char_002_amiya": {"own": True, "elite": 2, "level": 90, "potential": 6}},
            now=NOW,
            recognized_at=NOW,
        )
        assert result["diffs"] == 0
        assert (await service.list_diffs())["total"] == 0

    asyncio.run(scenario())


def test_recognition_marks_operators_missing_from_own_opers_as_not_owned(
    account_session_factory,
):
    """``all_opers`` 里、这次 ``own_opers`` 里没有的干员 → ``own=False``。"""
    _seed(account_session_factory, "char_002_amiya")
    service = _service(account_session_factory)

    async def scenario():
        result = await service.apply_recognition(
            {},
            all_operators=["char_002_amiya", "char_103_angel"],
            now=NOW,
            recognized_at=NOW,
        )
        # 两条都在 all_opers 里：一条已有行、一条新建为未拥有。
        assert result == {"created": 1, "updated": 1, "diffs": 0}
        owned = await service.list_operators(owned_only=True)
        assert [item["char_id"] for item in owned["items"]] == []
        everyone = await service.list_operators(owned_only=False)
        assert {item["char_id"] for item in everyone["items"]} == {
            "char_002_amiya",
            "char_103_angel",
        }
        assert all(item["own"] is False for item in everyone["items"])

    asyncio.run(scenario())


def test_recognition_without_all_opers_does_not_clear_other_rows(
    account_session_factory,
):
    """旧版内核对调没有 ``all_opers``：不在本次结果里的行必须原样保留。"""
    _seed(account_session_factory, "char_002_amiya", own=True)
    service = _service(account_session_factory)

    async def scenario():
        await service.apply_recognition(
            {"char_103_angel": {"name": "能天使", "rarity": 6, "own": True}},
            now=NOW,
            recognized_at=NOW,
        )
        ami = await service.get_operator("char_002_amiya")
        assert ami["own"] is True
        assert ami["level"] == 90

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 编辑约束（规格 §7.5 的编辑抽屉）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rarity", "cap"),
    [(1, 0), (2, 0), (3, 1), (4, 2), (5, 2), (6, 2)],
)
def test_elite_cap_follows_rarity(account_session_factory, rarity, cap):
    _seed(account_session_factory, "char_x", rarity=rarity, elite=0)
    service = _service(account_session_factory)

    async def scenario():
        detail = await service.patch_operator("char_x", {"elite": cap})
        assert detail["elite"] == cap
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"elite": cap + 1})
        assert caught.value.code == ErrorCode.VALIDATION_ERROR
        assert caught.value.http_status == 422
        if cap < 2:
            assert caught.value.details == {
                "field": "elite",
                "reason": f"{rarity}★ 干员的精英化上限是 {cap}",
                "allowed": {"maximum": cap, "rarity": rarity},
            }
        else:
            # 4–6★ 的上限就是全表上限 2：越界先撞字段范围。
            assert caught.value.details == {
                "field": "elite",
                "reason": "超出取值范围",
                "allowed": {"minimum": 0, "maximum": 2},
            }

    asyncio.run(scenario())


def test_masteries_require_elite_two_and_skill_seven(account_session_factory):
    _seed(account_session_factory, "char_x", elite=1, skill_level=7)
    service = _service(account_session_factory)

    async def scenario():
        # 精一：连填都不许。
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"1": 1}})
        assert caught.value.code == ErrorCode.VALIDATION_ERROR
        assert caught.value.details["field"] == "masteries"

        # 精二但技能不满 7：同样拒绝。
        await service.patch_operator("char_x", {"elite": 2, "skill_level": 6})
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"1": 1}})
        assert caught.value.details["reason"] == "专精要求技能等级为 7"

        # 精二 + 技能 7：可填，且写回 wire 形状 {"1": ..}。
        detail = await service.patch_operator(
            "char_x", {"skill_level": 7, "masteries": {"1": 3, "2": 0, "3": 0}}
        )
        assert detail["masteries"] == {"1": 3, "2": 0, "3": 0}
        assert detail["field_sources"]["masteries"] == "manual"

    asyncio.run(scenario())


def test_mastery_slots_beyond_the_rarity_skill_count_are_zeroed(
    account_session_factory,
):
    """4★ 只有两个技能：第 3 槽写 3 也归零，别的槽照常。"""
    _seed(
        account_session_factory,
        "char_x",
        rarity=4,
        elite=2,
        skill_level=7,
        masteries={},
        modules={},
    )
    service = _service(account_session_factory)

    async def scenario():
        detail = await service.patch_operator(
            "char_x", {"masteries": {"1": 3, "2": 2, "3": 3}}
        )
        assert detail["masteries"] == {"1": 3, "2": 2, "3": 0}

    asyncio.run(scenario())


def test_mastery_value_and_key_ranges_are_enforced(account_session_factory):
    _seed(account_session_factory, "char_x", elite=2, skill_level=7, masteries={})
    service = _service(account_session_factory)

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"1": 4}})
        assert caught.value.details["allowed"] == {"minimum": 0, "maximum": 3}
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"4": 1}})
        assert caught.value.details["field"] == "masteries"

    asyncio.run(scenario())


def test_modules_require_elite_two_and_fixed_types(account_session_factory):
    _seed(account_session_factory, "char_x", elite=1)
    service = _service(account_session_factory)

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"X": 1}})
        assert caught.value.details["reason"] == "模组只在精二时可填"

        await service.patch_operator("char_x", {"elite": 2})
        detail = await service.patch_operator(
            "char_x", {"modules": {"X": 1, "Y": 2, "D": 3, "A": 1}}
        )
        assert detail["modules"] == {"X": 1, "Y": 2, "D": 3, "A": 1}

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"Z": 1}})
        assert caught.value.details["allowed"] == {"types": ["X", "Y", "D", "A"]}
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"X": 4}})
        assert caught.value.details["allowed"] == {"minimum": 1, "maximum": 3}
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"X": 0}})
        assert caught.value.details["field"] == "modules.X"

    asyncio.run(scenario())


def test_setting_elite_below_two_clears_masteries_and_modules(account_session_factory):
    _seed(account_session_factory, "char_x", elite=2, masteries={"1": 3}, modules={"X": 3})
    service = _service(account_session_factory)

    async def scenario():
        detail = await service.patch_operator("char_x", {"elite": 1})
        assert detail["masteries"] == {"1": 0, "2": 0, "3": 0}
        assert detail["modules"] == {}
        assert detail["field_sources"]["masteries"] == "manual"
        assert detail["field_sources"]["modules"] == "manual"

    asyncio.run(scenario())


def test_patch_marks_field_sources_and_rejects_unknown_fields(
    account_session_factory,
):
    _seed(account_session_factory, "char_x", field_sources={})
    service = _service(account_session_factory)

    async def scenario():
        detail = await service.patch_operator(
            "char_x", {"level": 42, "potential": 2}, source="agent"
        )
        assert detail["field_sources"] == {"level": "agent", "potential": "agent"}
        # 未给的字段不动。
        assert detail["elite"] == 2
        assert detail["skill_level"] == 7

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"rarity": 6})
        assert caught.value.code == ErrorCode.VALIDATION_ERROR
        assert caught.value.details["allowed"]["fields"] == ["rarity"]

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"level": 91})
        assert caught.value.details["field"] == "level"

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"level": True})
        assert caught.value.details["field"] == "level"

    asyncio.run(scenario())


def test_patch_missing_operator_is_404(account_session_factory):
    service = _service(account_session_factory)

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.get_operator("char_none")
        assert caught.value.code == ErrorCode.ACCOUNT_DATA_NOT_FOUND
        assert caught.value.http_status == 404

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_none", {"level": 10})
        assert caught.value.code == ErrorCode.ACCOUNT_DATA_NOT_FOUND

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 列表筛选与概要
# ---------------------------------------------------------------------------


def test_list_operators_filters_and_paginates(account_session_factory):
    _seed(account_session_factory, "char_a", name="阿米娅", rarity=5, profession="术师", elite=2)
    _seed(account_session_factory, "char_b", name="陈", rarity=6, profession="近卫", elite=1)
    _seed(account_session_factory, "char_c", name="能天使", rarity=6, profession="狙击", own=False, elite=0)
    service = _service(account_session_factory)

    async def scenario():
        page = await service.list_operators(limit=1, offset=0)
        assert page["total"] == 2  # 默认隐藏未拥有
        assert page["limit"] == 1 and page["offset"] == 0
        assert len(page["items"]) == 1
        assert set(page["items"][0]) == {
            "char_id",
            "name",
            "rarity",
            "profession",
            "own",
            "elite",
            "level",
            "potential",
            "skill_level",
            "masteries",
            "modules",
            "field_sources",
            "recognized_at",
            "updated_at",
        }

        assert (await service.list_operators(rarity_min=6))["total"] == 1
        assert (await service.list_operators(profession="狙击", owned_only=False))["total"] == 1
        assert (await service.list_operators(elite_min=2))["total"] == 1
        assert (await service.list_operators(query="能天", owned_only=False))["total"] == 1
        assert (await service.list_operators(query="  "))["total"] == 2  # 空串忽略

        second = await service.list_operators(limit=1, offset=1)
        assert second["total"] == 2 and len(second["items"]) == 1
        assert second["items"][0]["char_id"] != page["items"][0]["char_id"]

        # limit / offset 越界由服务层夹住，不把负数丢给 SQLite。
        clamped = await service.list_operators(limit=0, offset=-5)
        assert clamped["limit"] == 1 and clamped["offset"] == 0

    asyncio.run(scenario())


def test_summary_counts_elite_two_and_pending_diffs(account_session_factory):
    _seed(account_session_factory, "char_a", elite=2)
    _seed(account_session_factory, "char_b", elite=1)
    _seed(account_session_factory, "char_c", own=False, elite=0)
    service = _service(account_session_factory)

    async def scenario():
        summary = await service.summary()
        assert summary["total"] == 2
        assert summary["elite_two"] == 1
        assert summary["unrecognized"] == 1
        assert summary["pending_diffs"] == 0
        assert summary["stale_days"] == 14
        assert summary["stale"] is False
        assert summary["recognized_at"] == "2026-10-10T03:00:00Z"

    asyncio.run(scenario())


def test_summary_uses_the_configured_stale_threshold(account_session_factory):
    _seed(account_session_factory, "char_a", recognized_at=_utc(days_ago=5))
    service = _service(account_session_factory, stale_days=14)
    strict = _service(account_session_factory, stale_days=3)

    async def scenario():
        assert (await service.summary())["stale"] is False
        assert (await strict.summary())["stale"] is True

    asyncio.run(scenario())


def test_summary_is_stale_when_nothing_was_ever_recognized(account_session_factory):
    service = _service(account_session_factory)

    async def scenario():
        summary = await service.summary()
        assert summary == {
            "total": 0,
            "elite_two": 0,
            "recognized_at": None,
            "stale": True,
            "stale_days": 14,
            "unrecognized": 0,
            "pending_diffs": 0,
        }

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 差异的「知道了」
# ---------------------------------------------------------------------------


def test_dismiss_diffs_marks_selected_or_all(account_session_factory):
    service = _service(account_session_factory)
    for char_id, field, manual, recognized in (
        ("char_a", "level", 55, 90),
        ("char_b", "elite", 1, 2),
        ("char_c", "potential", 1, 6),
    ):
        _seed(account_session_factory, char_id, level=manual, elite=1, potential=1)

    async def scenario():
        # 先手工造三条差异（识别路径已在上面覆盖，这里直接写仓储更快）。
        from maa_api.db.repositories.account import AccountOperatorDiffRepository

        async with account_session_factory() as session:
            repo = AccountOperatorDiffRepository(session)
            for char_id, field, manual, recognized in (
                ("char_a", "level", 55, 90),
                ("char_b", "elite", 1, 2),
                ("char_c", "potential", 1, 6),
            ):
                await repo.create(
                    char_id=char_id,
                    field=field,
                    manual_value=manual,
                    recognized_value=recognized,
                    created_at=NOW,
                )
            await session.commit()

        diffs = await service.list_diffs()
        assert diffs["total"] == 3
        first_id = diffs["items"][0]["id"]

        assert await service.dismiss_diffs([first_id]) == 1
        # 幂等：再标记同一条不再计数。
        assert await service.dismiss_diffs([first_id]) == 0
        remaining = await service.list_diffs()
        assert remaining["total"] == 2
        assert len(remaining["items"]) == 2

        # include_dismissed 把已知的一起列出。
        everything = await service.list_diffs(include_dismissed=True)
        assert len(everything["items"]) == 3
        assert everything["total"] == 2  # total 始终是未处理数

        assert await service.dismiss_diffs(None) == 2
        assert (await service.list_diffs())["items"] == []

    asyncio.run(scenario())
