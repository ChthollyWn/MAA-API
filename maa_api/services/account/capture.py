"""MaaCore 账号识别回调的采集（规格 §7.2 T2、§7.3）。

``OperBox`` / ``Depot`` 的结果通过 ``SubTaskExtraInfo`` 推送，过程中会发多次
``done=false``，只有最后一次 ``done=true`` 才是收尾（规格 §2.5）：

- ``OperBoxInfo``：``all_opers[]``（id、name、rarity、own）更新「是否拥有」，
  ``own_opers[]``（外加 elite 0–2、level、potential 1–6）更新精英化 / 等级 / 潜能；
  识别不动技能等级、专精、模组（那是手动或 Agent 的字段）。
- ``DepotInfo``：``data`` 是 JSON 字符串 ``{"物品ID": 数量}``，**整表替换**仓库数量；
  物品名与本机分类用 ``item_index.json`` 映射，映射不到的保留 id。

两条回调的解析与物品名/职业映射在本模块（纯函数 + 一次磁盘读，可单测），
落库与合并规则交给 :class:`~maa_api.services.account.box.OperatorBoxService` 与
:class:`~maa_api.services.account.depot.DepotService`：合并规则（覆盖哪些字段、
什么时候记差异）只有一份实现，就在服务层。本模块额外把「流水线 / 任务 / 渠道」
补进识别记录（服务层拿不到这三个值，见
:meth:`~maa_api.db.repositories.account.AccountRecognitionRepository.record`）。

**采集失败绝不影响流水线回调**：:meth:`AccountCaptureService.handle_callback` 吞掉
所有异常，只记一条 warning 并返回带 ``skipped`` / ``error`` 的结果；调用方按日志
处理即可。接线（LogHub 刷盘路径）：

.. code-block:: python

    capture = AccountCaptureService(db_session.session_factory)
    await capture.handle_callback(
        (record.raw or {}).get("details"),
        message=(record.raw or {}).get("msg"),
        created_at=datetime.fromtimestamp(record.ts, timezone.utc).replace(tzinfo=None),
        pipeline_id=record.pipeline_id,
        task_id=record.task_id,
    )

``created_at`` 统一是库内口径的 naive UTC（docs/04 §3.2）；``client_type`` 由接线处
从任务的生效参数取（取不到就留空，不影响落库）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from maa_api.core.enums import Message
from maa_api.db.models import utcnow
from maa_api.db.repositories.account import (
    RECOGNITION_KIND_DEPOT,
    RECOGNITION_KIND_OPERBOX,
    AccountRecognitionRepository,
)
# M45 W3a 的两路合并是纯函数（不碰库、不碰设备），放在这里做：采集层已经拿得到
# 内核那一趟的数据与本机物品索引，服务层只负责把它写下去。
from maa_api.services.account.depot import enumerate_depot_ids, merge_depot_sources
from maa_api.services.resource_index import iter_resource_roots

__all__ = [
    "AccountCaptureResult",
    "AccountCaptureService",
    "ItemMeta",
    "OperBoxSnapshot",
    "OperatorMeta",
    "load_item_catalog",
    "load_item_icons",
    "load_operator_catalog",
    "parse_depot_data",
    "parse_operbox",
]

logger = logging.getLogger(__name__)

#: 两个回调的 ``what``（规格 §7.3）。
OPERBOX_WHAT = "OperBoxInfo"
DEPOT_WHAT = "DepotInfo"

#: 本机资源文件名（与 ``services/game/items.py``、``services/game/operators.py`` 同源）。
_ITEM_INDEX = Path("item_index.json")
_BATTLE_DATA = Path("battle_data.json")

#: 本机职业名（battle_data 的 ``WARRIOR``）→ 中文（近卫）。与
#: ``services/game/operators.py`` 的 ``_profession_cn``、前端 ``format.ts`` 同一份映射：
#: 账号表存中文（REST 的 ``profession`` 查询参数按中文描述），未知值原样保留。
_PROFESSION_CN: dict[str, str] = {
    "WARRIOR": "近卫",
    "SNIPER": "狙击",
    "CASTER": "术师",
    "MEDIC": "医疗",
    "SUPPORT": "辅助",
    "TANK": "重装",
    "SPECIAL": "特种",
    "PIONEER": "先锋",
}

#: 识别能覆盖的字段与它们的取值范围（规格 §7.3 / §7.5）。越界的值不写：宁可不覆盖，
#: 也不往表里落一个假的练度。
_ELITE_RANGE = (0, 2)
_LEVEL_RANGE = (1, 90)
_POTENTIAL_RANGE = (1, 6)
_RARITY_RANGE = (1, 6)


@dataclass(frozen=True, slots=True)
class ItemMeta:
    """本机 ``item_index.json`` 的一条物品。"""

    name: str | None = None
    category: str | None = None
    #: 图标文件名（``3003.png``）；M44 的道具图代理靠它拼上游路径。
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class OperatorMeta:
    """本机 ``battle_data.json`` 的一条干员（识别回调给不出职业，用这里补）。"""

    name: str | None = None
    rarity: int | None = None
    profession: str | None = None
    #: 拉丁名（详情页左下角那一行的正体，M40 用它核对详情页里是谁）。``battle_data`` 里
    #: 有个别干员的 ``name_en`` 直接写着中文（如「克莱门莎」），这类当作没有拉丁名。
    name_en: str | None = None


@dataclass(frozen=True, slots=True)
class OperBoxSnapshot:
    """一次 ``OperBoxInfo`` 里某名干员的识别值；``None`` 表示这次没识别该字段。"""

    char_id: str
    name: str | None = None
    rarity: int | None = None
    own: bool | None = None
    elite: int | None = None
    level: int | None = None
    potential: int | None = None
    #: 这个 id 出现在 ``all_opers`` 里（是否拥有由它说话）。
    listed_in_all: bool = False


@dataclass(frozen=True, slots=True)
class AccountCaptureResult:
    """一次采集的结果：写没写、写了多少、为什么没写。

    ``skipped`` 取值：``not_subtask_extra_info``（不是这条回调）、``unsupported``
    （别的 ``what``）、``incomplete``（``done=false`` 的过程回调）、``bad_payload``
    （结构不符或坏 JSON）、``no_owned_operators``（空识别保护：一个已拥有干员都没识别
    到，M33 护栏 G1）、``error``（落库时抛了异常，已记 warning）。
    """

    kind: str | None = None
    written: bool = False
    skipped: str | None = None
    item_count: int = 0
    diff_count: int = 0
    #: 收缩保护留下的「已拥有」条数（M33 护栏 G2）。
    kept_count: int = 0
    error: str | None = None


class AccountCaptureService:
    """把账号识别的 ``SubTaskExtraInfo`` 落库（规格 §7.2 T2）。

    :param session_factory: ``async_sessionmaker``；落库与写识别记录各开一个会话。
    :param item_catalog: ``itemId → ItemMeta`` 替身，测试注入；默认读本机
        ``item_index.json``（读不到就只保留 id）。
    :param operator_catalog: ``char_id → OperatorMeta`` 替身，测试注入；默认读本机
        ``battle_data.json``（读不到就没有职业）。
    :param box_service / depot_service: 落库服务替身，测试注入；默认按
        ``session_factory`` 现建（构造签名见各自模块）。
    :param depot_scan: 仓库自识别服务（``DepotScanService`` 或同签名替身），M45 的
        补差集链路。**2026-10-13 起按用户要求关停并下线**（见 `docs/13` §8.68）：设置项
        ``account.depot_scan_enabled`` 默认 false，关着时 ``scan_all_pages`` 抛
        ``DepotScanDisabled``，采集层按「这一轮没有自识别」处理，走只写内核 ``Depot``
        那一趟的路径 —— 与 M44 之前**逐字一致**，绝不因此不写内核的结果。代码保留作备档，
        用户明确打开设置项时这条链路才参与写库。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        item_catalog: Mapping[str, ItemMeta] | None = None,
        operator_catalog: Mapping[str, OperatorMeta] | None = None,
        box_service: Any | None = None,
        depot_service: Any | None = None,
        depot_scan: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._item_catalog = item_catalog
        self._operator_catalog = operator_catalog
        self._box_service = box_service
        self._depot_service = depot_service
        self._depot_scan = depot_scan

    # -- 入口 -------------------------------------------------------------
    async def handle_callback(
        self,
        callback: Any,
        *,
        message: Any,
        created_at: datetime | None = None,
        pipeline_id: str | None = None,
        task_id: str | None = None,
        client_type: str | None = None,
    ) -> AccountCaptureResult:
        """处理一条回调；**任何异常都不会抛给调用方**。

        :param callback: ``{"what": ..., "details": {...}}`` 信封（LogRecord.raw 里的
            ``details``）。
        :param message: 回调的 ``Message`` 值（int 或枚举）。
        :param created_at: 识别事件时间（naive UTC）；缺省取库内时间源。
        :param pipeline_id / task_id / client_type: 识别记录的上下文，可缺省。
        """
        what = ""
        try:
            if isinstance(callback, Mapping):
                what = str(callback.get("what") or "")
            return await self._handle(
                callback,
                message=message,
                created_at=created_at or utcnow(),
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
            )
        except Exception as exc:  # noqa: BLE001 - 采集失败不能影响流水线回调
            logger.warning(
                "账号数据采集失败（what=%s）：%r", what or "?", exc, exc_info=True
            )
            return AccountCaptureResult(
                kind=_kind_of(what), skipped="error", error=repr(exc)
            )

    # -- 分发 -------------------------------------------------------------
    async def _handle(
        self,
        callback: Any,
        *,
        message: Any,
        created_at: datetime,
        pipeline_id: str | None,
        task_id: str | None,
        client_type: str | None,
    ) -> AccountCaptureResult:
        if not _is_extra_info(message):
            return AccountCaptureResult(skipped="not_subtask_extra_info")
        if not isinstance(callback, Mapping):
            return AccountCaptureResult(skipped="bad_payload")
        what = str(callback.get("what") or "")
        if what not in (OPERBOX_WHAT, DEPOT_WHAT):
            return AccountCaptureResult(skipped="unsupported")
        data = callback.get("details")
        if not isinstance(data, Mapping):
            return AccountCaptureResult(kind=_kind_of(what), skipped="bad_payload")
        if data.get("done") is not True:
            # done=false 是过程回调：只在收尾时写入（规格 §7.3）。
            return AccountCaptureResult(kind=_kind_of(what), skipped="incomplete")
        context = {
            "created_at": created_at,
            "pipeline_id": pipeline_id,
            "task_id": task_id,
            "client_type": client_type,
        }
        if what == OPERBOX_WHAT:
            return await self._capture_operbox(data, **context)
        return await self._capture_depot(data, **context)

    # -- OperBox ----------------------------------------------------------
    async def _capture_operbox(
        self,
        data: Mapping[str, Any],
        *,
        created_at: datetime,
        pipeline_id: str | None,
        task_id: str | None,
        client_type: str | None,
    ) -> AccountCaptureResult:
        snapshots = parse_operbox(data)
        if not snapshots:
            return AccountCaptureResult(kind=RECOGNITION_KIND_OPERBOX, skipped="bad_payload")

        # 空识别（M33 护栏 G1，规格 §4.3）：游戏没启动时内核照样发 ``done=true``，只是
        # ``all_opers`` 里一个 ``own=true`` 都没有（实测 16:26–16:29 那四次）。这种载荷
        # 落库会把整表标成「未拥有」——比留着旧数据危险得多，直接跳过、只记一条 warning。
        operators = _operators_payload(snapshots, self._operators)
        all_ids = [item.char_id for item in snapshots if item.listed_in_all]
        owned = sum(1 for item in operators.values() if item.get("own"))
        if owned == 0:
            logger.warning(
                "干员识别没有识别到任何已拥有干员（all_opers %d 条），本次不更新账号数据"
                "（M33 空识别保护）；请确认游戏已启动并停在干员列表页",
                len(all_ids),
            )
            return AccountCaptureResult(
                kind=RECOGNITION_KIND_OPERBOX, skipped="no_owned_operators"
            )

        result = await self._box.apply_recognition(
            operators,
            all_operators=all_ids,
            now=created_at,
            recognized_at=created_at,
            # 收缩保护（M33 护栏 G2，规格 §4.3，用户裁决「识别只加不减」）：识别没看到
            # 的已拥有干员保留为已拥有，只记日志提示；取消拥有走手动 / Agent 编辑。
            keep_owned=True,
        )
        result = result or {}
        kept_owned = [str(char_id) for char_id in result.get("kept_owned") or ()]
        if kept_owned:
            names = _operator_names(snapshots)
            logger.warning(
                "干员识别少了 %d 名库里已拥有的干员（%s%s），已按「识别只加不减」保留为"
                "已拥有；确实不再拥有时请用 update_operator_box 修改",
                len(kept_owned),
                "、".join(names.get(char_id, char_id) for char_id in kept_owned[:5]),
                " 等" if len(kept_owned) > 5 else "",
            )
        await self._record(
            RECOGNITION_KIND_OPERBOX,
            item_count=owned,
            recognized_at=created_at,
            pipeline_id=pipeline_id,
            task_id=task_id,
            client_type=client_type,
        )
        return AccountCaptureResult(
            kind=RECOGNITION_KIND_OPERBOX,
            written=True,
            item_count=owned,
            diff_count=int(result.get("diffs") or 0),
            kept_count=len(kept_owned),
        )

    # -- Depot ------------------------------------------------------------
    async def _capture_depot(
        self,
        data: Mapping[str, Any],
        *,
        created_at: datetime,
        pipeline_id: str | None,
        task_id: str | None,
        client_type: str | None,
    ) -> AccountCaptureResult:
        counts = parse_depot_data(data)
        if not counts:
            # 坏 JSON / 一个有效条目都没有：**不整表替换**。仓库识别失败时清空账号
            # 数据比留着旧数据危险得多，旧数据与旧识别时间都原样保留。
            return AccountCaptureResult(kind=RECOGNITION_KIND_DEPOT, skipped="bad_payload")

        # 自识别那一趟（未启用 / 不可用 / 失败都是 None）。有它才走两路合并；没有它时
        # 走下面那条**逐字保留 M44 形态**的路径：不跑合并纯函数、不给 ``item_sources``
        # 与 ``detail``，写出去的行只来自内核 ``DepotInfo``。
        scanned, scanned_detail = await self._scan_depot()
        catalog = self._items
        if scanned is None:
            items = [
                {
                    "item_id": item_id,
                    "name": (meta.name if meta else None),
                    "count": count,
                    "category": (meta.category if meta else None),
                }
                for item_id, count in counts.items()
                for meta in (catalog.get(item_id),)
            ]
            written = await self._depot.replace_all(
                items,
                recognized_at=created_at,
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
            )
        else:
            merged = merge_depot_sources(
                counts,
                scanned,
                catalog=catalog,
                depot_ids=enumerate_depot_ids(catalog),
            )
            written = await self._depot.replace_all(
                merged.rows,
                recognized_at=created_at,
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
                item_sources=merged.sources,
                detail=merged.detail(scanned=scanned_detail),
            )
        # DepotService 已经写过识别记录（它拿得到上下文），这里再调一次是幂等的：
        # 同 kind 同识别时刻只补上下文，不插第二条。
        await self._record(
            RECOGNITION_KIND_DEPOT,
            item_count=int(written),
            recognized_at=created_at,
            pipeline_id=pipeline_id,
            task_id=task_id,
            client_type=client_type,
        )
        return AccountCaptureResult(
            kind=RECOGNITION_KIND_DEPOT, written=True, item_count=int(written)
        )

    async def _scan_depot(self) -> tuple[dict[str, float] | None, dict[str, Any]]:
        """M45 自识别那一趟（未启用 / 不可用 / 失败都返回「没有」）。

        为什么**绝不因为自识别失败就不写内核的结果**：内核那一趟是既有事实（M27 起就
        这么写），自识别只是补差集。设备没连、链没生成、页面被点走都只该少补几个 id，
        不该把整表交白卷。所以这里一个异常都不外抛，只记 warning 并把原因写进 detail。
        """
        if self._depot_scan is None:
            return None, {}
        try:
            outcome = await self._depot_scan.scan_all_pages()
        except Exception as exc:  # noqa: BLE001 - 自识别失败只影响「补哪些 id」
            logger.warning("仓库自识别未完成，本轮只写内核那一趟的结果：%r", exc)
            return None, {"error": repr(exc)}
        run = getattr(outcome, "run", None)
        detail: dict[str, Any] = {
            "pages": int(getattr(run, "pages_scanned", 0) or 0),
            "stop_reason": getattr(run, "stop_reason", None),
            "accepted": int(getattr(outcome, "accepted", 0) or 0),
            "ambiguous": int(getattr(outcome, "ambiguous", 0) or 0),
            "unknown": int(getattr(outcome, "unknown", 0) or 0),
            "below_min_accepted": bool(getattr(outcome, "below_min_accepted", False)),
        }
        return dict(outcome.items), detail

    # -- 落库依赖 ---------------------------------------------------------
    async def _record(
        self,
        kind: str,
        *,
        item_count: int,
        recognized_at: datetime,
        pipeline_id: str | None,
        task_id: str | None,
        client_type: str | None,
    ) -> None:
        async with self._session_factory() as session:
            await AccountRecognitionRepository(session).record(
                kind=kind,
                item_count=item_count,
                recognized_at=recognized_at,
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
            )
            await session.commit()

    @property
    def _items(self) -> Mapping[str, ItemMeta]:
        if self._item_catalog is None:
            self._item_catalog = load_item_catalog()
        return self._item_catalog

    @property
    def _operators(self) -> Mapping[str, OperatorMeta]:
        if self._operator_catalog is None:
            self._operator_catalog = load_operator_catalog()
        return self._operator_catalog

    @property
    def _box(self) -> Any:
        if self._box_service is None:
            from maa_api.services.account.box import OperatorBoxService

            self._box_service = OperatorBoxService(self._session_factory)
        return self._box_service

    @property
    def _depot(self) -> Any:
        if self._depot_service is None:
            from maa_api.services.account.depot import DepotService

            self._depot_service = DepotService(self._session_factory)
        return self._depot_service


