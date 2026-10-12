"""账号仓库（Depot 识别）数据服务（M27，规格 §7.3、§7.5）。

仓库识别是**整表替换**：一次 ``DepotInfo``（``done=true``）把 ``account_depot_item``
整张表换成这次识别到的物品与数量（规格 §7.3）。这里读表、渲染 wire、判过期；
解析回调数据与物品名映射在 ``services/account/capture.py``。
"""

from __future__ import annotations

import logging

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from maa_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = ["DEPOT_FIELDS", "DepotService", "depot_row_wire"]

#: 一条仓库行对外给出的全部字段（规格 §7.5 的只读列表）。
DEPOT_FIELDS: tuple[str, ...] = ("item_id", "name", "count", "category", "recognized_at")


def _repositories() -> tuple[Any, Any]:
    """延迟取两个仓储类（同 ``box.py``：模块级不 import ``db.repositories``）。"""
    from maa_api.db.repositories import account as repositories

    return (repositories.AccountDepotItemRepository, repositories.AccountRecognitionRepository)


def _iso(value: datetime | None) -> str | None:
    """UTC 时间 → ``...Z``；naive 按 UTC 解释（库内统一 naive UTC，docs/04 §3.2）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def depot_row_wire(row: Any) -> dict[str, Any]:
    """仓库表行 → 对外形状；``count`` 归一成非负整数。"""
    count = getattr(row, "count", 0)
    return {
        "item_id": str(getattr(row, "item_id", "")),
        "name": getattr(row, "name", None),
        "count": int(count) if isinstance(count, int) and not isinstance(count, bool) else 0,
        "category": getattr(row, "category", None),
        "recognized_at": _iso(getattr(row, "recognized_at", None)),
    }


class DepotService:
    """账号仓库数据服务（规格 §7.3、§7.5）。

    :param session_factory: ``async_sessionmaker`` 或返回 ``AsyncSession`` 的可调用
        对象；写方法自己定事务边界。
    :param settings_provider: 现读配置，用于过期阈值 ``agent.depot_stale_days``。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider

    async def summary(self) -> dict[str, Any]:
        """仓库概要：物品种类数、识别时间与是否过期（规格 §7.5）。

        从未识别时 ``recognized_at`` 为 ``None``、``stale`` 为 ``True``：页面与注入段
        据此提示「尚未识别，建议运行仓库识别」。
        """
        item_repo, recognition_repo = _repositories()
        async with self._session_factory() as session:
            total = await item_repo(session).count()
            recognized_at = await recognition_repo(session).last_at("depot")
        stale_days = int(self._settings_provider().agent.depot_stale_days)
        return {
            "total": total,
            "recognized_at": _iso(recognized_at),
            "stale": _is_stale(recognized_at, stale_days),
            "stale_days": stale_days,
        }

    async def list_items(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """仓库物品列表，带分类与名字筛选、``total`` 与分页。

        ``limit`` / ``offset`` 由本层夹到合法区间；``total`` 是筛选后的总数。
        ``categories`` 是**整份快照**里出现过的分类（按名字排序），与 ``category`` /
        ``query`` 无关：页面用它渲染分类下拉，分页或筛选时不能缩水（规格 §7.3）。
        """
        limit = max(int(limit), 1)
        offset = max(int(offset), 0)
        cat = (category or "").strip() or None
        text = (query or "").strip() or None
        item_repo, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = item_repo(session)
            total = await repo.count(category=cat, query=text)
            rows = await repo.list(category=cat, query=text, limit=limit, offset=offset)
            categories = await repo.categories()
        return {
            "items": [depot_row_wire(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "categories": categories,
        }

    async def replace_all(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        recognized_at: datetime | None = None,
        pipeline_id: str | None = None,
        task_id: str | None = None,
        client_type: str | None = None,
    ) -> int:
        """整表替换仓库数量（规格 §7.3），并记一条 ``depot`` 识别记录。

        **下限护栏（M44）**：条目数低于 ``agent.depot_min_items`` 时**整轮不写**、直接
        返回 0，并记一条 warning。真机实测过没有这道闸的后果：只读到 26 件的那次识别把
        77 行的整表覆盖成 26 行——半截识别不该有「删除权」。``0`` 关闭护栏。

        :param items: 每条给 ``item_id``、``name``、``count``，可选 ``category``；
            同一 ``item_id`` 出现多次时取最后一次（识别结果的最后一条为准）。
        :returns: 写入的行数（被护栏拦下时是 0）。
        """
        stamp = recognized_at or datetime.now(UTC).replace(tzinfo=None)
        rows: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item.get("item_id") or "").strip()
            if not item_id:
                continue
            count = item.get("count")
            # 回调里的数量可能是字符串（"3"）；负数归零，识别不出数字按 0。``bool``
            # 是 ``int`` 子类，单独排除，别让 ``True`` 变成 1。
            if isinstance(count, bool) or not isinstance(count, int):
                try:
                    count = int(str(count).strip())
                except (TypeError, ValueError):
                    count = 0
            name = str(item.get("name")).strip() if item.get("name") else ""
            category = str(item.get("category")).strip() if item.get("category") else ""
            rows.append(
                {
                    "item_id": item_id,
                    # 识别没给出名字（或只给了空白）时用 item_id 兜底，页面至少能显示
                    # 一个可辨认的标识；物品名的映射在 capture 层做。
                    "name": name or item_id,
                    "count": max(int(count), 0),
                    "category": category or None,
                    "recognized_at": stamp,
                }
            )
        min_items = int(getattr(self._settings_provider().agent, "depot_min_items", 0) or 0)
        if min_items > 0 and len(rows) < min_items:
            # 不写库、也不记识别记录：这一轮什么都没被当成事实。
            logger.warning(
                "仓库识别只读到 %d 件（下限 %d），整轮不写：半截识别不该覆盖整表",
                len(rows),
                min_items,
            )
            return 0

        item_repo, recognition_repo = _repositories()
        async with self._session_factory() as session:
            written = await item_repo(session).replace_all(rows)
            await recognition_repo(session).record(
                kind="depot",
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
                item_count=written,
                recognized_at=stamp,
            )
            await session.commit()
        return written


def _is_stale(recognized_at: datetime | None, stale_days: int) -> bool:
    """从未识别算过期（页面要提示「尚未识别」）；否则按天数判。"""
    if recognized_at is None:
        return True
    now = datetime.now(UTC).replace(tzinfo=None)
    return recognized_at <= now - timedelta(days=stale_days)
