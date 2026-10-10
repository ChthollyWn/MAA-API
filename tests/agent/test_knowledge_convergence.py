"""M30 T3：删除内置条目时的覆盖行迁移（规格 §10.1 最后一条、§10.3 的 T3）。

背景：``agent_knowledge`` 的覆盖行只存与内置**不同**的列，其余为 NULL。内置条目
一旦从 ``builtin/*.md`` 删掉，这行就「没有内置可继承」了——NULL 列没有任何来源，
``_merge`` 会对它抛 ``RuntimeError``（自定义条目字段不完整），于是整个知识库都读不
出来。所以删除动作必须配一次迁移：把覆盖行就地补全成**自定义条目**，NULL 列用迁移
代码里冻结的最后一版内置内容补齐（正文随版本变，删掉之后再也读不到，只能冻结）。

本文件覆盖的契约：

- 被删且被用户覆盖过 → 迁移成同 id 的自定义条目；NULL 列取冻结值，用户改过的列保留；
- 被删但用户没覆盖过 → 没有行要迁移（``skipped``）；
- 内置仍在 → 一律不动；
- 幂等：跑两次，第二次 ``migrated`` 为空且行内容一字不变；
- ``frozen`` 为空字典 → 空操作（迁移机制先落地、删除清单后填时的状态）；
- 冻结里的 ``kind`` 与覆盖行冲突时以冻结值为准（照 ``_merge`` 的口径：kind 不可覆盖）；
- 任何异常都不抛给启动路径。

测试用 ``frozen=`` 传本文件内的假冻结表，**不依赖真实的**
``FROZEN_BUILTIN_ENTRIES``（真实的那份由主 agent 在收尾时按删除清单填）。

与同目录其它用例一致：没有 pytest-asyncio，异步用例写成「同步函数 + ``asyncio.run``」。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.agent.knowledge import (
    MAX_BODY_CHARS,
    BuiltinEntry,
    KnowledgeService,
)
from maa_api.agent.knowledge.convergence import (
    FrozenEntry,
    migrate_deleted_overrides,
)
from maa_api.db import session as db_session
from maa_api.db.models import AgentKnowledge

# ---------------------------------------------------------------------------
# 测试内的假内置表与假冻结表
# ---------------------------------------------------------------------------
#: 被删除的条目：迁移前它还在上一版的内置里，迁移后 ``load_builtins()`` 里没有它。
DELETED_KIND = "rule"
DELETED_FROZEN: dict[str, str] = {
    "kind": DELETED_KIND,
    "title": "理智与掉落（冻结标题）",
    "when_to_use": "冻结版的使用时机（用户没改过这一列）",
    "body": "冻结版的正文（用户没改过这一列）",
}

#: 内置仍在的条目：任何迁移都不该碰它。
KEPT_KIND = "playbook"
KEPT_BUILTIN = BuiltinEntry(
    id="kept-entry",
    kind=KEPT_KIND,
    title="仍在的内置标题",
    when_to_use="仍在的内置时机",
    body="仍在的内置正文",
)

#: 迁移时「当前版本」的内置表：只有 ``kept-entry``，``deleted-entry`` 已被删除。
CURRENT_BUILTINS: dict[str, BuiltinEntry] = {KEPT_BUILTIN.id: KEPT_BUILTIN}

#: 用户改过的列值（其余列为 NULL = 跟随内置）。
USER_TITLE = "用户改过的标题"
USER_BODY = "用户改过的正文"


def _frozen() -> dict[str, dict[str, str]]:
    """每次调用给一份新的假冻结表，避免用例之间串改。"""
    return {"deleted-entry": dict(DELETED_FROZEN)}


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------
async def _create_all(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


class _BoomFactory:
    """会话工厂桩：一被调用就炸，模拟迁移时数据库不可用。"""

    def __call__(self) -> Any:
        raise RuntimeError("数据库暂时不可用（测试桩）")


@pytest.fixture
def harness(tmp_path: Any):
    """``(factory, session_factory, service)``，绑定在一次性 SQLite 文件上。"""
    engine = db_session.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'knowledge-convergence.db'}",
        poolclass=NullPool,
    )
    asyncio.run(_create_all(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    service = KnowledgeService(factory, builtins=CURRENT_BUILTINS)
    yield factory, service
    engine.sync_engine.dispose()


async def _migrate(
    factory: Any,
    frozen: Mapping[str, Mapping[str, str]],
    *,
    builtins: Mapping[str, BuiltinEntry] = CURRENT_BUILTINS,
) -> dict[str, list[str]]:
    """跑一次迁移，两个注入点都显式给：内置表用测试内的那份，不碰真的 builtin/*.md。"""
    return await migrate_deleted_overrides(factory, frozen=frozen, builtins=builtins)


async def _seed(factory: Any, **values: Any) -> None:
    """直接写一行覆盖行，模拟「用户在删条目之前改过它」。"""
    async with factory() as session:
        session.add(AgentKnowledge(**values))
        await session.commit()


async def _rows(factory: Any) -> dict[str, AgentKnowledge]:
    """全表快照：``{id: 行}``（对象已脱离会话，断言时直接用）。"""
    async with factory() as session:
        result = await session.execute(select(AgentKnowledge))
        return {row.id: row for row in result.scalars().all()}


async def _row_snapshot(factory: Any) -> dict[str, tuple[Any, ...]]:
    """行的全部可写列快照，用来断言「幂等 = 一字不变」。"""
    rows = await _rows(factory)
    return {
        entry_id: (
            row.kind,
            row.title,
            row.when_to_use,
            row.body,
            row.enabled,
            row.created_at,
            row.updated_at,
        )
        for entry_id, row in rows.items()
    }


async def _entry(service: KnowledgeService, entry_id: str):
    """一条条目的完整生效内容（带正文）；不存在返回 ``None``。"""
    rows = {item.id: item for item in await service.list_entries()}
    if entry_id not in rows:
        return None
    return await service.get_entry(entry_id)


# ---------------------------------------------------------------------------
# 1. 被删 + 被覆盖过 → 迁移成自定义条目，NULL 列补齐、改过的列保留
# ---------------------------------------------------------------------------
def test_migrates_overridden_deleted_entry(harness) -> None:
    factory, service = harness

    async def scenario() -> None:
        await _seed(
            factory,
            id="deleted-entry",
            kind=None,
            title=USER_TITLE,  # 用户改过
            when_to_use=None,  # 跟随内置 → 迁移后取冻结值
            body=USER_BODY,  # 用户改过
            enabled=True,
        )
        # 迁移前的状态只能从库里读：这时它是字段不完整的自定义行，走 service
        # 的 ``list_entries()`` 会抛「字段不完整」，整张表都读不出来。
        before = (await _rows(factory))["deleted-entry"]
        assert (before.kind, before.title, before.when_to_use, before.body) == (
            None,
            USER_TITLE,
            None,
            USER_BODY,
        )

        report = await _migrate(factory, _frozen())

        assert report["migrated"] == ["deleted-entry"]
        assert report["skipped"] == []

        # 迁移后必须能正常读出（迁移前它是一行字段不完整的自定义行，
        # `_merge` 会抛「自定义知识条目字段不完整」而拖垮整个知识库）。
        entry = await _entry(service, "deleted-entry")
        assert entry is not None
        assert entry.source == "custom"
        assert entry.kind == DELETED_FROZEN["kind"]
        assert entry.title == USER_TITLE, "用户改过的列必须保留"
        assert entry.body == USER_BODY, "用户改过的列必须保留"
        assert entry.when_to_use == DELETED_FROZEN["when_to_use"], "NULL 列用冻结值补齐"

        row = (await _rows(factory))["deleted-entry"]
        assert row.kind == DELETED_FROZEN["kind"]
        assert row.title == USER_TITLE
        assert row.when_to_use == DELETED_FROZEN["when_to_use"]
        assert row.body == USER_BODY
        assert None not in (row.kind, row.title, row.when_to_use, row.body), (
            "迁移后不能再有 NULL 列：自定义条目要求字段完整"
        )

    asyncio.run(scenario())


def test_frozen_fills_every_null_column(harness) -> None:
    """用户一列都没改（只停用）时，四个字段全部取冻结值。"""
    factory, service = harness

    async def scenario() -> None:
        await _seed(
            factory,
            id="deleted-entry",
            kind=None,
            title=None,
            when_to_use=None,
            body=None,
            enabled=False,
        )

        report = await _migrate(factory, _frozen())

        assert report == {"migrated": ["deleted-entry"], "skipped": []}
        entry = await _entry(service, "deleted-entry")
        assert entry.title == DELETED_FROZEN["title"]
        assert entry.when_to_use == DELETED_FROZEN["when_to_use"]
        assert entry.body == DELETED_FROZEN["body"]
        assert entry.kind == DELETED_FROZEN["kind"]
        assert entry.enabled is False, "用户停用过就保持停用"
        assert entry.source == "custom"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2–3. 不需要迁移的行
# ---------------------------------------------------------------------------
def test_deleted_but_never_overridden_is_skipped(harness) -> None:
    """被删、但用户没覆盖过 → 表里本来就没有行，不产生新行。"""
    factory, service = harness

    async def scenario() -> None:
        report = await _migrate(factory, _frozen())

        assert report["migrated"] == []
        assert report["skipped"] == ["deleted-entry"]
        assert await _rows(factory) == {}, "迁移不会凭冻结内容造出新条目"
        assert (await _rows(factory)) == {}

    asyncio.run(scenario())


def test_builtin_still_present_is_untouched(harness) -> None:
    """内置仍在：覆盖行的 NULL 列照旧继承内置，迁移一行都不动。"""
    factory, service = harness

    async def scenario() -> None:
        await _seed(
            factory,
            id="kept-entry",
            kind=None,
            title=USER_TITLE,  # 与内置不同的列
            when_to_use=None,  # 与内置相同 → NULL
            body=None,
            enabled=True,
        )
        before = await _row_snapshot(factory)

        report = await _migrate(
            factory,
            {
                "kept-entry": {
                    "kind": "rule",
                    "title": "不该被用到",
                    "when_to_use": "x",
                    "body": "x",
                }
            },
        )

        assert report == {"migrated": [], "skipped": ["kept-entry"]}
        assert await _row_snapshot(factory) == before
        entry = await _entry(service, "kept-entry")
        assert entry.source == "builtin", "仍是内置条目的覆盖行，没有变成自定义"
        assert entry.title == USER_TITLE
        assert entry.when_to_use == KEPT_BUILTIN.when_to_use

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 4. 幂等
# ---------------------------------------------------------------------------
def test_second_run_is_a_noop(harness) -> None:
    factory, _ = harness

    async def scenario() -> None:
        await _seed(
            factory,
            id="deleted-entry",
            kind=None,
            title=USER_TITLE,
            when_to_use=None,
            body=None,
            enabled=True,
        )

        first = await _migrate(factory, _frozen())
        assert first["migrated"] == ["deleted-entry"]
        after_first = await _row_snapshot(factory)

        second = await _migrate(factory, _frozen())

        assert second["migrated"] == [], "第二次运行不产生任何变化"
        assert second["skipped"] == ["deleted-entry"]
        assert await _row_snapshot(factory) == after_first, "行内容一字不变"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 5. 空冻结表 = 空操作
# ---------------------------------------------------------------------------
def test_empty_frozen_is_a_noop(harness) -> None:
    """``FROZEN_BUILTIN_ENTRIES`` 还没填（机制先落地）时迁移什么都不做。

    空冻结表是「删除清单还没填」的常态：不查库、不写库、连内置条目表都不读
    （真的开始迁移时才需要它）。
    """
    factory, _ = harness

    async def scenario() -> None:
        await _seed(
            factory,
            id="deleted-entry",
            kind=None,
            title=USER_TITLE,
            when_to_use=None,
            body=None,
            enabled=True,
        )
        before = await _row_snapshot(factory)

        report = await _migrate(factory, {})

        assert report == {"migrated": [], "skipped": []}
        assert await _row_snapshot(factory) == before, "空冻结表下迁移是彻底的 no-op"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 6. kind 冲突以冻结值为准
# ---------------------------------------------------------------------------
def test_frozen_kind_wins_over_row_kind(harness) -> None:
    """上一版留下的自定义行撞上同 id 的覆盖行时 kind 已经乱了；照 ``_merge`` 的口径
    （kind 不可覆盖），迁移后取冻结值。"""
    factory, service = harness

    async def scenario() -> None:
        await _seed(
            factory,
            id="deleted-entry",
            kind="glossary",  # 与冻结的 kind 冲突
            title=USER_TITLE,
            when_to_use=None,
            body=None,
            enabled=True,
        )

        report = await _migrate(factory, _frozen())

        assert report["migrated"] == ["deleted-entry"]
        row = (await _rows(factory))["deleted-entry"]
        assert row.kind == DELETED_FROZEN["kind"], "kind 取冻结值"
        assert row.title == USER_TITLE
        assert (await _entry(service, "deleted-entry")).kind == DELETED_FROZEN["kind"]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 7. 异常不抛给启动路径
# ---------------------------------------------------------------------------
def test_session_failure_is_swallowed() -> None:
    """迁移失败只记 warning：知识库读不出来不该拦住整个服务启动。"""

    async def scenario() -> None:
        report = await migrate_deleted_overrides(
            _BoomFactory(), frozen={"deleted-entry": dict(DELETED_FROZEN)}
        )
        assert report == {"migrated": [], "skipped": []}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad_frozen",
    [
        pytest.param(
            {"kind": "rule", "title": "缺 when_to_use 与 body"},
            id="missing-fields",
        ),
        pytest.param(
            {
                "kind": "rule",
                "title": "标题",
                "when_to_use": "时机",
                "body": "正" * (MAX_BODY_CHARS + 1),
            },
            id="over-column-width",
        ),
    ],
)
def test_bad_frozen_entry_does_not_crash_startup(harness, bad_frozen) -> None:
    """冻结表写坏（缺字段、超列宽）时不抛异常，也不写坏数据，只是这一条不迁移。"""
    factory, _ = harness

    async def scenario() -> None:
        await _seed(
            factory,
            id="deleted-entry",
            kind=None,
            title=None,
            when_to_use=None,
            body=None,
            enabled=True,
        )
        before = await _row_snapshot(factory)
        bad: Mapping[str, Mapping[str, str]] = {"deleted-entry": bad_frozen}

        report = await _migrate(factory, bad)  # type: ignore[arg-type]

        assert report == {"migrated": [], "skipped": ["deleted-entry"]}
        assert await _row_snapshot(factory) == before, "不合格的冻结内容不会被写进去"

    asyncio.run(scenario())