# --------------------------------------------------------------------------- #
# 纯解析（可脱离数据库单测）
# --------------------------------------------------------------------------- #
def parse_operbox(data: Mapping[str, Any]) -> list[OperBoxSnapshot] | None:
    """``OperBoxInfo`` 的 details → 按 char_id 去重的快照列表；结构不符返回 ``None``。

    ``all_opers`` 先给「是否拥有」（加上 name / rarity），``own_opers`` 再用
    elite / level / potential 覆盖它（同 id 时后者为准）。两个列表都没有、
    或一条有效记录都没有时返回 ``None``：这不该被当成「一个干员都没有」。
    """
    all_entries = _entries(data.get("all_opers"))
    own_entries = _entries(data.get("own_opers"))
    if all_entries is None and own_entries is None:
        return None

    merged: dict[str, OperBoxSnapshot] = {}
    for entry in all_entries or ():
        char_id = _char_id(entry)
        if char_id is None:
            continue
        merged[char_id] = OperBoxSnapshot(
            char_id=char_id,
            name=_text(entry.get("name")),
            rarity=_in_range(entry.get("rarity"), _RARITY_RANGE),
            own=_bool(entry.get("own")),
            listed_in_all=True,
        )
    for entry in own_entries or ():
        char_id = _char_id(entry)
        if char_id is None:
            continue
        previous = merged.get(char_id)
        merged[char_id] = OperBoxSnapshot(
            char_id=char_id,
            name=_text(entry.get("name")) or (previous.name if previous else None),
            rarity=_in_range(entry.get("rarity"), _RARITY_RANGE)
            or (previous.rarity if previous else None),
            own=True,
            elite=_in_range(entry.get("elite"), _ELITE_RANGE),
            level=_in_range(entry.get("level"), _LEVEL_RANGE),
            potential=_in_range(entry.get("potential"), _POTENTIAL_RANGE),
            listed_in_all=bool(previous and previous.listed_in_all),
        )
    return list(merged.values()) or None


