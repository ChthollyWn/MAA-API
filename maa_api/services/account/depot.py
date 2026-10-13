"""账号仓库（Depot 识别）数据服务（M27，规格 §7.3、§7.5；M45 W3a 起两路合并）。

仓库识别是**整表替换**：一次识别把 ``account_depot_item`` 整张表换成这次识别到的物品
与数量（规格 §7.3）。这里读表、渲染 wire、判过期；解析回调数据与物品名映射在
``services/account/capture.py``。

**M45 W3a：两路结果按 id 合并**（规格 §4.5）。内核 ``Depot`` 那一趟（95 个材料 + 5 个
硬编码基础物品，``DepotRecognitionTask.cpp:45``）照跑照收，M45 的自识别
（:mod:`maa_api.services.account.depot_scan_service`）把内核白名单外的道具补成差集：

- 并集；同一 id 两边都有时以**内核的数量**为准（它给得出数量，自识别这一波只保证身份；
  数量 OCR 是 W3b 的活）；
- 自识别独有的 id 写 ``count=None`` ＝ 未识别，**绝不写 0**（页面显示「—」）；
- 每一行的来源记在 ``sources``（``kernel`` / ``self_scan``），整轮的过程事实记在
  ``account_recognition.detail``（含「这一轮没覆盖到哪些 id」）。

``account.depot_scan_enabled`` 为假时自识别整块不参与，这条路径与 M27 的实现**逐字等价**
（只写内核那一趟的结果），见 :meth:`DepotService.replace_all` 的 ``item_sources`` 默认值。
"""

from __future__ import annotations

import logging

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from maa_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "DEPOT_FIELDS",
    "DEPOT_SOURCE_KERNEL",
    "DEPOT_SOURCE_SELF_SCAN",
    "DepotMerge",
    "DepotService",
    "depot_row_wire",
    "enumerate_depot_ids",
    "merge_depot_sources",
    "unrecognized_depot_items",
]

#: 一条仓库行对外给出的全部字段（规格 §7.5 的只读列表；M45 W3a 加 ``source``）。
DEPOT_FIELDS: tuple[str, ...] = (
    "item_id",
    "name",
    "count",
    "category",
    "recognized_at",
    "source",
)

#: 来源取值：内核 ``Depot`` 那一趟认出来的（带数量）。
DEPOT_SOURCE_KERNEL = "kernel"
#: 来源取值：M45 自识别补上的（内核白名单外那批；W3a 阶段数量可能未识别）。
DEPOT_SOURCE_SELF_SCAN = "self_scan"

#: 仓库里**可能**出现的道具（``item_index.json`` 的 ``classifyType``）。
#: 用来算「这一轮没识别到哪些 id」：``MEMENTO`` / ``NONE`` 在本机索引里是干员信物一类，
#: 按游戏机制不会出现在仓库页，列进来只会制造噪音。
DEPOT_CATEGORIES: frozenset[str] = frozenset({"MATERIAL", "NORMAL", "CONSUME"})


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


def enumerate_depot_ids(catalog: Mapping[str, Any]) -> tuple[str, ...]:
    """本机 ``item_index.json`` → 「仓库里可能有」的 id（按 id 排序，去重）。

    只认 :data:`DEPOT_CATEGORIES` 三类。这是在**算「未识别」的候选集**，不是白名单：
    没有这条约束时「未识别」会把 200 多件按机制不可能进仓库的东西也算进去。
    """
    ids: set[str] = set()
    for item_id, meta in catalog.items():
        category = getattr(meta, "category", None)
        if isinstance(meta, Mapping):
            category = meta.get("classifyType")
        if str(category or "").strip().upper() in DEPOT_CATEGORIES:
            ids.add(str(item_id))
    return tuple(sorted(ids))


