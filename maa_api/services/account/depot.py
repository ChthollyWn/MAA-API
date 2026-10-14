"""账号仓库（Depot 识别）数据服务（M27，规格 §7.3、§7.5）。

仓库识别是**整表替换**：一次识别把 ``account_depot_item`` 整张表换成这次识别到的物品
与数量（规格 §7.3）。这里读表、渲染 wire、判过期；解析回调数据与物品名映射在
``services/account/capture.py``。数据源只有内核 ``Depot`` 那一趟（M45 的自识别已于
2026-10-14 连实现一起删除，见 `docs/13` §8.68）。

**读侧口径（2026-10-13 用户裁决）**：仓库页只显示**内核识别到的、有明确数量的**物品
（约 77 件）。历史残留的 ``count = NULL`` 行 ＝ 未识别，读侧一律排除（``only_counted``）；
``total`` / ``categories`` 同样按这个口径算。行还在库里，真出现时页面显示「—」而不是 0。

**注意两处刻意保留的记号**：

- ``replace_all`` 的下降护栏数的是**整表行数**（不按数量过滤），这是 M45 W4 的既有语义；
- 迁移 `0021` 让 ``count`` 可空、加了 ``sources``，**不回退迁移**（回退有数据风险）：
  ``count`` 仍可空（防御性，见 :class:`~maa_api.api.routers.account.AccountDepotRow`），
  ``sources`` 列保留为历史（当前没有任何代码写它），读侧不再把它透出。
"""

from __future__ import annotations

import logging

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from maa_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "DepotService",
    "depot_row_wire",
]


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