def parse_depot_data(data: Mapping[str, Any]) -> dict[str, int] | None:
    """``DepotInfo`` 的 details → ``{物品ID: 数量}``；坏 JSON 或空结果返回 ``None``。

    ``data`` 是 JSON **字符串**（规格 §2.5）。单个条目数量不是非负整数时跳过它；
    一个有效条目都没有（含 ``{}``）同样返回 ``None``——识别失败不该清空仓库表。
    """
    raw = data.get("data")
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning("DepotInfo 的 data 不是合法 JSON，本次识别不落库")
        return None
    if not isinstance(payload, Mapping):
        return None
    counts: dict[str, int] = {}
    for key, value in payload.items():
        item_id = str(key).strip()
        count = _int(value)
        if not item_id or count is None or count < 0:
            continue
        counts[item_id] = count
    return counts or None


def load_item_catalog() -> dict[str, ItemMeta]:
    """本机 ``item_index.json`` → ``{itemId: ItemMeta}``；读不到返回空表。

    按 MaaCore 的真实加载链**反向**遍历（增量层覆盖基础层），第一份能读的文件生效。
    """
    catalog: dict[str, ItemMeta] = {}
    for path in _resource_paths(_ITEM_INDEX):
        payload = _read_json(path)
        if not isinstance(payload, Mapping):
            continue
        for item_id, entry in payload.items():
            if not isinstance(entry, Mapping):
                continue
            catalog[str(item_id)] = ItemMeta(
                name=_text(entry.get("name")),
                category=_text(entry.get("classifyType")),
                icon=_text(entry.get("icon")),
            )
        break
    return catalog


