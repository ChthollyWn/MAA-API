"""MaaCore 账号识别回调的采集（规格 §7.2 T2、§7.3）。

M47 起只剩内核仓库识别这一条通道（``DepotInfo``，docs/13 §8.70）：结果通过
``SubTaskExtraInfo`` 推送，过程中会发多次 ``done=false``，只有最后一次 ``done=true``
才是收尾（规格 §2.5）：

- ``DepotInfo``：``data`` 是 JSON 字符串 ``{"物品ID": 数量}``，**整表替换**仓库数量；
  物品名与本机分类用 ``item_index.json`` 映射，映射不到的保留 id。

干员数据的唯一来源是森空岛练度同步（``services/skland/`` → ``OperatorBoxService``
``apply_skland_sync``）；内核 ``OperBoxInfo`` 不再有解析与落库路径。本模块额外把
「流水线 / 任务 / 渠道」补进识别记录（服务层拿不到这三个值，见
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
    AccountRecognitionRepository,
)
from maa_api.services.resource_index import iter_resource_roots

__all__ = [
    "AccountCaptureResult",
    "AccountCaptureService",
    "ItemMeta",
    "OperatorMeta",
    "load_item_catalog",
    "load_item_icons",
    "load_operator_catalog",
    "parse_depot_data",
]

logger = logging.getLogger(__name__)

#: 采集回调的 ``what``（规格 §7.3）。M47 起只剩仓库：干员数据唯一来源是森空岛同步。
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
class AccountCaptureResult:
    """一次采集的结果：写没写、写了多少、为什么没写。

    ``skipped`` 取值：``not_subtask_extra_info``（不是这条回调）、``unsupported``
    （别的 ``what``）、``incomplete``（``done=false`` 的过程回调）、``bad_payload``
    （结构不符或坏 JSON）、``error``（落库时抛了异常，已记 warning）。
    """

    kind: str | None = None
    written: bool = False
    skipped: str | None = None
    item_count: int = 0
    error: str | None = None


class AccountCaptureService:
    """把账号识别的 ``SubTaskExtraInfo`` 落库（规格 §7.2 T2）。

    M47 起只处理内核仓库识别的 ``DepotInfo``：干员数据的唯一来源是森空岛同步
    （``SklandSyncService`` → :meth:`OperatorBoxService.apply_skland_sync`）。

    :param session_factory: ``async_sessionmaker``；落库与写识别记录各开一个会话。
    :param item_catalog: ``itemId → ItemMeta`` 替身，测试注入；默认读本机
        ``item_index.json``（读不到就只保留 id）。
    :param depot_service: 落库服务替身，测试注入；默认按 ``session_factory`` 现建
        （构造签名见 :mod:`maa_api.services.account.depot`）。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        item_catalog: Mapping[str, ItemMeta] | None = None,
        depot_service: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._item_catalog = item_catalog
        self._depot_service = depot_service

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
        if what != DEPOT_WHAT:
            # M47 起只剩仓库识别这一条通道：干员数据的唯一来源是森空岛同步，
            # 内核 ``OperBoxInfo`` 不再有落库路径（docs/13 §8.70）。
            return AccountCaptureResult(skipped="unsupported")
        data = callback.get("details")
        if not isinstance(data, Mapping):
            return AccountCaptureResult(kind=_kind_of(what), skipped="bad_payload")
        if data.get("done") is not True:
            # done=false 是过程回调：只在收尾时写入（规格 §7.3）。
            return AccountCaptureResult(kind=_kind_of(what), skipped="incomplete")
        return await self._capture_depot(
            data,
            created_at=created_at,
            pipeline_id=pipeline_id,
            task_id=task_id,
            client_type=client_type,
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

        # 整表替换的输入只有内核 ``DepotInfo`` 这一趟（M27 起就是这么写的，M44 收起
        # 自识别之后**逐字一致**）：物品名与分类用本机 ``item_index.json`` 映射，
        # 映射不到的保留 id。写库的两道护栏在 ``DepotService.replace_all`` 里。
        catalog = self._items
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
    def _depot(self) -> Any:
        if self._depot_service is None:
            from maa_api.services.account.depot import DepotService

            self._depot_service = DepotService(self._session_factory)
        return self._depot_service


# --------------------------------------------------------------------------- #
# 纯解析（可脱离数据库单测）
# --------------------------------------------------------------------------- #
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


def _is_extra_info(message: Any) -> bool:
    try:
        return int(message) == int(Message.SubTaskExtraInfo)
    except (TypeError, ValueError):
        return False


def _kind_of(what: str) -> str | None:
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


def _in_range(value: Any, bounds: tuple[int, int]) -> int | None:
    parsed = _int(value)
    if parsed is None or parsed < bounds[0] or parsed > bounds[1]:
        return None
    return parsed