def depot_row_wire(row: Any, *, source: str | None = None) -> dict[str, Any]:
    """仓库表行 → 对外形状；``count`` 保留 ``None`` ＝ 未识别（防御性，见模块 docstring）。

    ``source`` 从 ``sources`` 列取（``{item_id: ...}``），取不到时留 ``None``；
    接口模型里**没有这个字段**（页面只有内核那一路），保留它是为了 ``sources`` 列
    这个历史列的语义完整：将来若真要重开一条别的来源，读侧不用改就能把来源讲清楚。
    """
    item_id = str(getattr(row, "item_id", ""))
    count = getattr(row, "count", None)
    if count is not None and isinstance(count, bool):
        count = int(count)
    if source is None:
        sources = getattr(row, "sources", None)
        if isinstance(sources, Mapping):
            value = sources.get(item_id)
            source = str(value) if value else None
    return {
        "item_id": item_id,
        "name": getattr(row, "name", None),
        "count": None if count is None else int(count),
        "category": getattr(row, "category", None),
        "recognized_at": _iso(getattr(row, "recognized_at", None)),
        "source": source,
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

        ``total`` 与列表同一个口径：**只数有明确数量的行**（``count IS NULL`` 的自识别
        独有行不算，见模块 docstring）。
        """
        item_repo, recognition_repo = _repositories()
        async with self._session_factory() as session:
            total = await item_repo(session).count(only_counted=True)
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
        """仓库物品列表，带分类与名字筛选、``total`` 与分页（规格 §7.5）。

        ``limit`` / ``offset`` 由本层夹到合法区间；``total`` 是筛选后的总数。
        ``categories`` 是**整份快照**里出现过的分类（按名字排序），与 ``category`` /
        ``query`` 无关：页面用它渲染分类下拉，分页或筛选时不能缩水（规格 §7.3）。

        **只返回有明确数量的行**（``count IS NOT NULL``）：用户 2026-10-13 的口径是
        「仓库页只显示内核识别的、有明确数量的物品」，自识别独有的 ``NULL`` 行读侧一律
        不出现（行还在库里，见模块 docstring）。``total`` 与 ``categories`` 同口径。
        """
        limit = max(int(limit), 1)
        offset = max(int(offset), 0)
        cat = (category or "").strip() or None
        text = (query or "").strip() or None
        item_repo, _recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = item_repo(session)
            total = await repo.count(category=cat, query=text, only_counted=True)
            rows = await repo.list(
                category=cat, query=text, limit=limit, offset=offset, only_counted=True
            )
            categories = await repo.categories(only_counted=True)
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
        detail: Mapping[str, Any] | None = None,
    ) -> int:
        """整表替换仓库数量（规格 §7.3），并记一条 ``depot`` 识别记录。

        **下限护栏（M44）**：条目数低于 ``agent.depot_min_items`` 时**整轮不写**、直接
        返回 0，并记一条 warning。真机实测过没有这道闸的后果：只读到 26 件的那次识别把
        77 行的整表覆盖成 26 行——半截识别不该有「删除权」。``0`` 关闭护栏。

        **下降护栏（M45 W4）**：``agent.depot_min_items`` 防的是「这一轮绝对太少」，
        防不住「上一轮 96 → 本轮 69」这种相对断崖（真机 14:17 那次内核失败后的自动重试
        拿到 69 条，越过了绝对下限，把上一轮完整的 96 行冲掉）。规则：上一轮条目数
        ``≥ agent.depot_drop_min_prev`` 且本轮 ``< 上一轮 × agent.depot_drop_ratio``
        ⇒ **整表不动**、同样返回 0，并记一条 warning + 识别记录的
        ``detail["skipped"]``（``{reason, previous, current, min_prev, ratio, at}``）。
        ``depot_drop_min_prev=0`` 或 ``depot_drop_ratio=0`` 都等于关掉这条规则
        （``ratio=0`` 时判据 ``current < 0`` 恒假）。**这一轮照样写识别记录**（``item_count=0``
        + ``detail.skipped``）：这是要让人事后查得到「为什么表没动」，而表的行内
        ``recognized_at`` 不变 —— 数据时间仍是上一轮，没有被冒充成新的。

        :param items: 每条给 ``item_id``、``name``、``count``，可选 ``category``；
            同一 ``item_id`` 出现多次时取最后一次（识别结果的最后一条为准）。
            ``count`` 允许 ``None`` ＝ 未识别（正常路径不会再写这种行，读侧也会把它们
            过滤掉；``NULL`` 与 ``0`` 不是一回事）。
        :param detail: 写进识别记录的过程事实（当前只有 ``detail.skipped`` 会用到）。
        :returns: 写入的行数（被任一护栏拦下时是 0）。
        """
        stamp = recognized_at or datetime.now(UTC).replace(tzinfo=None)
        rows: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item.get("item_id") or "").strip()
            if not item_id:
                continue
            count = item.get("count")
            # 回调里的数量可能是字符串（"3"）；``None`` ＝ 未识别，原样保留
            # （「不知道有几件」与「有 0 件」不是一回事）。负数归零，识别不出数字按未识别。
            # ``bool`` 是 ``int`` 子类，单独排除，别让 ``True`` 变成 1。
            if count is None:
                normalized: int | None = None
            elif isinstance(count, bool):
                normalized = int(count)
            elif isinstance(count, int):
                normalized = max(count, 0)
            else:
                try:
                    normalized = max(int(str(count).strip()), 0)
                except (TypeError, ValueError):
                    normalized = None
            name = str(item.get("name")).strip() if item.get("name") else ""
            category = str(item.get("category")).strip() if item.get("category") else ""
            rows.append(
                {
                    "item_id": item_id,
                    # 识别没给出名字（或只给了空白）时用 item_id 兜底，页面至少能显示
                    # 一个可辨认的标识；物品名的映射在 capture 层做。
                    "name": name or item_id,
                    "count": normalized,
                    "category": category or None,
                    "recognized_at": stamp,
                }
            )
        agent = self._settings_provider().agent
        min_items = int(getattr(agent, "depot_min_items", 0) or 0)
        if min_items > 0 and len(rows) < min_items:
            # 不写库、也不记识别记录：这一轮什么都没被当成事实。
            logger.warning(
                "仓库识别只读到 %d 件（下限 %d），整轮不写：半截识别不该覆盖整表",
                len(rows),
                min_items,
            )
            return 0
        drop_min_prev = int(getattr(agent, "depot_drop_min_prev", 0) or 0)
        drop_ratio = float(getattr(agent, "depot_drop_ratio", 0.0) or 0.0)

        item_repo, recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = item_repo(session)
            if drop_min_prev > 0 and drop_ratio > 0:
                previous = await repo.count()
                if previous >= drop_min_prev and len(rows) < previous * drop_ratio:
                    logger.warning(
                        "仓库识别本轮 %d 件、上一轮 %d 件（低于上一轮的 %g 倍），整轮不写："
                        "下降护栏拦下，表保持原样（要关掉它把 agent.depot_drop_ratio 设 0）",
                        len(rows),
                        previous,
                        drop_ratio,
                    )
                    payload: dict[str, Any] = dict(detail) if detail else {}
                    payload["skipped"] = {
                        "reason": "drop_guard",
                        "previous": previous,
                        "current": len(rows),
                        "min_prev": drop_min_prev,
                        "ratio": drop_ratio,
                        "at": _iso(stamp),
                    }
                    # 记一条 ``item_count=0`` 的记录：表没动，但要能查出「这一轮为什么没写」。
                    # 行内的 ``recognized_at`` 不变 —— 数据时间仍是上一轮。
                    await recognition_repo(session).record(
                        kind="depot",
                        pipeline_id=pipeline_id,
                        task_id=task_id,
                        client_type=client_type,
                        item_count=0,
                        recognized_at=stamp,
                        detail=payload,
                    )
                    await session.commit()
                    return 0
            written = await repo.replace_all(rows)
            await recognition_repo(session).record(
                kind="depot",
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
                item_count=written,
                recognized_at=stamp,
                detail=detail,
            )
            await session.commit()
        return written


def _is_stale(recognized_at: datetime | None, stale_days: int) -> bool:
    """从未识别算过期（页面要提示「尚未识别」）；否则按天数判。"""
    if recognized_at is None:
        return True
    now = datetime.now(UTC).replace(tzinfo=None)
    return recognized_at <= now - timedelta(days=stale_days)