def load_item_icons() -> dict[str, str]:
    """本机 ``item_index.json`` → ``{itemId: icon}``；读不到返回空表。

    M44 的道具图代理用它把 ``item_id`` 解析成图标文件名（``3003`` → ``3003.png``）。
    读法与 :func:`load_item_catalog` 同一条链（反向遍历资源层、只认第一份能读的文件），
    没单独再写一套解析；调用时读一次磁盘，没有进程内缓存——换资源后不用重启即可生效。
    """
    return {
        item_id: meta.icon
        for item_id, meta in load_item_catalog().items()
        if meta.icon
    }


def load_operator_catalog() -> dict[str, OperatorMeta]:
    """本机 ``battle_data.json`` → ``{char_id: OperatorMeta}``；读不到返回空表。

    识别回调给不出职业，这一步把它补齐；职业统一转成中文（与 REST 查询参数、
    前端 ``format.ts`` 一致）。
    """
    catalog: dict[str, OperatorMeta] = {}
    for path in _resource_paths(_BATTLE_DATA):
        payload = _read_json(path)
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("chars"), Mapping
        ):
            continue
        for char_id, entry in payload["chars"].items():
            if not isinstance(entry, Mapping):
                continue
            catalog[str(char_id)] = OperatorMeta(
                name=_text(entry.get("name")),
                rarity=_in_range(entry.get("rarity"), _RARITY_RANGE),
                profession=_profession(entry.get("profession")),
                name_en=_latin_name(entry.get("name_en")),
            )
        break
    return catalog


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _resource_paths(filename: Path) -> list[Path]:
    """资源层里的文件名，增量层在前（后写的层覆盖先写的层）。"""
    return [Path(root) / filename for root in reversed(iter_resource_roots())]


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("读取资源文件失败：%s", path, exc_info=True)
        return None


