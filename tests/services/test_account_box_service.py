"""干员表服务的读侧逻辑契约（规格 §7.3、§7.5）：用假仓储驱动真实服务代码。

与 ``test_account_box.py`` 的分工：
- 本文件**不碰数据库**，把 ``AccountOperatorRepository`` 换成内存假替身，验证概要计数、
  过期判定、列表筛选与分页夹取、单条 404。
- ``test_account_box.py`` 是同一个服务的 SQLite 集成版本。两者共用同一批场景名，
  这样「假仓储绿了、真仓储没绿」时能一眼看出是仓储接口的差异，而不是业务逻辑。

M47 起写侧只剩森空岛同步（``apply_skland_sync``，契约见 ``test_account_box_skland.py``）：
内核 ``OperBox`` 识别合并、手动编辑与识别差异随之下线（docs/13 §8.70），所以这里不再
有 ``apply_recognition`` / ``patch_operator`` / ``list_diffs`` 的场景。

假仓储只实现服务用到的那些方法，签名与 ``db/repositories/account.py`` 冻结的接口
一致；差异出现时在这里加一层适配，而不是让服务层为测试让步。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings

from maa_api.services.account import box as box_module  # noqa: E402
from maa_api.services.account.box import OperatorBoxService  # noqa: E402

NOW = datetime(2026, 10, 10, 3, 0, 0)


def _utc(*, days_ago: int = 0) -> datetime:
    return NOW - timedelta(days=days_ago)


class _Row:
    """一条干员行：属性名与 ``account_operator`` 的列同名。"""

    def __init__(self, char_id: str, **fields: Any) -> None:
        self.char_id = char_id
        self.name: str | None = None
        self.rarity: int | None = None
        self.profession: str | None = None
        self.own = False
        self.elite: int | None = None
        self.level: int | None = None
        self.potential: int | None = None
        self.skill_level: int | None = None
        self.masteries: dict[str, int] | None = None
        self.modules: dict[str, int] | None = None
        self.favor_percent: int | None = None
        self.skill_ids: list[str] | None = None
        self.module_details: list[dict[str, Any]] | None = None
        self.recognized_values: dict[str, Any] = {}
        self.field_sources: dict[str, str] = {}
        self.recognized_at: datetime | None = None
        self.updated_at: datetime | None = None
        for key, value in fields.items():
            setattr(self, key, value)


class _Store:
    """假仓储的内存状态。"""

    def __init__(self) -> None:
        self.rows: dict[str, _Row] = {}


def _make_fakes(store: _Store) -> dict[str, Any]:
    """构造与冻结接口同形的假仓储类。"""

    class AccountOperatorRepository:
        def __init__(self, session: Any = None) -> None:
            self.session = session

        def new_row(self, char_id: str, **fields: Any) -> _Row:
            return _Row(char_id, **fields)

        async def get(self, char_id: str) -> _Row | None:
            return store.rows.get(char_id)

        async def upsert(self, row: _Row) -> None:
            store.rows[row.char_id] = row

        def _filtered(
            self,
            *,
            query: str | None,
            owned_only: bool | None,
            rarity_min: int | None,
            profession: str | None,
            elite_min: int | None,
        ) -> list[_Row]:
            rows = sorted(store.rows.values(), key=lambda item: item.char_id)
            # 三态与真仓储一致：``None`` 不过滤 / ``True`` 只列拥有 / ``False`` 只列未拥有。
            if owned_only is not None:
                rows = [row for row in rows if bool(row.own) is owned_only]
            if query:
                needle = query.casefold()
                rows = [
                    row
                    for row in rows
                    if needle in (row.name or "").casefold()
                    or needle in row.char_id.casefold()
                ]
            if rarity_min is not None:
                rows = [row for row in rows if (row.rarity or 0) >= rarity_min]
            if profession:
                rows = [row for row in rows if row.profession == profession]
            if elite_min is not None:
                rows = [row for row in rows if (row.elite or 0) >= elite_min]
            return rows

        async def count(
            self,
            *,
            query: str | None = None,
            owned_only: bool | None = True,
            rarity_min: int | None = None,
            profession: str | None = None,
            elite_min: int | None = None,
        ) -> int:
            return len(
                self._filtered(
                    query=query,
                    owned_only=owned_only,
                    rarity_min=rarity_min,
                    profession=profession,
                    elite_min=elite_min,
                )
            )

        async def list(
            self,
            *,
            query: str | None = None,
            owned_only: bool | None = True,
            rarity_min: int | None = None,
            profession: str | None = None,
            elite_min: int | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> list[_Row]:
            rows = self._filtered(
                query=query,
                owned_only=owned_only,
                rarity_min=rarity_min,
                profession=profession,
                elite_min=elite_min,
            )
            return rows[offset : offset + limit]

        async def last_recognized_at(self) -> datetime | None:
            stamps = [
                row.recognized_at for row in store.rows.values() if row.recognized_at
            ]
            return max(stamps) if stamps else None

    return {"AccountOperatorRepository": AccountOperatorRepository}


@pytest.fixture
def box(monkeypatch):
    """``(service, store)``：真实服务 + 假仓储（仓储入口被换掉）。"""
    store = _Store()
    fakes = _make_fakes(store)
    # 服务层的仓储入口是懒加载的 ``_repositories()``（模块级不 import db.repositories），
    # 这里把它整个换掉。
    monkeypatch.setattr(
        box_module,
        "_repositories",
        lambda: fakes["AccountOperatorRepository"],
    )

    class _Session:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

        async def close(self) -> None:
            return None

    @asynccontextmanager
    async def session_factory():
        yield _Session()

    service = OperatorBoxService(
        session_factory, settings_provider=lambda: _settings(stale_days=14)
    )
    return service, store


def _settings(*, stale_days: int) -> Settings:
    settings = Settings()
    settings.agent.box_stale_days = stale_days
    return settings


def _seed(store: _Store, char_id: str, **fields: Any) -> _Row:
    """造一行「森空岛同步过」的形状。"""
    defaults: dict[str, Any] = dict(
        name="测试干员",
        rarity=6,
        profession="近卫",
        own=True,
        elite=2,
        level=90,
        potential=6,
        skill_level=7,
        masteries={"1": 3, "2": 0, "3": 0},
        modules={"X": 3},
        recognized_values={"own": True, "elite": 2, "level": 90, "potential": 6},
        field_sources={
            "own": "skland",
            "elite": "skland",
            "level": "skland",
            "potential": "skland",
            "skill_level": "skland",
            "masteries": "skland",
            "modules": "skland",
        },
        recognized_at=_utc(),
        updated_at=_utc(),
    )
    defaults.update(fields)
    row = _Row(char_id, **defaults)
    store.rows[char_id] = row
    return row


# ---------------------------------------------------------------------------
# 列表筛选与概要
# ---------------------------------------------------------------------------


def test_list_operators_filters_and_paginates(box):
    service, store = box
    _seed(store, "char_a", name="阿米娅", rarity=5, profession="术师", elite=2)
    _seed(store, "char_b", name="陈", rarity=6, profession="近卫", elite=1)
    _seed(store, "char_c", name="能天使", rarity=6, profession="狙击", own=False, elite=0)

    async def scenario():
        page = await service.list_operators(limit=1, offset=0)
        assert page["total"] == 2  # 默认隐藏未拥有
        assert (page["limit"], page["offset"], len(page["items"])) == (1, 0, 1)
        assert page["items"][0] == box_module.box_row_wire(store.rows["char_a"])

        assert (await service.list_operators(rarity_min=6))["total"] == 1
        assert (await service.list_operators(profession="狙击", owned_only=False))["total"] == 1
        assert (await service.list_operators(elite_min=2))["total"] == 1
        assert (await service.list_operators(query="能天", owned_only=False))["total"] == 1
        assert (await service.list_operators(query="  "))["total"] == 2  # 空串与空 profession 忽略
        assert (await service.list_operators(profession="   "))["total"] == 2

        second = await service.list_operators(limit=1, offset=1)
        assert second["total"] == 2 and second["items"][0]["char_id"] != page["items"][0]["char_id"]

        # limit / offset 越界由服务层夹住，不把负数丢给 SQLite。
        clamped = await service.list_operators(limit=0, offset=-5)
        assert (clamped["limit"], clamped["offset"]) == (1, 0)

    asyncio.run(scenario())


def test_summary_counts_owned_operators_and_elite_two(box):
    service, store = box
    _seed(store, "char_a", elite=2)
    _seed(store, "char_b", elite=1)
    _seed(store, "char_c", own=False, elite=0)

    async def scenario():
        summary = await service.summary()
        assert summary["total"] == 2
        assert summary["elite_two"] == 1
        assert summary["unrecognized"] == 1
        assert summary["stale_days"] == 14
        assert summary["stale"] is False
        assert summary["recognized_at"] == "2026-10-10T03:00:00Z"
        # M47：差异条随写通道下线，概要不再有 pending_diffs。
        assert "pending_diffs" not in summary

    asyncio.run(scenario())


def test_summary_uses_the_configured_stale_threshold(box):
    service, store = box
    _seed(store, "char_a", recognized_at=_utc(days_ago=5))
    strict = OperatorBoxService(
        service._session_factory, settings_provider=lambda: _settings(stale_days=3)
    )

    async def scenario():
        assert (await service.summary())["stale"] is False
        assert (await strict.summary())["stale"] is True

    asyncio.run(scenario())


def test_summary_is_stale_when_nothing_was_ever_synced(box):
    service, _store = box

    async def scenario():
        summary = await service.summary()
        assert summary == {
            "total": 0,
            "elite_two": 0,
            "recognized_at": None,
            "stale": True,
            "stale_days": 14,
            "unrecognized": 0,
        }

    asyncio.run(scenario())


def test_summary_treats_the_threshold_as_inclusive(box):
    """``recognized_at`` 恰好落在阈值上算过期（与仓储层的 ``<=`` 口径一致）。"""
    service, store = box
    _seed(store, "char_a", recognized_at=NOW - timedelta(days=14))

    async def scenario():
        assert (await service.summary())["stale"] is True

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 单条
# ---------------------------------------------------------------------------


def test_get_operator_returns_the_wire_shape(box):
    service, store = box
    _seed(store, "char_a", name="阿米娅", rarity=5, profession="术师")

    async def scenario():
        detail = await service.get_operator("char_a")
        assert detail["char_id"] == "char_a"
        assert (detail["name"], detail["rarity"], detail["profession"]) == ("阿米娅", 5, "术师")
        assert detail["field_sources"] == {
            "own": "skland",
            "elite": "skland",
            "level": "skland",
            "potential": "skland",
            "skill_level": "skland",
            "masteries": "skland",
            "modules": "skland",
        }

    asyncio.run(scenario())


def test_get_operator_missing_row_is_404(box):
    service, _store = box

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.get_operator("char_none")
        assert caught.value.code == ErrorCode.ACCOUNT_DATA_NOT_FOUND
        assert caught.value.http_status == 404
        assert caught.value.details == {"char_id": "char_none"}
        # M47：提示语指向唯一的写入通道（森空岛同步），不再让人去跑干员识别。
        assert "森空岛" in caught.value.message

    asyncio.run(scenario())


def test_write_surface_is_gone(box):
    """M47：服务层不再有识别 / 编辑 / 差异三个写入口（docs/13 §8.70）。"""
    service, _store = box

    for name in ("apply_recognition", "patch_operator", "list_diffs", "dismiss_diffs"):
        assert not hasattr(service, name), name
    assert hasattr(service, "apply_skland_sync")


def test_now_matches_the_configured_timezone_contract():
    """哨兵：``NOW`` 是 naive UTC（库内口径，docs/04 §3.2），别被改成 aware。"""
    assert NOW.tzinfo is None
    assert datetime(2026, 10, 10, 3, 0, 0, tzinfo=UTC).replace(tzinfo=None) == NOW
