"""基建技能与排班候选（规格 §8.3）：Cargo 表 + 干员页 + 账号数据 + 已存方案。

数据来源与优先级（照规格 §8.3）：

1. 技能描述与房间：Cargo ``building_skill2`` 整表（服务端单次上限 500 行，按房间分块取，
   ``static`` 档缓存），进程内再缓存一次。
2. 干员与技能的对应：先试 SMW 属性 ``后勤技能名`` 反查（``action=ask`` 一次拿全表）。
   **2026-10-10 实测：该属性在 PRTS 上没有任何取值**（``[[后勤技能名::+]]`` 返回 0 条、
   ``银灰`` 页的该属性 printout 为空），所以实际走的是回落路径：批量取干员页
   （一次 ≤20 个标题）只解析 ``{{后勤技能}}``。
3. 效率提示：从清洗后的描述里抽（``efficiency_hints``）；本机内核资源 ``infrast.json``
   的 ``efficient`` 数值一并带上（``source=resource``），房间槽位也对它取 ``maxNumOfOpers``。
4. 账号数据：``OperatorBoxService``（已拥有、精英化、等级）。

``candidates`` 的候选人是「账号里已拥有 **且** 已解锁该技能」的干员：同一槽位里只算账号
练度能满足的最高档（森蚺精一只列 ``自动化·α``，不列要精二的 ``自动化·β``）。缺账号数据
或 PRTS 取数失败时如实进 ``unavailable``，不返回一张空表当答案。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef, build_envelope
from maa_api.services.external.prts import PrtsClient
from maa_api.services.game.operators import OperatorService
from maa_api.services.prts_parse.building import (
    BuildingSkill,
    BuildingSkillEntry,
    efficiency_hints,
    parse_building_skill_table,
    parse_operator_building_skills,
)
from maa_api.services.resource_index import iter_resource_roots
from maa_api.settings import Settings, get_settings

__all__ = ["BuildingService", "default_building_service"]

logger = logging.getLogger(__name__)

#: Cargo `building_skill2` 的房间取值（2026-10-10 实测 10 个，合计 720 行，与内核
#: `infrast.json` 的 `roomType` 一一对应）。整表按房间分块取：服务端单次上限 500 行、
#: 且 `PrtsClient.cargo` 没有 offset，逐房间查最稳（当前最大一间 108 行）。
_SKILL_ROOMS = (
    "制造站",
    "加工站",
    "控制中枢",
    "宿舍",
    "训练室",
    "贸易站",
    "会客室",
    "办公室",
    "发电站",
    "回收站",
)

#: 房间别名：中文名 / 内核键（``trading``）/ 任务设施枚举（``Trade``）都认。
_ROOM_ALIASES: dict[str, str] = {
    "制造站": "制造站",
    "mfg": "制造站",
    "manufacture": "制造站",
    "贸易站": "贸易站",
    "trade": "贸易站",
    "trading": "贸易站",
    "发电站": "发电站",
    "power": "发电站",
    "控制中枢": "控制中枢",
    "control": "控制中枢",
    "会客室": "会客室",
    "reception": "会客室",
    "meeting": "会客室",
    "办公室": "办公室",
    "office": "办公室",
    "hire": "办公室",
    "宿舍": "宿舍",
    "dorm": "宿舍",
    "dormitory": "宿舍",
    "加工站": "加工站",
    "processing": "加工站",
    "训练室": "训练室",
    "training": "训练室",
    "回收站": "回收站",
    "recycle": "回收站",
}

#: 排班方案（custom_infrast JSON）的房间键 → (中文名, 该布局是否给数量)。
_LAYOUT_ROOMS: tuple[tuple[str, str], ...] = (
    ("trading", "贸易站"),
    ("manufacture", "制造站"),
    ("power", "发电站"),
)
_FIXED_ROOMS: tuple[tuple[str, str], ...] = (
    ("control", "控制中枢"),
    ("meeting", "会客室"),
    ("hire", "办公室"),
    ("dormitory", "宿舍"),
    ("processing", "加工站"),
)

#: 布局不定数量的房间的固定间数（基地固定 1 间中枢/会客室/办公室/加工站、4 间宿舍）。
_FIXED_ROOM_COUNTS: dict[str, int] = {
    "control": 1,
    "meeting": 1,
    "hire": 1,
    "dormitory": 4,
    "processing": 1,
}

#: 布局串的三位数字：贸易站 / 制造站 / 发电站。
_LAYOUTS = {"243", "153", "252", "333"}

#: 房间的进驻人数（内核 `infrast.json` 的 `maxNumOfOpers` 优先，这里是兜底）。
_ROOM_SLOTS: dict[str, int] = {
    "control": 5,
    "manufacture": 3,
    "trading": 3,
    "power": 1,
    "meeting": 2,
    "hire": 1,
    "dormitory": 5,
    "processing": 1,
}

#: 排班方案的房间键 → 内核 `infrast.json` 的房间键（取 `maxNumOfOpers`）。
_CORE_ROOM_KEYS: dict[str, str] = {
    "control": "Control",
    "manufacture": "Mfg",
    "trading": "Trade",
    "power": "Power",
    "meeting": "Reception",
    "hire": "Office",
    "dormitory": "Dorm",
    "processing": "Processing",
    "training": "Training",
    "recycle": "Recycle",
}

#: 一次批量取干员页的标题数（规格 §5.4 / §8.3：一次 ≤ 20 个）。
MAX_TITLES = 20

#: 可以降级的外部错误：技能表取不到时不把整个调用变成错误（§3.5）。
_DEGRADABLE = frozenset(
    {
        ErrorCode.EXTERNAL_SOURCE_DISABLED,
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
    }
)

_BATTLE_DATA = Path("battle_data.json")
_CORE_INFRAST = Path("infrast.json")

#: PRTS 基建技能表的出处（Cargo 查询没有单页标题，指到一览页）。
_SKILL_TABLE_REF = SourceRef(
    kind="prts",
    label="PRTS",
    title="后勤技能一览（building_skill2 表）",
    url="https://prts.wiki/w/后勤技能一览",
)


@dataclass(frozen=True, slots=True)
class _CoreSkill:
    """内核 `infrast.json` 里的一条技能：效率数值与效果组内的干员。"""

    names: tuple[str, ...]
    operator_ids: tuple[str, ...]
    efficient: Mapping[str, Any]

    @property
    def group_has_multiple_names(self) -> bool:
        return len(self.names) > 1


class BuildingService:
    """基建技能的领域服务（规格 §8.3 的三个方法）。"""

    def __init__(
        self,
        *,
        prts: PrtsClient | None = None,
        box: Any = None,
        resource_service: Any = None,
        operator_service: OperatorService | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
        core_resource_roots: Sequence[str | Path] | None = None,
    ) -> None:
        self._prts = prts
        self._box = box
        self._resource_service = resource_service
        self._operator_service = operator_service
        self._settings_provider = settings_provider
        self._core_roots = (
            [Path(root) for root in core_resource_roots] if core_resource_roots else None
        )
        #: Cargo 整表（进程内缓存；HTTP 层还有 static 档）。
        self._table: tuple[BuildingSkill, ...] | None = None
        #: SMW 反查的干员 → 后勤技能（None 表示还没试过）。
        self._smw_index: dict[str, tuple[BuildingSkillEntry, ...]] | None = None
        #: 干员页解析结果（标题 → 条目），失败按空元组记，避免反复打站点。
        self._page_index: dict[str, tuple[BuildingSkillEntry, ...]] = {}
        #: 内核 `infrast.json` 的技能表（技能名 → _CoreSkill）。
        self._core_skills: dict[str, _CoreSkill] | None = None
        self._core_room_slots: dict[str, int] = {}
        #: 最近一次干员页取数的出处（进来源信封）。
        self._last_page: _PageRef | None = None

    # -- 依赖 -------------------------------------------------------------
    @property
    def prts(self) -> PrtsClient:
        return self._prts or PrtsClient()

    @property
    def operator_service(self) -> OperatorService:
        if self._operator_service is None:
            self._operator_service = OperatorService()
        return self._operator_service

    @property
    def box(self) -> Any:
        if self._box is not None:
            return self._box
        from maa_api.services.account.box import OperatorBoxService
        from maa_api.db import session as db_session

        self._box = OperatorBoxService(db_session.session_factory)
        return self._box

    # -- skills -----------------------------------------------------------
    async def skills(
        self,
        *,
        operator: str | None = None,
        room: str | None = None,
        skill: str | None = None,
    ) -> dict[str, Any]:
        """按干员、房间或技能名查基建技能；三者必须给一个。"""
        given = [name for name, value in (("operator", operator), ("room", room), ("skill", skill)) if value]
        if len(given) != 1:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "operator、room、skill 必须三选一",
                {"operator": operator, "room": room, "skill": skill},
            )
        unavailable: list[str] = []
        try:
            table = await self.skill_table()
        except AppError as exc:
            # 按干员查时技能名与解锁条件来自干员页：Cargo 挂了还能给一份不带房间与描述的
            # 结果（`unavailable=["skill_table"]`）；按房间或技能名查时表就是数据本身，
            # 照旧把外部错误抛给工具层（规格 §3.5、§4.8）。
            if exc.code not in _DEGRADABLE or operator is None:
                raise
            table = ()
            unavailable.append("skill_table")
        sources: list[SourceRef] = [_SKILL_TABLE_REF]
        if operator is not None:
            payload, owners_unavailable = await self._skills_of_operator(
                operator, table, sources
            )
            unavailable.extend(owners_unavailable)
        elif skill is not None:
            payload = self._skills_named(skill, table)
        else:
            payload = self._skills_in_room(room or "", table)
        payload["query"] = {"operator": operator, "room": room, "skill": skill}
        payload.setdefault("unavailable", [])
        payload["unavailable"] = list(dict.fromkeys([*unavailable, *payload["unavailable"]]))
        payload.update(build_envelope(sources, unavailable=payload["unavailable"]))
        return payload

    async def skill_table(self) -> tuple[BuildingSkill, ...]:
        """Cargo ``building_skill2`` 整表（进程内缓存）。"""
        if self._table is not None:
            return self._table
        rows: list[Any] = []
        for room in _SKILL_ROOMS:
            result = await self.prts.cargo(
                tables=["building_skill2"],
                fields=["name", "room", "description"],
                where=f'room="{room}"',
                limit=500,
            )
            rows.extend(_cargo_rows(result))
        self._table = parse_building_skill_table(rows)
        return self._table

    async def skill_info(self, names: Sequence[str]) -> dict[str, dict[str, Any]]:
        """按技能名取 ``房间 / 描述 / 效率提示``（`get_operator` 的 building 段用）。"""
        table = await self.skill_table()
        by_name = {item.name: item for item in table}
        info: dict[str, dict[str, Any]] = {}
        for name in names:
            item = by_name.get(name)
            if item is None:
                continue
            info[name] = {
                "room": item.room,
                "description": item.description,
                "hints": efficiency_hints(item.description, room=item.room),
                "efficiency": self._core_efficiency(name),
            }
        return info

    # -- candidates -------------------------------------------------------
    async def candidates(
        self,
        *,
        layout: str,
        shifts: int,
        rooms: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """按布局与班次列出各房间「已拥有且已解锁技能」的干员。"""
        layout_key = str(layout).strip()
        if layout_key not in _LAYOUTS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"layout 只能是 {'、'.join(sorted(_LAYOUTS))} 之一",
                {"layout": layout},
            )
        if isinstance(shifts, bool) or not isinstance(shifts, int) or not 2 <= shifts <= 4:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "shifts 必须是 2–4 的整数",
                {"shifts": shifts},
            )
        wanted = self._wanted_rooms(rooms)

        unavailable: list[str] = []
        try:
            table = await self.skill_table()
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
            table = ()
            unavailable.append("skill_table")
        by_name = {item.name: item for item in table}

        owned, account_unavailable = await self._owned_operators()
        unavailable.extend(account_unavailable)

        entries_by_operator: dict[str, tuple[BuildingSkillEntry, ...]] = {}
        missing_pages = 0
        for row in owned:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            # 本机 battle_data.json 认识时用它的正式名当 PRTS 标题（账号数据里的名字可能
            # 带括注或外文写法）；不认识就直接拿名字试一次，失败计入 operators_missing_page。
            local = self._local_operator(name)
            title = local.name if local is not None else name
            entries = await self._operator_entries(title)
            if not entries:
                missing_pages += 1
                continue
            entries_by_operator[name] = entries
        if missing_pages:
            unavailable.append("operator_pages")

        candidates = self._match_candidates(owned, entries_by_operator, by_name)
        room_rows = self._room_rows(layout_key, shifts, candidates, wanted)
        payload: dict[str, Any] = {
            "layout": layout_key,
            "shifts": shifts,
            "rooms": room_rows,
            "owned_total": len(owned),
            "operators_missing_page": missing_pages,
            "unavailable": list(dict.fromkeys(unavailable)),
        }
        if "account" in unavailable:
            payload["hint"] = (
                "账号里没有已拥有的干员记录，先用 ask_user 建议用户在账号页绑定森空岛账号并同步练度"
                "再查候选。"
            )
        payload.update(build_envelope([_SKILL_TABLE_REF], unavailable=payload["unavailable"]))
        return payload

    # -- plans ------------------------------------------------------------
    async def plans(self) -> dict[str, Any]:
        """已保存的自定义基建方案：文件名、标题、班次数与各班 ``period``。"""
        if self._resource_service is None:
            raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "资源服务尚未启动")
        listing = await self._resource_service.list_infrast_plans(page=1, size=200)
        rows: list[dict[str, Any]] = []
        for item in listing.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            filename = item.get("filename") or (item.get("meta") or {}).get("filename")
            filename = filename if isinstance(filename, str) and filename else None
            row: dict[str, Any] = {
                "filename": filename,
                "name": item.get("name"),
                "description": item.get("description"),
                "plan_count": (item.get("meta") or {}).get("plan_count"),
                "title": None,
                "plans": [],
                "readable": False,
            }
            if filename:
                try:
                    content = await self._resource_service.read_infrast_plan(filename)
                except AppError as exc:
                    logger.warning("基建方案 %s 读不出来：%s", filename, exc.code)
                    row["error"] = exc.message
                else:
                    row["readable"] = True
                    row["title"] = content.get("title")
                    plans = content.get("plans")
                    row["plans"] = [
                        {
                            "name": plan.get("name"),
                            "period": plan.get("period"),
                            "description": plan.get("description"),
                        }
                        for plan in (plans if isinstance(plans, list) else [])
                        if isinstance(plan, Mapping)
                    ]
                    row["plan_count"] = row["plan_count"] or len(row["plans"])
            rows.append(row)
        return {
            "plans": rows,
            "total": len(rows),
            **build_envelope(),
        }

    # -- 内部：skills -----------------------------------------------------
    async def _skills_of_operator(
        self, query: str, table: tuple[BuildingSkill, ...], sources: list[SourceRef]
    ) -> tuple[dict[str, Any], list[str]]:
        """按干员查：干员名 → 条目 + Cargo 的房间与描述。"""
        unavailable: list[str] = []
        local = self._local_operator(query)
        char_id = local.char_id if local is not None else None
        if local is not None:
            title: str | None = local.name
            entries = await self._operator_entries(title)
        else:
            # 本机不认识的写法交给 PRTS：先直查（吃重定向），再搜索。
            title, entries = await self._resolve_remote(query)
        if title is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"找不到干员：{query}。可以试中文名、英文名或 char_id",
                {"operator": query},
            )
        if not entries:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"干员页里没有后勤技能数据：{title}",
                {"operator": query, "title": title},
            )
        if self._last_page is not None and self._last_page.title == title:
            sources.extend(self._last_page.refs)
        by_name = {item.name: item for item in table}
        rows: list[dict[str, Any]] = []
        for entry in entries:
            item = by_name.get(entry.name)
            rows.append(
                {
                    "name": entry.name,
                    "room": item.room if item else None,
                    "unlock": entry.unlock,
                    "description": item.description if item else None,
                    "hints": efficiency_hints(item.description, room=item.room) if item else [],
                    "efficiency": self._core_efficiency(entry.name),
                    "slot": entry.slot,
                    "owners": [
                        {
                            "name": title,
                            "char_id": char_id,
                            "unlock": entry.unlock,
                            "source": "prts_page",
                        }
                    ],
                }
            )
        if any(row["room"] is None for row in rows):
            unavailable.append("skill_table")
        return (
            {
                "operator": {"title": title, "char_id": char_id},
                "skills": rows,
                "total": len(rows),
            },
            unavailable,
        )

    def _skills_named(
        self, query: str, table: tuple[BuildingSkill, ...]
    ) -> dict[str, Any]:
        needle = query.strip()
        matched = [item for item in table if item.name == needle]
        if not matched:
            folded = needle.casefold()
            matched = [item for item in table if item.name.casefold() == folded]
        if not matched:
            suggestions = [
                item.name for item in table if needle and needle in item.name
            ][:5]
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"找不到基建技能：{query}",
                {"skill": query, "suggestions": suggestions},
            )
        rows = [self._skill_row(item, unlock=None) for item in matched]
        return {"skills": rows, "total": len(rows)}

    def _skills_in_room(
        self, query: str, table: tuple[BuildingSkill, ...]
    ) -> dict[str, Any]:
        room = _ROOM_ALIASES.get(query.strip().casefold()) or _ROOM_ALIASES.get(query.strip())
        if room is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"不认识基建房间：{query}。可用：{'、'.join(dict.fromkeys(_ROOM_ALIASES.values()))}",
                {"room": query},
            )
        rows = [self._skill_row(item, unlock=None) for item in table if item.room == room]
        if not rows:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"技能表里没有 {room} 的技能",
                {"room": query},
            )
        return {"room": room, "skills": rows, "total": len(rows)}

    def _skill_row(self, item: BuildingSkill, *, unlock: str | None) -> dict[str, Any]:
        owners, approximate = self._owners_of(item.name)
        return {
            "name": item.name,
            "room": item.room,
            "unlock": unlock,
            "description": item.description,
            "hints": efficiency_hints(item.description, room=item.room),
            "efficiency": self._core_efficiency(item.name),
            "owners": owners,
            "owners_approximate": approximate,
        }

    def _owners_of(self, name: str) -> tuple[list[dict[str, Any]], bool]:
        """技能名 → 拥有者。SMW 反查不可用时用干员页缓存 + 内核资源。"""
        owners: list[dict[str, Any]] = []
        seen: set[str] = set()
        for title, entries in self._page_index.items():
            if any(entry.name == name for entry in entries):
                owners.append({"name": title, "char_id": None, "source": "prts_page"})
                seen.add(title)
        core = self._core_skills_map().get(name)
        approximate = False
        if core is not None and core.operator_ids:
            approximate = core.group_has_multiple_names
            for char_id in core.operator_ids:
                operator_name = self._operator_name(char_id)
                if operator_name is None or operator_name in seen:
                    continue
                owners.append(
                    {"name": operator_name, "char_id": char_id, "source": "resource"}
                )
                seen.add(operator_name)
        return owners, approximate

    # -- 内部：candidates -------------------------------------------------
    def _wanted_rooms(self, rooms: Sequence[str] | None) -> list[str] | None:
        if rooms is None:
            return None
        wanted: list[str] = []
        for raw in rooms:
            key = _ROOM_ALIASES.get(str(raw).strip().casefold()) or _ROOM_ALIASES.get(
                str(raw).strip()
            )
            if key is None:
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    f"不认识基建房间：{raw}",
                    {"room": raw, "rooms": list(_ROOM_ALIASES.values())},
                )
            slot = next(
                (
                    room_key
                    for room_key, label in (*_LAYOUT_ROOMS, *_FIXED_ROOMS)
                    if label == key
                ),
                None,
            )
            if slot is not None and slot not in wanted:
                wanted.append(slot)
        return wanted

    async def _owned_operators(self) -> tuple[list[dict[str, Any]], list[str]]:
        """账号里已拥有的干员（分页取全量）；没有记录时如实说明。"""
        if self._box is None:
            try:
                self.box  # 缺省门面：读本服务的账号数据表
            except Exception:  # noqa: BLE001 - 账号数据只是可选上下文
                logger.warning("账号数据服务不可用，候选项只按技能表给", exc_info=True)
                return [], ["account"]
        box = self._box
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            try:
                page = await box.list_operators(owned_only=True, limit=100, offset=offset)
            except AppError as exc:
                if exc.code in _DEGRADABLE or exc.code is ErrorCode.ACCOUNT_DATA_NOT_FOUND:
                    return [], ["account"]
                raise
            items = [item for item in (page.get("items") or []) if isinstance(item, Mapping)]
            rows.extend(dict(item) for item in items)
            total = int(page.get("total") or 0)
            offset += len(items)
            if not items or offset >= total:
                break
        if not rows:
            return [], ["account"]
        return rows, []

    async def _operator_entries(self, title: str) -> tuple[BuildingSkillEntry, ...]:
        """干员页的 ``{{后勤技能}}``（缓存；SMW 反查可用时先看 SMW 索引）。"""
        index = await self._smw_mapping()
        if index is not None and title in index:
            return index[title]
        if title in self._page_index:
            return self._page_index[title]
        entries: tuple[BuildingSkillEntry, ...] = ()
        try:
            batch = await self.prts.wikitext([title])
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
            self._page_index[title] = ()
            return ()
        self._last_page = _PageRef(
            title=title,
            refs=list(batch.source_refs()),
        )
        for page in batch.pages:
            if page.missing:
                continue
            parsed = parse_operator_building_skills(page.content, title=page.title)
            if parsed:
                self._page_index[page.title] = parsed
                if page.title != title:
                    self._page_index[title] = parsed
                entries = parsed
                break
        if not entries:
            self._page_index[title] = ()
        return entries

    async def _smw_mapping(self) -> dict[str, tuple[BuildingSkillEntry, ...]] | None:
        """SMW ``后勤技能名`` 反查（一次拿全表）；实测取不到时返回 ``None`` 并回落。"""
        if self._smw_index is not None:
            return self._smw_index or None
        try:
            result = await self.prts.ask(
                ["[[后勤技能名::+]]"],
                printouts=["后勤技能名", "后勤技能阶段"],
                limit=500,
            )
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
            self._smw_index = {}
            return None
        index: dict[str, tuple[BuildingSkillEntry, ...]] = {}
        for page in self.prts.smw_pages(result):
            names = [name.strip() for name in page.texts("后勤技能名") if name.strip()]
            stages = [str(value).strip() for value in (page.printouts.get("后勤技能阶段") or [])]
            entries = tuple(
                BuildingSkillEntry(
                    name=name,
                    unlock=stages[index_] if index_ < len(stages) else None,
                    slot=f"{index_ + 1}-1",
                    group=index_ + 1,
                    variant=1,
                )
                for index_, name in enumerate(names)
            )
            if entries:
                index[page.title] = entries
        self._smw_index = index
        return index or None

    def _local_operator(self, title: str) -> Any:
        entries = self.operator_service.match_local(title)
        return entries[0] if entries else None

    async def _resolve_remote(
        self, query: str
    ) -> tuple[str | None, tuple[BuildingSkillEntry, ...]]:
        """PRTS 直查 + 搜索：返回能解析出后勤技能的页面标题。"""
        titles: list[str] = []
        try:
            batch = await self.prts.wikitext([query])
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
        else:
            for page in batch.pages:
                if not page.missing:
                    titles.append(page.title)
                    self._last_page = _PageRef(title=page.title, refs=list(batch.source_refs()))
        try:
            found = await self.prts.search(query, limit=5)
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
        else:
            for item in found.query.get("search") or []:
                if isinstance(item, Mapping) and isinstance(item.get("title"), str):
                    titles.append(str(item["title"]))
        for title in titles:
            entries = await self._operator_entries(title)
            if entries:
                return title, entries
        return None, ()

    def _match_candidates(
        self,
        owned: Sequence[Mapping[str, Any]],
        entries_by_operator: Mapping[str, tuple[BuildingSkillEntry, ...]],
        table: Mapping[str, BuildingSkill],
    ) -> dict[str, list[dict[str, Any]]]:
        """房间键 → 候选人（已解锁且同槽位只留最高档）。"""
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in owned:
            name = str(row.get("name") or "").strip()
            entries = entries_by_operator.get(name)
            if not entries:
                continue
            elite = _int(row.get("elite")) or 0
            level = _int(row.get("level")) or 0
            effective = _effective_skills(entries, elite=elite, level=level)
            if not effective:
                continue
            by_room: dict[str, list[dict[str, Any]]] = {}
            for entry in effective:
                item = table.get(entry.name)
                if item is None or not item.room:
                    continue
                by_room.setdefault(item.room, []).append(
                    {
                        "name": entry.name,
                        "room": item.room,
                        "unlock": entry.unlock,
                        "description": item.description,
                        "hints": efficiency_hints(item.description, room=item.room),
                        "efficiency": self._core_efficiency(entry.name),
                    }
                )
            if not by_room:
                continue
            candidate = {
                "name": name,
                "char_id": row.get("char_id"),
                "rarity": _int(row.get("rarity")),
                "elite": elite,
                "level": level,
                "skills": [],
            }
            for room, skills in by_room.items():
                row_payload = dict(candidate)
                row_payload["skills"] = skills
                buckets.setdefault(room, []).append(row_payload)
        for rows in buckets.values():
            rows.sort(
                key=lambda item: (
                    -(item.get("rarity") or 0),
                    -(item.get("elite") or 0),
                    str(item.get("name") or ""),
                )
            )
        return buckets

    def _room_rows(
        self,
        layout: str,
        shifts: int,
        candidates: Mapping[str, list[dict[str, Any]]],
        wanted: Sequence[str] | None,
    ) -> list[dict[str, Any]]:
        counts = {
            "trading": int(layout[0]),
            "manufacture": int(layout[1]),
            "power": int(layout[2]),
        }
        rows: list[dict[str, Any]] = []
        for key, label in (*_LAYOUT_ROOMS, *_FIXED_ROOMS):
            if wanted is not None and key not in wanted:
                continue
            count = counts.get(key) or _FIXED_ROOM_COUNTS.get(key, 1)
            slots = self._room_slots(key)
            rows.append(
                {
                    "room": label,
                    "key": key,
                    "count": count,
                    "slots": slots,
                    "operators_per_shift": count * slots,
                    "candidates": list(candidates.get(label, [])),
                    "candidates_total": len(candidates.get(label, [])),
                }
            )
        return rows

    # -- 内部：本机内核资源 -------------------------------------------------
    def _core_skills_map(self) -> dict[str, _CoreSkill]:
        if self._core_skills is not None:
            return self._core_skills
        payload = self._load_core_file(_CORE_INFRAST)
        skills: dict[str, _CoreSkill] = {}
        slots: dict[str, int] = {}
        if isinstance(payload, Mapping):
            for key, value in payload.items():
                if not isinstance(value, Mapping):
                    continue
                max_opers = _int(value.get("maxNumOfOpers"))
                if max_opers:
                    slots[str(key)] = max_opers
                for skill in (value.get("skills") or {}).values():
                    if not isinstance(skill, Mapping):
                        continue
                    names = tuple(
                        str(name) for name in (skill.get("name") or []) if isinstance(name, str)
                    )
                    ids = tuple(
                        str(item) for item in (skill.get("operatorIds") or []) if isinstance(item, str)
                    )
                    efficient = skill.get("efficient")
                    core = _CoreSkill(
                        names=names,
                        operator_ids=ids,
                        efficient=efficient if isinstance(efficient, Mapping) else {},
                    )
                    for name in names:
                        skills.setdefault(name, core)
        self._core_skills = skills
        self._core_room_slots = slots
        return skills

    def _core_efficiency(self, name: str) -> dict[str, Any] | None:
        core = self._core_skills_map().get(name)
        if core is None or not core.efficient:
            return None
        return dict(core.efficient)

    def _room_slots(self, key: str) -> int:
        self._core_skills_map()
        core_key = _CORE_ROOM_KEYS.get(key, key.capitalize())
        return self._core_room_slots.get(core_key, _ROOM_SLOTS.get(key, 1))

    def _operator_name(self, char_id: str) -> str | None:
        entry = self.operator_service.char_entry(char_id)
        return entry.name if entry is not None else None

    def _load_core_file(self, name: Path) -> Any:
        roots = self._core_roots or list(iter_resource_roots())
        for root in reversed(roots):  # 增量层在后，反向遍历取生效的那份
            path = Path(root) / name
            if not path.is_file():
                continue
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("本机资源 %s 读不出来：%s", path, name)
                continue
        return None


#: 最近一次干员页取数的出处（给来源信封用）。
@dataclass(frozen=True, slots=True)
class _PageRef:
    title: str
    refs: list[SourceRef]


def _cargo_rows(result: Any) -> list[Any]:
    """Cargo 响应 → 行列表（``cargoquery`` 的每项是 ``{"title": {...}}``）。"""
    data = result.data if hasattr(result, "data") else result
    if not isinstance(data, Mapping):
        return []
    rows = data.get("cargoquery")
    return list(rows) if isinstance(rows, list) else []


def _effective_skills(
    entries: Sequence[BuildingSkillEntry], *, elite: int, level: int
) -> list[BuildingSkillEntry]:
    """账号练度能满足的技能：同一槽位只留解锁阶段最高的那一档。"""
    by_group: dict[Any, list[BuildingSkillEntry]] = {}
    for entry in entries:
        by_group.setdefault(entry.group if entry.group is not None else entry.slot, []).append(entry)
    effective: list[BuildingSkillEntry] = []
    for group_entries in by_group.values():
        satisfied = [entry for entry in group_entries if _unlocked(entry, elite=elite, level=level)]
        if not satisfied:
            continue
        effective.append(
            max(
                satisfied,
                key=lambda entry: (
                    entry.elite_requirement if entry.elite_requirement is not None else -1,
                    entry.variant or 0,
                ),
            )
        )
    effective.sort(key=lambda entry: (entry.group or 0, entry.variant or 0))
    return effective


def _unlocked(entry: BuildingSkillEntry, *, elite: int, level: int) -> bool:
    """``阶段`` 认得出就按它比；认不出的写法不拦（不猜含义，交给模型看原文）。"""
    needed_elite = entry.elite_requirement
    if needed_elite is not None and elite < needed_elite:
        return False
    needed_level = entry.level_requirement
    if needed_level is not None and level < needed_level:
        return False
    return True


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


#: 进程内共享的默认门面。
_DEFAULT: BuildingService | None = None
#: 带注入的装配（工具的 app.state 里没有 building_service 时用）；按注入对象的身份缓存最近
#: 一次，让 Cargo 整表与干员页的进程内缓存跨工具调用复用，又不会长期持有旧装配。
_INJECTED: dict[tuple[int, int], BuildingService] = {}


def default_building_service(
    *, resource_service: Any = None, box: Any = None
) -> BuildingService:
    """默认门面；注入 ``resource_service``/``box`` 时返回（缓存过的）独立实例。"""
    global _DEFAULT
    if resource_service is None and box is None:
        if _DEFAULT is None:
            _DEFAULT = BuildingService()
        return _DEFAULT
    key = (id(resource_service), id(box))
    service = _INJECTED.get(key)
    if service is None:
        service = BuildingService(resource_service=resource_service, box=box)
        _INJECTED.clear()
        _INJECTED[key] = service
    return service