def _profession(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return _PROFESSION_CN.get(text, text)


def _operators_payload(
    snapshots: Sequence[OperBoxSnapshot],
    catalog: Mapping[str, OperatorMeta],
) -> dict[str, dict[str, Any]]:
    """快照 → ``OperatorBoxService.apply_recognition`` 的 ``operators`` 形参。

    ``own`` 一定给：``all_opers`` 里、这次 ``own_opers`` 里没有的干员要明确落成
    未拥有。name / rarity / profession 用回调值优先、本机资料兜底（职业只有本机
    资料有）；识别不了精英化 / 等级 / 潜能的条目就不带这三个键，服务层会沿用行里
    既有的值。
    """
    payload: dict[str, dict[str, Any]] = {}
    for item in snapshots:
        meta = catalog.get(item.char_id)
        values: dict[str, Any] = {"own": bool(item.own)}
        name = item.name or (meta.name if meta else None)
        rarity = item.rarity or (meta.rarity if meta else None)
        profession = meta.profession if meta else None
        if name:
            values["name"] = name
        if rarity:
            values["rarity"] = rarity
        if profession:
            values["profession"] = profession
        for field in ("elite", "level", "potential"):
            value = getattr(item, field)
            if value is not None:
                values[field] = value
        payload[item.char_id] = values
    return payload


def _operator_names(snapshots: Sequence[OperBoxSnapshot]) -> dict[str, str]:
    """``char_id → 名字``，只用于收缩保护的日志提示（回调没给名字就退回 id）。"""
    return {item.char_id: item.name for item in snapshots if item.name}


def _entries(value: Any) -> list[Mapping[str, Any]] | None:
    """回调里的一个列表字段：不是 list 时返回 ``None``（缺失 ≠ 空表）。"""
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, Mapping)]


