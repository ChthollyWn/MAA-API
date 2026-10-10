"""干员（规格 §6.3）：本机 ``battle_data.json`` 定位 + PRTS 干员页字段白名单。

名字解析顺序（规格 §6.3）：本机 ``battle_data.json`` 精确匹配 → PRTS 重定向后属于
``分类:干员`` → 搜索并按分类过滤。有多个候选时返回候选列表，不替模型选。

攻击范围用**本机** ``battle_data.json`` 的 ``ranges``（那才是内核与作业用的网格），并标
``source=resource``；其余字段来自 PRTS。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef, build_envelope
from maa_api.services.external.prts import PrtsClient
from maa_api.services.prts_parse.building import parse_operator_building_skills
from maa_api.services.prts_parse.operator import (
    MaterialCost,
    OperatorModule,
    OperatorPage,
    OperatorSkill,
    OperatorTalent,
    parse_operator_page,
)
from maa_api.services.resource_index import iter_resource_roots
from maa_api.settings import Settings, get_settings

__all__ = [
    "ALL_SECTIONS",
    "DEFAULT_SECTIONS",
    "OperatorService",
    "default_operator_service",
    "render_range_grid",
]

#: `sections` 的取值域（规格 §6.3）；`building` 在 M28 加入。
ALL_SECTIONS = ("basic", "stats", "skills", "talents", "potentials", "modules", "costs", "building")
DEFAULT_SECTIONS = ("basic", "stats", "skills", "talents", "modules")

#: 单次结果的目标体积上限（规格 §6.3）：超了先砍技能描述与模组文本。
MAX_RESULT_BYTES = 16 * 1024

#: 长描述截断的长度。
MAX_TEXT_CHARS = 400

_BATTLE_DATA = Path("battle_data.json")


@dataclass(frozen=True, slots=True)
class _CharEntry:
    """本机 `battle_data.json` 里的一条干员。"""

    char_id: str
    name: str
    names: tuple[str, ...]
    rarity: int | None
    profession: str | None
    position: str | None
    range_ids: tuple[str, ...]
    sub_profession: str | None


class OperatorService:
    """干员情报的领域服务。"""

    def __init__(
        self,
        *,
        prts: PrtsClient | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
        battle_data_path: str | Path | None = None,
        building: Any = None,
    ) -> None:
        self._prts = prts
        self._settings_provider = settings_provider
        self._battle_path = Path(battle_data_path) if battle_data_path else None
        self._building = building
        self._chars: tuple[_CharEntry, ...] | None = None
        self._ranges: dict[str, list[list[int]]] | None = None

    @property
    def prts(self) -> PrtsClient:
        return self._prts or PrtsClient()

    @property
    def building(self) -> Any:
        """基建技能数据（M28 的 `building` 段）；缺省用模块默认门面。

        注入了 ``prts``（测试与多来源装配）时复用同一个客户端，别让 `building` 段偷偷
        走进程默认门面去打真实网络。
        """
        if self._building is None:
            from maa_api.services.game.building import (
                BuildingService,
                default_building_service,
            )

            if self._prts is not None:
                self._building = BuildingService(prts=self._prts)
            else:
                self._building = default_building_service()
        return self._building

    def char_entry(self, char_id: str) -> _CharEntry | None:
        """按 char_id 取本机 ``battle_data.json`` 的条目（基建技能的拥有者要用）。"""
        chars, _ = self._load_battle_data()
        return next((entry for entry in chars if entry.char_id == char_id), None)

    # -- 名字解析 ---------------------------------------------------------
    def _load_battle_data(self) -> tuple[tuple[_CharEntry, ...], dict[str, list[list[int]]]]:
        """读本机 `battle_data.json`（读不到就返回空表：PRTS 仍然可用）。"""
        if self._chars is not None and self._ranges is not None:
            return self._chars, self._ranges
        roots = [self._battle_path] if self._battle_path else [
            Path(root) / _BATTLE_DATA for root in iter_resource_roots()
        ]
        payload: Mapping[str, Any] = {}
        for path in roots:
            if path is None or not path.is_file():
                continue
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(loaded, dict) and isinstance(loaded.get("chars"), dict):
                payload = loaded
                break
        chars: list[_CharEntry] = []
        for char_id, raw in (payload.get("chars") or {}).items():
            if not isinstance(raw, dict):
                continue
            names = tuple(
                str(raw.get(key))
                for key in ("name", "name_en", "name_jp", "name_kr", "name_tw")
                if isinstance(raw.get(key), str) and raw.get(key)
            )
            if not names:
                continue
            range_ids = raw.get("rangeId")
            chars.append(
                _CharEntry(
                    char_id=str(char_id),
                    name=str(raw.get("name") or names[0]),
                    names=(*names, str(char_id)),
                    rarity=raw.get("rarity") if isinstance(raw.get("rarity"), int) else None,
                    profession=raw.get("profession") if isinstance(raw.get("profession"), str) else None,
                    position=raw.get("position") if isinstance(raw.get("position"), str) else None,
                    range_ids=tuple(str(item) for item in range_ids)
                    if isinstance(range_ids, list)
                    else (),
                    sub_profession=raw.get("subProfessionId")
                    if isinstance(raw.get("subProfessionId"), str)
                    else None,
                )
            )
        ranges = {
            str(key): value
            for key, value in (payload.get("ranges") or {}).items()
            if isinstance(value, list)
        }
        self._chars, self._ranges = tuple(chars), ranges
        return self._chars, self._ranges

    def match_local(self, query: str) -> list[_CharEntry]:
        """本机精确匹配：中文名、四种外文名、char_id（大小写不敏感）。"""
        chars, _ = self._load_battle_data()
        needle = query.strip().casefold()
        return [entry for entry in chars if needle in {name.casefold() for name in entry.names}]

    async def _resolve(self, query: str) -> tuple[_CharEntry | None, str, list[dict[str, Any]]]:
        """返回 ``(本机条目, 匹配方式, 候选列表)``；候选非空且多于一条时不替模型选。"""
        local = self.match_local(query)
        if len(local) == 1:
            return local[0], "local_name", []
        if len(local) > 1:
            return None, "candidates", [_candidate(entry) for entry in local]

        # PRTS 重定向：别名页会跳到正式页面，再看它是不是干员分类。
        try:
            batch = await self.prts.wikitext([query])
        except AppError:
            batch = None
        if batch is not None and batch.pages and not batch.pages[0].missing:
            page = batch.pages[0]
            categories = await self._categories(page.title)
            if _is_operator(categories):
                entry = self._local_of(page.title)
                return entry, "prts_redirect", []

        # 命中的页面不是干员（实测：搜「爱国者」会命中敌人页）：继续走搜索，
        # 不能拿敌人页当干员页解析，否则会返回一条字段全空的假记录。
        found = await self.prts.search(query, limit=5)
        candidates: list[dict[str, Any]] = []
        for item in found.query.get("search") or []:
            if not isinstance(item, dict) or not isinstance(item.get("title"), str):
                continue
            title = str(item["title"])
            if _is_operator(await self._categories(title)):
                candidates.append({"title": title, "char_id": _char_id_of(self._local_of(title))})
        if len(candidates) == 1:
            entry = self._local_of(str(candidates[0]["title"]))
            return entry, "search", []
        if candidates:
            return None, "candidates", candidates
        raise AppError(
            ErrorCode.GAME_DATA_NOT_FOUND,
            f"找不到干员：{query}。可以试中文名、英文名或 char_id",
            {"name": query},
        )

    def _local_of(self, title: str) -> _CharEntry | None:
        """PRTS 页面标题 → 本机条目（标题可能带消歧后缀，所以用前缀匹配兜一次）。"""
        chars, _ = self._load_battle_data()
        exact = [entry for entry in chars if entry.name == title]
        if exact:
            return exact[0]
        prefixed = [entry for entry in chars if title.startswith(entry.name)]
        return prefixed[0] if len(prefixed) == 1 else None

    async def _categories(self, title: str) -> tuple[str, ...]:
        try:
            mapping = await self.prts.categories([title])
        except AppError:
            return ()
        return tuple(mapping.get(title) or ())

    # -- 取数 -------------------------------------------------------------
    async def get(
        self,
        name: str,
        *,
        sections: Sequence[str] | None = None,
        skill_detail: str = "max",
    ) -> dict[str, Any]:
        """取一名干员的字段组；多候选时只返回候选列表。"""
        groups = _normalize_sections(sections)
        if skill_detail not in {"max", "all"}:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"skill_detail 只能是 max 或 all，收到 {skill_detail}",
                {"skill_detail": skill_detail},
            )
        entry, matched_by, candidates = await self._resolve(name)
        if candidates:
            return {
                "query": name,
                "operator": None,
                "matched_by": matched_by,
                "candidates": candidates,
                "candidates_total": len(candidates),
                "hint": "有多个同名候选，用完整的中文名或 char_id 再查一次。",
                **build_envelope(),
            }

        page: OperatorPage | None = None
        page_wikitext: str | None = None
        sources: list[SourceRef] = []
        unavailable: list[str] = []
        title = entry.name if entry is not None else name
        try:
            batch = await self.prts.wikitext([title])
            if batch.pages and not batch.pages[0].missing:
                sources.extend(batch.source_refs())
                page_wikitext = batch.pages[0].content
                page = parse_operator_page(page_wikitext, title=batch.pages[0].title)
        except AppError as exc:
            if exc.code in _DEGRADABLE:
                unavailable.extend(
                    group for group in groups if group not in {"basic", "stats"}
                )
            else:
                raise

        payload: dict[str, Any] = {
            "query": name,
            "operator": _identity(entry, page, name),
            "matched_by": matched_by,
            "candidates": [],
            "candidates_total": 1,
        }
        _, ranges = self._load_battle_data()

        if "basic" in groups:
            payload["basic"] = _basic(entry, page)
            if page is None:
                payload["basic"]["source"] = "resource"
        if "stats" in groups:
            payload["stats"] = _stats(entry, page, ranges)
        if "skills" in groups:
            if page is not None:
                payload["skills"] = [_skill(skill, detail=skill_detail) for skill in page.skills]
            else:
                unavailable.append("skills")
        if "talents" in groups:
            if page is not None:
                payload["talents"] = [_talent(item) for item in page.talents]
            else:
                unavailable.append("talents")
        if "potentials" in groups:
            if page is not None:
                payload["potentials"] = dict(page.potentials)
            else:
                unavailable.append("potentials")
        if "modules" in groups:
            if page is not None:
                payload["modules"] = [_module(item) for item in page.modules]
            else:
                unavailable.append("modules")
        if "costs" in groups:
            if page is not None:
                payload["costs"] = _costs(page)
            else:
                unavailable.append("costs")
        if "building" in groups:
            await self._add_building(payload, page_wikitext, unavailable)

        if page is None and not unavailable:
            unavailable.append("page")
        payload.update(
            build_envelope(sources, unavailable=_dedupe(unavailable))
        )
        return _shrink(payload)


    async def _add_building(
        self,
        payload: dict[str, Any],
        wikitext: str | None,
        unavailable: list[str],
    ) -> None:
        """M28 的 `building` 段：干员页 ``{{后勤技能}}` + Cargo 的房间与描述（规格 §8.3）。"""
        skills = parse_operator_building_skills(wikitext or "")
        if not skills:
            unavailable.append("building")
            return
        try:
            info = await self.building.skill_info([skill.name for skill in skills])
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
            info = {}
            unavailable.append("building")
        rows: list[dict[str, Any]] = []
        for skill in skills:
            detail = info.get(skill.name) or {}
            rows.append(
                {
                    "name": skill.name,
                    "room": detail.get("room"),
                    "unlock": skill.unlock,
                    "description": detail.get("description"),
                    "hints": detail.get("hints") or [],
                    "efficiency": detail.get("efficiency"),
                    "slot": skill.slot,
                }
            )
        if any(row["room"] is None for row in rows):
            unavailable.append("building")
        payload["building"] = rows


def _candidate(entry: _CharEntry) -> dict[str, Any]:
    return {
        "title": entry.name,
        "char_id": entry.char_id,
        "rarity": entry.rarity,
        "profession": entry.profession,
    }


def _char_id_of(entry: _CharEntry | None) -> str | None:
    return entry.char_id if entry is not None else None


def _is_operator(categories: Iterable[str]) -> bool:
    return any("干员" in str(item) for item in categories)


def _normalize_sections(sections: Sequence[str] | None) -> tuple[str, ...]:
    if sections is None:
        return DEFAULT_SECTIONS
    cleaned: list[str] = []
    for group in sections:
        if group not in ALL_SECTIONS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"sections 只能是 {'、'.join(ALL_SECTIONS)} 的子集，收到 {group}",
                {"sections": list(sections)},
            )
        if group not in cleaned:
            cleaned.append(group)
    return tuple(cleaned) or DEFAULT_SECTIONS


#: 可以降级的外部错误：只影响字段组，不把整个调用变成错误（§3.5）。
_DEGRADABLE = frozenset(
    {
        ErrorCode.EXTERNAL_SOURCE_DISABLED,
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
        # 规格 §3.5 把「解析失败」与断网、限流、熔断、来源关闭并列：页面取回来但结构
        # 不符时同样走本机兜底，而不是让整个工具调用失败。
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
    }
)


def _identity(entry: _CharEntry | None, page: OperatorPage | None, fallback: str) -> dict[str, Any]:
    return {
        "name": (page.name if page and page.name else None)
        or (entry.name if entry else fallback),
        "char_id": (page.char_id if page and page.char_id else None)
        or (entry.char_id if entry else None),
        "rarity": (page.rarity if page and page.rarity else None)
        or (entry.rarity if entry else None),
        "profession": (page.profession if page and page.profession else None)
        or (_profession_cn(entry.profession) if entry else None),
    }


_PROFESSION_CN = {
    "WARRIOR": "近卫",
    "SNIPER": "狙击",
    "CASTER": "术师",
    "MEDIC": "医疗",
    "SUPPORT": "辅助",
    "TANK": "重装",
    "SPECIAL": "特种",
    "PIONEER": "先锋",
}

_POSITION_CN = {"MELEE": "近战位", "RANGED": "远程位"}


def _profession_cn(value: str | None) -> str | None:
    """本机职业名（WARRIOR）→ 中文（近卫）；未知原样返回。"""
    if not value:
        return None
    return _PROFESSION_CN.get(value, value)


def _basic(entry: _CharEntry | None, page: OperatorPage | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": None,
        "char_id": None,
        "rarity": None,
        "profession": None,
        "branch": None,
        "trait": None,
        "tags": [],
        "position": None,
        "obtain": None,
        "release_time": None,
        "info_number": None,
        "source": "prts",
    }
    if entry is not None:
        payload.update(
            {
                "name": entry.name,
                "char_id": entry.char_id,
                "rarity": entry.rarity,
                "profession": _profession_cn(entry.profession),
                "position": _POSITION_CN.get(entry.position or "", entry.position),
            }
        )
    if page is not None:
        payload.update(
            {
                "name": page.name or payload["name"],
                "char_id": page.char_id or payload["char_id"],
                "rarity": page.rarity or payload["rarity"],
                "profession": page.profession or payload["profession"],
                "branch": page.branch,
                "trait": page.trait,
                "tags": list(page.tags),
                "position": page.position or payload["position"],
                "obtain": page.obtain,
                "release_time": page.release_time,
                "info_number": page.info_number,
            }
        )
    return payload


def _stats(
    entry: _CharEntry | None,
    page: OperatorPage | None,
    ranges: Mapping[str, list[list[int]]],
) -> dict[str, Any]:
    stats = dict(page.stats) if page is not None else {}
    payload: dict[str, Any] = {
        "deploy_time": stats.get("再部署"),
        "cost": stats.get("部署费用"),
        "block": stats.get("阻挡数"),
        "attack_interval": stats.get("攻击速度"),
        "trust_bonus": {
            key.split("_", 1)[1]: _int(value)
            for key, value in stats.items()
            if key.startswith("信赖加成_") and _int(value) is not None
        },
        "elite_phases": [],
        "source": "prts",
    }
    range_ids = entry.range_ids if entry is not None else ()
    for phase in (0, 1, 2):
        max_level = _int(stats.get(f"精英{phase}_满级"))
        phase_stats = {
            "hp": _int(stats.get(f"精英{phase}_满级_生命上限")),
            "atk": _int(stats.get(f"精英{phase}_满级_攻击")),
            "def": _int(stats.get(f"精英{phase}_满级_防御")),
            "res": _int(stats.get(f"精英{phase}_满级_法术抗性")),
        }
        if max_level is None and all(value is None for value in phase_stats.values()):
            continue
        range_id = range_ids[phase] if phase < len(range_ids) else None
        grid = render_range_grid(ranges.get(str(range_id)) or [])
        payload["elite_phases"].append(
            {
                "elite": phase,
                "max_level": max_level,
                **phase_stats,
                "range_id": range_id,
                "range": grid,
                "range_source": "resource" if grid else None,
            }
        )
    if not payload["elite_phases"]:
        # PRTS 不可用（或页面缺字段）时，攻击范围仍能从本机 `battle_data.json` 给出来
        # （规格 §6.3：「本机能给 basic 的一部分与攻击范围」）。
        payload["source"] = "resource"
        for phase, range_id in enumerate(range_ids):
            grid = render_range_grid(ranges.get(str(range_id)) or [])
            payload["elite_phases"].append(
                {
                    "elite": phase,
                    "max_level": None,
                    "hp": None,
                    "atk": None,
                    "def": None,
                    "res": None,
                    "range_id": range_id,
                    "range": grid,
                    "range_source": "resource" if grid else None,
                }
            )
    return payload


def render_range_grid(offsets: Sequence[Sequence[int]]) -> list[str]:
    """本机 `ranges` 的偏移表 → 文字网格。

    ``#`` 是攻击范围、``@`` 是干员自身所在格、``.`` 是范围外。行自上而下、列自左而右，
    与 `send_stage_map` 的读法一致（坐标以格为单位）。
    """
    cells: set[tuple[int, int]] = set()
    for item in offsets:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                cells.add((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
    if not cells:
        return []
    cells.add((0, 0))
    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    rows: list[str] = []
    for y in range(max(ys), min(ys) - 1, -1):
        row = ""
        for x in range(min(xs), max(xs) + 1):
            if (x, y) == (0, 0):
                row += "@"
            elif (x, y) in cells:
                row += "#"
            else:
                row += "."
        rows.append(row)
    return rows


def _skill(skill: OperatorSkill, *, detail: str) -> dict[str, Any]:
    levels = skill.levels
    if detail == "max":
        levels = tuple(level for level in levels if level.level in {"7", "专精1", "专精2", "专精3"})
    return {
        "name": skill.name,
        "charge_type": skill.charge_type,
        "trigger_type": skill.trigger_type,
        "levels": [
            {
                "level": level.level,
                "sp_initial": level.sp_initial,
                "sp_cost": level.sp_cost,
                "duration": level.duration,
                "description": _clip(level.description),
            }
            for level in levels
        ],
        "note": _clip(skill.note),
    }


def _talent(talent: OperatorTalent) -> dict[str, Any]:
    return {
        "name": talent.name,
        "condition": talent.condition,
        "effect": _clip(talent.effect),
    }


def _module(module: OperatorModule) -> dict[str, Any]:
    return {
        "name": module.name,
        "type_code": module.type_code,
        "trait_added": module.trait_added,
        "trait": _clip(module.trait),
        "level_stats": [
            {"level": level, "stats": dict(stats)} for level, stats in module.level_stats
        ],
        "talents": [_talent(item) for item in module.talents],
        "potential_talents": [_talent(item) for item in module.potential_talents],
        "costs": [
            {"level": level, "materials": _materials(items)}
            for level, items in module.costs
        ],
    }


def _costs(page: OperatorPage) -> dict[str, Any]:
    return {
        "elite": {
            key: _materials(items) for key, items in page.elite_costs.items()
        },
        "skill_levels": {
            key: _materials(items) for key, items in page.skill_costs.items()
        },
        "modules": [
            {"name": module.name, "levels": [
                {"level": level, "materials": _materials(items)}
                for level, items in module.costs
            ]}
            for module in page.modules
            if module.costs
        ],
        "note": "材料数量已归一成整数（`3w` = 30000 龙门币）",
    }


def _materials(items: Sequence[MaterialCost]) -> list[dict[str, Any]]:
    return [{"name": item.name, "amount": item.amount} for item in items]


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= MAX_TEXT_CHARS:
        return value
    return value[: MAX_TEXT_CHARS - 1] + "…"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def _shrink(payload: dict[str, Any]) -> dict[str, Any]:
    """体积超出 16 KB 时按「先砍描述、再砍模组文本」的顺序收敛（规格 §6.3）。"""
    import json as _json

    def size() -> int:
        return len(_json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    if size() <= MAX_RESULT_BYTES:
        return payload
    for section in ("skills", "modules"):
        for item in payload.get(section) or []:
            if isinstance(item, dict) and "description" in item:
                item["description"] = _clip(item["description"])
        for item in payload.get("skills") or []:
            for level in item.get("levels") or []:
                level["description"] = None
        if size() <= MAX_RESULT_BYTES:
            return payload
    payload["truncated"] = {
        "field": "result",
        "total_chars": size(),
        "note": "结果超出 16 KB，长描述已截断；需要细节请按 sections 分组再查一次。",
    }
    return payload


#: 进程内共享的默认门面。
_DEFAULT: OperatorService | None = None


def default_operator_service() -> OperatorService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = OperatorService()
    return _DEFAULT
