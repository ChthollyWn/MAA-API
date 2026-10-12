"""仓库（Depot）服务的逻辑契约（M27 规格 §7.3、§7.5）：用假仓储驱动真实服务代码。

``test_account_depot.py`` 是同一个服务的 SQLite 集成版本；本文件不碰数据库，把
``AccountDepotItemRepository`` 与 ``AccountRecognitionRepository`` 换成内存替身，
验证整表替换语义、筛选与分页、概要取「识别记录」而不是「行的最大值」、过期阈值。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maa_api.settings import Settings

from maa_api.services.account import depot as depot_module  # noqa: E402
from maa_api.services.account.depot import DepotService  # noqa: E402

#: 基时刻取「当前整点」而不是写死日期：``_is_stale`` 读的是真实时钟，写死日期一旦跨过
#: 「基准日 + 过期天数」就会把「两天前的记录」判成过期（2026-10-11 起这两条用例真红过）。
NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)


def _utc(*, days_ago: int = 0) -> datetime:
    return NOW - timedelta(days=days_ago)


def _iso(moment: datetime) -> str:
    """与 ``DepotService`` 出参同款的时间字面量（naive UTC + ``Z``）。"""
    return moment.isoformat() + "Z"


def _settings(*, stale_days: int, min_items: int = 0) -> Settings:
    """用例配置。

    本文件只写 1–5 件的小表，测的是整表替换 / 筛选分页 / 概要语义，不是 M44 的下限
    护栏，所以 ``depot_min_items`` 默认置 0（显式关护栏）；护栏本身由
    ``test_account_depot.py::test_min_items_guard_blocks_a_partial_recognition`` 覆盖。
    """
    settings = Settings()
    settings.agent.depot_stale_days = stale_days
    settings.agent.depot_min_items = min_items
    return settings


class _Item:
    """一条仓库行：属性名与 ``account_depot_item`` 的列同名。"""

    def __init__(self, **fields: Any) -> None:
        self.item_id = ""
        self.name: str | None = None
        self.count = 0
        self.category: str | None = None
        self.recognized_at: datetime | None = None
        for key, value in fields.items():
            setattr(self, key, value)


def _make_fakes(store: dict[str, Any]) -> dict[str, Any]:
    class AccountDepotItemRepository:
        def __init__(self, session: Any = None) -> None:
            self.session = session

        async def replace_all(self, rows: list[dict[str, Any]]) -> int:
            store["items"] = {row["item_id"]: _Item(**row) for row in rows}
            return len(store["items"])

        def _filtered(
            self, *, category: str | None, query: str | None
        ) -> list[_Item]:
            rows = sorted(store["items"].values(), key=lambda item: item.item_id)
            if category:
                rows = [row for row in rows if row.category == category]
            if query:
                needle = query.casefold()
                rows = [
                    row
                    for row in rows
                    if needle in (row.name or "").casefold()
                    or needle in row.item_id.casefold()
                ]
            return rows

        async def count(
            self, *, category: str | None = None, query: str | None = None
        ) -> int:
            return len(self._filtered(category=category, query=query))

        async def list(
            self,
            *,
            category: str | None = None,
            query: str | None = None,
            limit: int = 100,
            offset: int = 0,
        ) -> list[_Item]:
            rows = self._filtered(category=category, query=query)
            return rows[offset : offset + limit]

        async def categories(self) -> list[str]:
            # 真仓储取的是整份快照的 distinct 分类（与筛选无关），这里同样不接筛选参数。
            return sorted(
                {
                    str(row.category).strip()
                    for row in store["items"].values()
                    if str(row.category or "").strip()
                }
            )

    class AccountRecognitionRepository:
        def __init__(self, session: Any = None) -> None:
            self.session = session

        async def record(self, **fields: Any) -> dict[str, Any]:
            store["recognitions"].append(fields)
            return fields

        async def last_at(self, kind: str) -> datetime | None:
            stamps = [
                row["recognized_at"]
                for row in store["recognitions"]
                if row["kind"] == kind
            ]
            return max(stamps) if stamps else None

    return {
        "AccountDepotItemRepository": AccountDepotItemRepository,
        "AccountRecognitionRepository": AccountRecognitionRepository,
    }


@pytest.fixture
def depot(monkeypatch):
    """``(service, store, factory)``：真实服务 + 假仓储。"""
    store: dict[str, Any] = {"items": {}, "recognitions": []}
    fakes = _make_fakes(store)
    monkeypatch.setattr(
        depot_module,
        "_repositories",
        lambda: (
            fakes["AccountDepotItemRepository"],
            fakes["AccountRecognitionRepository"],
        ),
    )

    class _Session:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    @asynccontextmanager
    async def session_factory():
        yield _Session()

    service = DepotService(
        session_factory, settings_provider=lambda: _settings(stale_days=3)
    )
    return service, store, session_factory


def test_replace_all_swaps_the_whole_table(depot):
    """显式关掉下限护栏（``_settings`` 默认 ``min_items=0``）后验整表替换语义。"""
    service, _store, _factory = depot

    async def scenario():
        assert (
            await service.replace_all(
                [
                    {"item_id": "30012", "name": "聚酸酯组", "count": 3, "category": "材料"},
                    {"item_id": "30013", "name": "聚酸酯块", "count": 5, "category": "材料"},
                ],
                recognized_at=NOW,
            )
            == 2
        )
        first = await service.list_items()
        assert first["total"] == 2
        assert [item["item_id"] for item in first["items"]] == ["30012", "30013"]
        assert first["items"][0]["recognized_at"] == _iso(NOW)

        # 第二次识别里没有 30012 → 整表替换后它必须消失（不是合并）。
        assert (
            await service.replace_all(
                [{"item_id": "30013", "name": "聚酸酯块", "count": 9, "category": "材料"}],
                recognized_at=NOW,
            )
            == 1
        )
        second = await service.list_items()
        assert second["total"] == 1
        assert second["items"][0]["count"] == 9

        # 识别到空仓库同样合法：整表清空（关着护栏时，空表是「真空仓库」的表达）。
        assert await service.replace_all([], recognized_at=NOW) == 0
        assert (await service.list_items())["total"] == 0

    asyncio.run(scenario())


def test_replace_all_normalizes_rows(depot):
    """缺 name 用 item_id 兜底、负数量归零、空 item_id 丢弃、重复 id 取最后一条。"""
    service, _store, _factory = depot

    async def scenario():
        written = await service.replace_all(
            [
                {"item_id": "30012", "count": 3},
                {"item_id": "30012", "count": 4},
                {"item_id": "", "count": 5},
                {"item_id": "30013", "name": " ", "count": -2},
            ],
            recognized_at=NOW,
        )
        assert written == 2
        items = {item["item_id"]: item for item in (await service.list_items())["items"]}
        assert items["30012"]["name"] == "30012"
        assert items["30012"]["count"] == 4
        assert items["30013"]["count"] == 0
        assert items["30013"]["name"] == "30013"

    asyncio.run(scenario())


def test_list_items_filters_by_category_and_name_with_pagination(depot):
    service, _store, _factory = depot

    async def scenario():
        await service.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3, "category": "材料"},
                {"item_id": "30013", "name": "聚酸酯块", "count": 5, "category": "材料"},
                {"item_id": "3231", "name": "技巧概要·卷1", "count": 40, "category": "技巧概要"},
            ],
            recognized_at=NOW,
        )
        assert (await service.list_items(category="材料"))["total"] == 2
        assert (await service.list_items(query="聚酸酯"))["total"] == 2
        assert (await service.list_items(category="材料", query="块"))["total"] == 1
        assert (await service.list_items(category="  "))["total"] == 3
        assert (await service.list_items(category="不存在"))["total"] == 0

        page = await service.list_items(limit=1, offset=1)
        assert page["total"] == 3
        assert page["limit"] == 1 and page["offset"] == 1
        assert len(page["items"]) == 1

        clamped = await service.list_items(limit=0, offset=-3)
        assert clamped["limit"] == 1 and clamped["offset"] == 0

    asyncio.run(scenario())


def test_list_items_reports_every_category_of_the_snapshot(depot):
    """分类下拉的取值来自整份快照：不受 category / query 筛选与分页影响。"""
    service, _store, _factory = depot

    async def scenario():
        await service.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3, "category": "材料"},
                {"item_id": "30013", "name": "聚酸酯块", "count": 5, "category": "材料"},
                {"item_id": "3231", "name": "技巧概要·卷1", "count": 40, "category": "技巧概要"},
                {"item_id": "30061", "name": "无分类物品", "count": 1},
                {"item_id": "30062", "name": "空白分类", "count": 1, "category": "  "},
            ],
            recognized_at=NOW,
        )

        assert (await service.list_items())["categories"] == ["技巧概要", "材料"]
        # 筛选与分页都不改变这份清单——页面的分类下拉靠它。
        assert (await service.list_items(category="材料"))["categories"] == ["技巧概要", "材料"]
        assert (await service.list_items(query="聚酸酯", limit=1))["categories"] == [
            "技巧概要",
            "材料",
        ]

        # 整表清空后清单也空（不是把历史分类留着）。
        assert await service.replace_all([], recognized_at=NOW) == 0
        assert (await service.list_items())["categories"] == []

    asyncio.run(scenario())


def test_summary_uses_the_recognition_record_and_threshold(depot):
    service, _store, factory = depot

    async def scenario():
        # 从未识别：recognized_at 为空、stale 为真（页面提示「尚未识别」）。
        assert await service.summary() == {
            "total": 0,
            "recognized_at": None,
            "stale": True,
            "stale_days": 3,
        }

        await service.replace_all(
            [{"item_id": "30012", "name": "聚酸酯组", "count": 3}],
            recognized_at=_utc(days_ago=2),
        )
        summary = await service.summary()
        assert summary == {
            "total": 1,
            "recognized_at": _iso(_utc(days_ago=2)),
            "stale": False,
            "stale_days": 3,
        }

        strict = DepotService(
            factory, settings_provider=lambda: _settings(stale_days=1)
        )
        assert (await strict.summary())["stale"] is True

    asyncio.run(scenario())


def test_replace_all_writes_a_depot_recognition_record(depot):
    service, store, _factory = depot

    async def scenario():
        await service.replace_all(
            [{"item_id": "30012", "name": "聚酸酯组", "count": 3}],
            recognized_at=NOW,
            pipeline_id="p1",
            task_id="t1",
            client_type="Bilibili",
        )
        assert store["recognitions"] == [
            {
                "kind": "depot",
                "pipeline_id": "p1",
                "task_id": "t1",
                "client_type": "Bilibili",
                "item_count": 1,
                "recognized_at": NOW,
            }
        ]

    asyncio.run(scenario())


def test_summary_reflects_the_current_table_only(depot):
    """多次识别之间概要不累加历史条数；识别时间取最新的那条记录。"""
    service, _store, _factory = depot

    async def scenario():
        await service.replace_all(
            [
                {"item_id": "30012", "name": "聚酸酯组", "count": 3},
                {"item_id": "30013", "name": "聚酸酯块", "count": 5},
            ],
            recognized_at=NOW,
        )
        assert (await service.summary())["total"] == 2

        await service.replace_all(
            [{"item_id": "30012", "name": "聚酸酯组", "count": 1}],
            recognized_at=NOW + timedelta(hours=1),
        )
        summary = await service.summary()
        assert summary["total"] == 1
        assert summary["recognized_at"] == _iso(NOW + timedelta(hours=1))

    asyncio.run(scenario())
