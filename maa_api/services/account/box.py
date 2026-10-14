"""账号干员数据（M27，规格 §7.3、§7.5）：读表、概要，以及森空岛同步的写路径。

**M47 起账号干员数据只有一个来源**：森空岛练度同步（`SklandSyncService` →
:meth:`OperatorBoxService.apply_skland_sync`）。内核 `OperBox` 识别、手动修正与
Agent 补录都随 M47 下线（docs/13 §8.70），所以这一层只剩三件事：

- **读**：REST 路由、Agent 工具与注入段都从这里读（``list_operators`` / ``get_operator``）。
- **概要**：``own`` 计数与「多久没同步」（``summary``）。
- **写**：:meth:`OperatorBoxService.apply_skland_sync` 一次写 :data:`SKLAND_FIELDS`
  的全部字段，并（在调用方确认过响应完整时）收缩所有权。

wire 形状在 :mod:`maa_api.services.account.rules` 里（纯函数，可脱离数据库验证），
每个写方法自成一个事务（仓储不 commit）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.rules import (
    BOX_FIELDS,
    SKLAND_FIELDS,
    SKLAND_SOURCE,
    box_row_wire,
    field_sources_wire,
    iso_z,
    json_mapping,
    mastery_wire,
    module_details_wire,
    module_wire,
    optional_int,
    text_list_wire,
)
from maa_api.settings import Settings, get_settings

__all__ = [
    "BOX_FIELDS",
    "SKLAND_FIELDS",
    "SKLAND_SOURCE",
    "OperatorBoxService",
    "box_row_wire",
]

class OperatorBoxService:
    """账号干员数据服务（规格 §7.3、§7.5）。

    :param session_factory: ``async_sessionmaker`` 或返回 ``AsyncSession`` 的可调用
        对象；每个方法自己开会话、自己定事务边界（仓储不 commit）。
    :param settings_provider: 现读配置，用于过期阈值 ``agent.box_stale_days``
        （注入替身可在测试里固定阈值）。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider

    # -- 概要 -------------------------------------------------------------
    async def summary(self) -> dict[str, Any]:
        """账号干员概要：总数、精二数、同步时间与是否过期（规格 §7.5）。

        「总数」只算 ``own`` 为真的行——森空岛同步会把这一轮没有的干员收缩成未拥有，
        页面与注入段要的是「你有多少干员」，不是「游戏里有多少干员」。``recognized_at``
        取 ``max(account_operator.recognized_at)``：M47 起唯一会写它的就是森空岛同步。
        """
        operator_repo = _repositories()
        async with self._session_factory() as session:
            repo = operator_repo(session)
            total = await repo.count(owned_only=True)
            elite_two = await repo.count(owned_only=True, elite_min=2)
            recognized_at = await repo.last_recognized_at()
        stale_days = int(self._settings_provider().agent.box_stale_days)
        return {
            "total": total,
            "elite_two": elite_two,
            "recognized_at": iso_z(recognized_at),
            "stale": _is_stale(recognized_at, stale_days),
            "stale_days": stale_days,
            "unrecognized": max(total - elite_two, 0),
        }

    # -- 读 ---------------------------------------------------------------
    async def list_operators(
        self,
        *,
        query: str | None = None,
        owned_only: bool = True,
        rarity_min: int | None = None,
        profession: str | None = None,
        elite_min: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """干员列表，带筛选与 ``total``（规格 §7.5 与 Agent 工具的同一组参数）。

        ``limit`` / ``offset`` 由本层夹到合法区间（负数在 SQLite 上不报错、只静默
        返回错结果）；``total`` 是筛选后的总数，不是当前页条数。``owned_only=False``
        会把未拥有的干员也列出来（同步把这一轮名单里没有的干员收缩成未拥有，行还留着）。
        """
        limit = max(int(limit), 1)
        offset = max(int(offset), 0)
        text = (query or "").strip() or None
        prof = (profession or "").strip() or None
        # 仓储的 ``owned_only`` 是三态（``None`` 不过滤 / ``True`` 只列拥有 / ``False``
        # 只列未拥有）；接口上的 ``owned_only=False`` 是「全都列出来」，所以这里映射成
        # ``None``。直接把 False 透下去会只返回未拥有的干员。
        ownership: bool | None = True if owned_only else None
        operator_repo = _repositories()
        async with self._session_factory() as session:
            repo = operator_repo(session)
            total = await repo.count(
                query=text,
                owned_only=ownership,
                rarity_min=rarity_min,
                profession=prof,
                elite_min=elite_min,
            )
            rows = await repo.list(
                query=text,
                owned_only=ownership,
                rarity_min=rarity_min,
                profession=prof,
                elite_min=elite_min,
                limit=limit,
                offset=offset,
            )
        return {
            "items": [box_row_wire(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def get_operator(self, char_id: str) -> dict[str, Any]:
        """单条干员；没有记录时 ``ACCOUNT_DATA_NOT_FOUND``（规格 §7.5）。"""
        operator_repo = _repositories()
        async with self._session_factory() as session:
            row = await operator_repo(session).get(char_id)
        if row is None:
            raise AppError(
                ErrorCode.ACCOUNT_DATA_NOT_FOUND,
                "账号数据里没有这名干员的记录，请先在账号页绑定森空岛账号并同步练度",
                {"char_id": char_id},
            )
        return box_row_wire(row)

    # -- 森空岛账号数据（M42，sync.py 调用）--------------------------------
    async def apply_skland_sync(
        self,
        updates: Sequence[Any],
        *,
        clear_missing: bool = False,
        synced_at: datetime | None = None,
    ) -> dict[str, Any]:
        """把一轮森空岛同步写进表（M42 规格 §4.5）。

        **M47 起这是账号干员数据的唯一写入者**：内核 `OperBox` 识别与手动 / Agent
        补录都随 M47 一起下线（docs/13 §8.70），所以没有字段级分工，一次同步写
        :data:`SKLAND_FIELDS` 里的全部字段。

        ``None`` 一律解释成「这一项上游没给或给了越界值」：不写该列，也不把它标成
        ``skland`` 来源（来源台账与值必须一致，M35 的口径）。**空映射是正面读数**：
        ``modules={}`` 表示「确认没装模组」，要把 ``NULL`` 落成 ``{}``；
        ``masteries`` 的三槽全 0 同理。

        ``clear_missing`` 决定要不要把「库里已拥有、这一轮 ``chars`` 里没有」的干员置
        ``own=False``。**默认不动**：只有调用方（
        :class:`~maa_api.services.skland.sync.SklandSyncService`）确认过这一轮
        ``len(chars)`` 数量级正常（``SKLAND_MIN_CHARS``）才会传 ``True``——把「什么时候
        可以清 ``own``」与「怎么清」分开，免得半截响应在这里被当成事实。

        :param updates: :class:`~maa_api.services.skland.mapping.OperatorTrainingUpdate` 列表。
        :param clear_missing: 是否按这一轮结果收缩所有权。
        :param synced_at: 同步时间（默认当前 UTC 时间），写进 ``recognized_at``。
        :returns: ``{"total", "created", "updated", "unchanged", "ownership_cleared"}``
            五个计数，供同步摘要与测试断言。
        """
        moment = synced_at or _utcnow()
        operator_repo = _repositories()
        created = updated = unchanged = cleared = 0
        seen: set[str] = set()

        async with self._session_factory() as session:
            repo = operator_repo(session)
            for update in updates:
                char_id = str(getattr(update, "char_id", "") or "")
                if not char_id:
                    continue
                seen.add(char_id)
                row = await repo.get(char_id)
                if row is None:
                    row = repo.new_row(
                        char_id,
                        name=getattr(update, "name", None),
                        rarity=getattr(update, "rarity", None),
                        profession=getattr(update, "profession", None),
                        own=True,
                    )
                    created += 1
                else:
                    # 目录里有名字/星级/职业就补上（新库或早期识别没认出来的行）。
                    if getattr(row, "name", None) is None and getattr(update, "name", None):
                        row.name = update.name
                    if getattr(row, "rarity", None) is None and getattr(update, "rarity", None):
                        row.rarity = update.rarity
                    if (
                        getattr(row, "profession", None) is None
                        and getattr(update, "profession", None)
                    ):
                        row.profession = update.profession

                previous = json_mapping(getattr(row, "recognized_values", None))
                sources = field_sources_wire(getattr(row, "field_sources", None))
                changed: set[str] = set()
                for field in SKLAND_FIELDS:
                    value = getattr(update, field, None)
                    if value is None:
                        continue
                    before = getattr(row, field, None)
                    if _skland_value_changed(field, before, value):
                        # M47 起没有「手动 / Agent 改过」这类值了，差异表不再产生新行
                        # （旧行保留为历史，docs/13 §8.70）。
                        setattr(row, field, value)
                        changed.add(field)
                    previous[field] = value
                    sources[field] = SKLAND_SOURCE

                if changed:
                    row.recognized_values = previous
                    row.field_sources = sources
                    row.recognized_at = moment
                    row.updated_at = moment
                    await repo.upsert(row)
                    updated += 1
                else:
                    # 值没变也要把「这一轮同步过、来源是森空岛」记下来：字段来源与
                    # recognized_at 是页面判断「数据有多新」的依据。
                    row.recognized_values = previous
                    row.field_sources = sources
                    row.recognized_at = moment
                    await repo.upsert(row)
                    unchanged += 1

            if clear_missing:
                # 一次取回全部已拥有的行（``limit=None`` ＝ 不限制），只对不在这一轮
                # ``chars`` 里的那些收缩；未拥有的行本来就该保持未拥有，跳过。
                for row in await repo.list(owned_only=True):
                    if row.char_id in seen or not bool(getattr(row, "own", False)):
                        continue
                    row.own = False
                    sources = field_sources_wire(getattr(row, "field_sources", None))
                    sources["own"] = SKLAND_SOURCE
                    previous = json_mapping(getattr(row, "recognized_values", None))
                    previous["own"] = False
                    row.recognized_values = previous
                    row.field_sources = sources
                    row.recognized_at = moment
                    row.updated_at = moment
                    await repo.upsert(row)
                    cleared += 1

            await session.commit()

        return {
            "total": len(updates),
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "ownership_cleared": cleared,
        }

    async def record_skland_sync(
        self,
        *,
        item_count: int,
        synced_at: datetime | None = None,
    ) -> None:
        """写一条 ``skland`` 识别记录（M42 规格 §4.5）。

        ``item_count`` 是这一轮真的写进库的干员数与清理掉的所有权数之和——与
        ``training`` 记录同口径：记的是成果，不是名单长度。
        """
        from maa_api.db.repositories.account import RECOGNITION_KIND_SKLAND

        moment = synced_at or _utcnow()
        recognition_repo = _recognition_repository()
        async with self._session_factory() as session:
            await recognition_repo(session).record(
                kind=RECOGNITION_KIND_SKLAND,
                pipeline_id=None,
                task_id=None,
                client_type=None,
                item_count=int(item_count),
                recognized_at=moment,
            )
            await session.commit()


def _repositories() -> Any:
    """延迟取干员仓储类（import 放在调用处，模块级不 import ``db.repositories``）。

    这样服务层与 ORM、建表脚本解耦：``rules.py`` 与路由的请求模型可以在数据库就绪
    之前被单独引用与测试（Agent 工具、前端契约测试都要用同一份约束）。
    """
    from maa_api.db.repositories import account as repositories

    return repositories.AccountOperatorRepository


def _recognition_repository() -> Any:
    """延迟取识别记录仓储类（只写 ``skland`` / ``depot`` 两种历史记录）。"""
    from maa_api.db.repositories import account as repositories

    return repositories.AccountRecognitionRepository


def _utcnow() -> datetime:
    """库内时间源：naive UTC（与 ``db.models.utcnow`` 同口径）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _is_stale(recognized_at: datetime | None, stale_days: int) -> bool:
    """从未识别算过期（页面要提示「尚未识别」）；否则按天数判。"""
    if recognized_at is None:
        return True
    return recognized_at <= _utcnow() - timedelta(days=stale_days)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _skland_value_changed(field: str, before: Any, value: Any) -> bool:
    """森空岛同步里「这一列要不要写」的判定（M42 规格 §4.5）。

    JSON 两列先过一遍 wire 归一：库里可能躺着历史形状（``{"1": 3}`` 缺槽位、未知模组
    类型），直接比字典会把「等价但形状不同」判成变化，白白多记一次 ``updated``。
    整数与布尔列按原值比；``None``（＝未识别）与任何具体值都算变化。
    """
    if field == "masteries":
        return mastery_wire(before) != value
    if field == "modules":
        return module_wire(before) != value
    if field == "own":
        return bool(before) is not bool(value)
    if field == "skill_ids":
        # M43：列表列。不归一的话会掉进下面的 ``optional_int`` 分支——两侧都变 ``None``，
        # 于是这一列**永远不会被写**（不是报错，是静默不写，更难发现）。
        return text_list_wire(before) != text_list_wire(value)
    if field == "module_details":
        return module_details_wire(before) != module_details_wire(value)
    return optional_int(before) != optional_int(value)