def _char_id(entry: Mapping[str, Any]) -> str | None:
    for key in ("id", "charId", "char_id"):
        text = _text(entry.get(key))
        if text:
            return text
    return None


def _is_extra_info(message: Any) -> bool:
    try:
        return int(message) == int(Message.SubTaskExtraInfo)
    except (TypeError, ValueError):
        return False


def _kind_of(what: str) -> str | None:
    if what == OPERBOX_WHAT:
        return RECOGNITION_KIND_OPERBOX
    if what == DEPOT_WHAT:
        return RECOGNITION_KIND_DEPOT
    return None


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _latin_name(value: Any) -> str | None:
    """``battle_data`` 的 ``name_en`` → 可用作身份核对的拉丁名；不是拉丁名给 ``None``。

    真机取证（2026-10-11 M40）：详情页左下角那一行是**拉丁名**（``Bagpipe`` /
    ``Ch'en the Dawnstreak``），而 ``battle_data`` 里个别干员的 ``name_en`` 直接写着中文
    （「克莱门莎」）。中文当不了拉丁名，用 ``isascii()`` 把这类挡掉。
    """
    text = _text(value)
    if not text or not text.isascii():
        return None
    return text if any(char.isalpha() for char in text) else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _bool(value: Any) -> bool | None:
    """只认真布尔：``own`` 认错方向的代价比「这次不更新」大得多。"""
    return value if isinstance(value, bool) else None


def _in_range(value: Any, bounds: tuple[int, int]) -> int | None:
    parsed = _int(value)
    if parsed is None or parsed < bounds[0] or parsed > bounds[1]:
        return None
    return parsed