def depot_row_wire(row: Any, *, source: str | None = None) -> dict[str, Any]:
    """仓库表行 → 对外形状；``count`` 保留 ``None`` ＝ 未识别（M45 W3a）。

    ``source`` 从 ``sources`` 列取（``{item_id: ...}``），取不到时留 ``None``：
    0021 之前的行没有来源信息，读侧按「未知」处理，不猜成内核。
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


def unrecognized_depot_items(
    depot_ids: Iterable[str],
    sources: Mapping[str, str] | None,
    *,
    names: Mapping[str, str] | None = None,
    limit: int = 200,
) -> list[dict[str, str | None]]:
    """自识别这一轮**没覆盖到**的 id（＝既没被自识别认出来、也没被内核那一趟带到）。

    M45 W0 定性：``DepotInfo`` 只回 ``{itemId: count}``，**缺失与 0 库存无法区分**；
    对内核白名单外那批道具（``7003/7004/4004/4005/3401/3141`` 之类）而言，「没进这张表」
    只说明「这次没识别到」，不能当成 0。这个函数把那份差集如实列出来，供接口层与页面
    显示「未识别」，而不是渲染成 0。
    """
    if not sources:
        return []
    cap = max(int(limit), 0)
    if cap == 0:
        return []
    known = set(sources)
    listed: list[dict[str, str | None]] = []
    for item_id in sorted(set(depot_ids) - known):
        listed.append({"item_id": item_id, "name": (names or {}).get(item_id)})
        if len(listed) >= cap:
            break
    return listed


@dataclass(frozen=True, slots=True)
class DepotMerge:
    """内核那趟 + 自识别那趟 → 一次写库的输入（纯数据，可离线单测）。

    ``rows`` 直接喂 :meth:`DepotService.replace_all`；``sources`` 是 ``{item_id: 来源}``；
    ``unrecognized`` 是这一轮没覆盖到的 id（接口层据此把「未识别」讲清楚），
    ``unrecognized_items`` 是同一份清单带名字的版本（进 ``detail``，页面直接用）。
    """

    rows: tuple[dict[str, Any], ...]
    sources: Mapping[str, str] = field(default_factory=dict)
    unrecognized: tuple[str, ...] = ()
    unrecognized_items: tuple[dict[str, str | None], ...] = ()

    @property
    def item_count(self) -> int:
        return len(self.rows)

    def detail(self, *, scanned: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """写进 ``account_recognition.detail`` 的过程事实。"""
        payload: dict[str, Any] = {
            "sources": dict(self.sources),
            "unrecognized": [dict(entry) for entry in self.unrecognized_items],
            "unrecognized_total": len(self.unrecognized),
        }
        if scanned:
            payload["scanned"] = dict(scanned)
        return payload


def merge_depot_sources(
    kernel: Mapping[str, int | None] | None,
    scanned: Mapping[str, float] | None,
    *,
    catalog: Mapping[str, Any] | None = None,
    scanned_meta: Mapping[str, Any] | None = None,
    depot_ids: Sequence[str] | None = None,
) -> DepotMerge:
    """两路结果按 id 合并（规格 §4.5）：并集、同一 id 以内核的数量为准、来源逐行记清。

    :param kernel: 内核 ``DepotInfo`` 的 ``{item_id: 数量}``（照跑照收的那一趟）。
    :param scanned: M45 自识别的 ``{item_id: 分数}``（内核白名单外那批的补充）。
    :param catalog: 本机 ``item_index.json``（``{item_id: ItemMeta}`` 或原始 dict），
        用来补名字与分类；读不到就只写 id。
    :param scanned_meta: 自识别那一轮的统计（页数、停止原因等），原样进 ``detail``。
    :param depot_ids: 「可能有」的 id 全集；不传时从 ``catalog`` 推（见
        :func:`enumerate_depot_ids`）。只影响 ``unrecognized`` 的算法范围。

    **谁的数量更可信**：内核。它那一趟是真读数量（``match_quantity`` 的 OCR），
    自识别 W3a 只保证身份（数量 OCR 是 W3b），所以两边都有时取内核的 ``count``、
    来源仍记内核；只有自识别有的 id 写 ``count=None``、来源记 ``self_scan``。
    两边都没有的 id 不写进表（不能凭空造行）。

    内核数量是 ``0`` 时**照写 0**：整表替换的语义是「这次识别到的全部物品」，
    M27 起就把数量为 0 的行也算在内 —— 0 是一个真实的数量事实，与 ``None``（不知道）
    不是一回事，这正是 M45 要分开的两件事。
    """
    names: dict[str, str] = {}
    categories: dict[str, str | None] = {}
    for item_id, meta in (catalog or {}).items():
        name = getattr(meta, "name", None)
        category = getattr(meta, "category", None)
        if isinstance(meta, Mapping):
            name = meta.get("name")
            category = meta.get("classifyType")
        if name:
            names[str(item_id)] = str(name)
        if category:
            categories[str(item_id)] = str(category)

    rows: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    for item_id, count in (kernel or {}).items():
        key = str(item_id)
        rows.append(
            {
                "item_id": key,
                "name": names.get(key),
                "count": None if count is None else int(count),
                "category": categories.get(key),
                "source": DEPOT_SOURCE_KERNEL,
            }
        )
        sources[key] = DEPOT_SOURCE_KERNEL
    for item_id in (scanned or {}):
        key = str(item_id)
        if key in sources:
            continue  # 内核也给了：身份与数量都以内核那趟为准（见 docstring）
        rows.append(
            {
                "item_id": key,
                "name": names.get(key),
                "count": None,  # W3a 只保证身份；数量 OCR 是 W3b
                "category": categories.get(key),
                "source": DEPOT_SOURCE_SELF_SCAN,
            }
        )
        sources[key] = DEPOT_SOURCE_SELF_SCAN

    candidates = tuple(depot_ids) if depot_ids is not None else enumerate_depot_ids(catalog or {})
    listed = tuple(
        unrecognized_depot_items(candidates, sources, names=names)
    )
    return DepotMerge(
        rows=tuple(rows),
        sources=sources,
        unrecognized=tuple(entry["item_id"] for entry in listed if entry.get("item_id")),
        unrecognized_items=listed,
    )


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

        M45 W3a 起每行多一个 ``source``（``kernel`` / ``self_scan`` / ``None``），
        ``count`` 可以是 ``None`` ＝ 未识别。**整份快照**的来源表与「未识别」清单不放
        这里：它们是整轮的事实，与筛选、分页无关，放在 :meth:`depot_state`（同一份
        实现，路由只调这一个入口）。
        """
        return await self.depot_state(category=category, query=query, limit=limit, offset=offset)

    async def depot_state(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """仓库页要的全部事实：分页后的行 + 整份快照的来源 / 未识别 / 覆盖情况。

        - ``items``：按 ``category`` / ``query`` 筛选、分页后的行（每行带 ``source``）；
        - ``sources``：**整份快照**的 ``{item_id: 来源}``（与筛选、分页无关）；
        - ``unrecognized``：这一轮没识别到的 id 清单（``item_id`` + ``name``），
          来自最近一条 ``depot`` 识别记录的 ``detail``；从没跑过自识别时是空表；
        - ``unrecognized_total``：清单长度（清单本身截断到 200 条）；
        - ``coverage``：这一轮的 ``scanned`` 摘要（识别到几种、自识别补了几种、
          自识别有没有跑）。
        """
        limit = max(int(limit), 1)
        offset = max(int(offset), 0)
        cat = (category or "").strip() or None
        text = (query or "").strip() or None
        item_repo, recognition_repo = _repositories()
        async with self._session_factory() as session:
            repo = item_repo(session)
            total = await repo.count(category=cat, query=text)
            rows = await repo.list(category=cat, query=text, limit=limit, offset=offset)
            categories = await repo.categories()
            all_rows = await repo.list()
            latest = await recognition_repo(session).latest("depot")
        sources = _sources_of(all_rows)
        detail = getattr(latest, "detail", None)
        detail = detail if isinstance(detail, Mapping) else {}
        listed = detail.get("unrecognized")
        unrecognized = [entry for entry in listed if isinstance(entry, Mapping)] if isinstance(
            listed, list
        ) else []
        coverage = {
            "total": len(all_rows),
            "kernel": sum(1 for value in sources.values() if value == DEPOT_SOURCE_KERNEL),
            "self_scan": sum(1 for value in sources.values() if value == DEPOT_SOURCE_SELF_SCAN),
            "unrecognized": int(detail.get("unrecognized_total") or len(unrecognized)),
            "scanned": dict(detail.get("scanned") or {}) if isinstance(
                detail.get("scanned"), Mapping
            ) else {},
        }
        return {
            "items": [depot_row_wire(row, source=sources.get(str(getattr(row, "item_id", "")))) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "categories": categories,
            "sources": dict(sources),
            "unrecognized": unrecognized,
            "unrecognized_total": coverage["unrecognized"],
            "coverage": coverage,
        }

    async def replace_all(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        recognized_at: datetime | None = None,
        pipeline_id: str | None = None,
        task_id: str | None = None,
        client_type: str | None = None,
        item_sources: Mapping[str, str] | None = None,
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
            ``count`` 允许 ``None`` ＝ 未识别（M45 W3a：自识别只保证身份）。
        :param item_sources: ``{item_id: "kernel" | "self_scan"}``（M45 W3a）。
            **不传时整列留 NULL**：这是「内核那一趟照跑照收」的既有路径（自识别未启用、
            或旧调用方），行为与 M27 完全一致。
        :param detail: 写进识别记录的过程事实（来源表、未识别清单、逐屏计数）。
        :returns: 写入的行数（被任一护栏拦下时是 0）。
        """
        stamp = recognized_at or datetime.now(UTC).replace(tzinfo=None)
        rows: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item.get("item_id") or "").strip()
            if not item_id:
                continue
            count = item.get("count")
            # 回调里的数量可能是字符串（"3"）；``None`` ＝ 未识别，原样保留（M45 W3a：
            # 「不知道有几件」与「有 0 件」不是一回事）。负数归零，识别不出数字按未识别。
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
            source = str(item.get("source") or "").strip() or (
                str(item_sources.get(item_id) or "").strip() if item_sources else ""
            )
            rows.append(
                {
                    "item_id": item_id,
                    # 识别没给出名字（或只给了空白）时用 item_id 兜底，页面至少能显示
                    # 一个可辨认的标识；物品名的映射在 capture 层做。
                    "name": name or item_id,
                    "count": normalized,
                    "category": category or None,
                    "recognized_at": stamp,
                    **({"source": source} if source else {}),
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


def _sources_of(rows: Iterable[Any]) -> dict[str, str]:
    """仓库行 → ``{item_id: 来源}``（旧行的 ``sources`` 是 NULL，直接跳过）。

    来源存在每一行的 ``sources`` JSON 里（值都一样），读的时候按行取自己的那一项：
    这样即使将来一行一个来源地混写也不会读串。
    """
    sources: dict[str, str] = {}
    for row in rows:
        item_id = str(getattr(row, "item_id", ""))
        payload = getattr(row, "sources", None)
        if not item_id or not isinstance(payload, Mapping):
            continue
        value = payload.get(item_id)
        if value:
            sources[item_id] = str(value)
    return sources


def _is_stale(recognized_at: datetime | None, stale_days: int) -> bool:
    """从未识别算过期（页面要提示「尚未识别」）；否则按天数判。"""
    if recognized_at is None:
        return True
    now = datetime.now(UTC).replace(tzinfo=None)
    return recognized_at <= now - timedelta(days=stale_days)
