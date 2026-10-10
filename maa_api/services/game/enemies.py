"""敌人（规格 §6.4）：PRTS 敌人页，级别 0 以上先补齐再输出。

敌人没有本机兜底（规格 §3.5）：PRTS 不可用时直接报错，不拿别处的数据冒充。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef, build_envelope
from maa_api.services.external.prts import PrtsClient
from maa_api.services.prts_parse.enemy import EnemyPage, merge_levels, parse_enemy_page
from maa_api.settings import Settings, get_settings

__all__ = ["EnemyService", "default_enemy_service", "LEVEL_FIELD_MAP", "RESISTANCE_FIELD_MAP"]

#: `{{敌人信息/levelcontent}}` 的原始键 → 输出键（规格 §6.4 的字段白名单）。
LEVEL_FIELD_MAP: dict[str, str] = {
    "名称": "name",
    "地位": "rank",
    "种类": "category",
    "描述": "description",
    "攻击方式": "attack_type",
    "行动方式": "move_type",
    "数量": "count",
    "攻击范围半径": "attack_range_radius",
    "最大生命值": "hp",
    "攻击力": "atk",
    "防御力": "def",
    "法术抗性": "res",
    "移动速度": "move_speed",
    "攻击速度": "attack_speed",
    "攻击间隔": "attack_interval",
    "生命恢复速度": "hp_recovery",
    "sp恢复速度": "sp_recovery",
    "重量等级": "weight",
    "损伤抵抗": "damage_resistance",
    "元素抗性": "element_resistance",
    "基础嘲讽等级": "taunt_level",
}

#: 异常抗性：原始键 → 输出键。
RESISTANCE_FIELD_MAP: dict[str, str] = {
    "眩晕抗性": "stun",
    "沉默抗性": "silence",
    "沉睡抗性": "sleep",
    "冻结抗性": "freeze",
    "浮空抗性": "levitate",
    "战栗抗性": "frighten",
    "恐惧抗性": "fear",
    "麻痹抗性": "paralysis",
    "诱导抗性": "attract",
    "传送抗性": "teleport",
    "缚地抗性": "ground_bind",
}

#: 数值型字段：能转成数字就转（模型要拿来比较）。
_NUMERIC = frozenset(
    {
        "count",
        "hp",
        "atk",
        "def",
        "res",
        "move_speed",
        "attack_speed",
        "attack_interval",
        "hp_recovery",
        "sp_recovery",
        "weight",
        "damage_resistance",
        "element_resistance",
        "taunt_level",
        "attack_range_radius",
    }
)


class EnemyService:
    """敌人情报的领域服务。"""

    def __init__(
        self,
        *,
        prts: PrtsClient | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._prts = prts
        self._settings_provider = settings_provider

    @property
    def prts(self) -> PrtsClient:
        return self._prts or PrtsClient()

    async def get(self, name: str, *, level: int | None = None) -> dict[str, Any]:
        """取一个敌人的各级数值；``level`` 只返回那一级。"""
        if level is not None and not 0 <= level <= 2:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"level 只能是 0–2，收到 {level}",
                {"level": level},
            )
        page, sources = await self._load(name)
        merged = merge_levels(page)
        if level is not None:
            selected = [item for item in merged if item.get("index") == level]
            if not selected:
                raise AppError(
                    ErrorCode.GAME_DATA_NOT_FOUND,
                    f"{page.name or name} 没有级别 {level} 的数据",
                    {"name": name, "level": level, "levels": [item.get("index") for item in merged]},
                )
            merged = selected

        return {
            "query": name,
            "enemy": {
                "title": page.title,
                "enemy_id": page.enemy_id,
                "name": page.name,
                "index": page.index,
                "rank": page.rank,
                "category": page.category,
                "damage_type": page.damage_type,
                "attack_type": page.attack_type,
                "move_type": page.move_type,
                "description": page.description,
                "abilities": page.abilities,
                "debut": page.debut,
            },
            "levels": [_level(item) for item in merged],
            "levels_total": len(merged),
            **build_envelope(sources, unavailable=[] if page.levels else ["levels"]),
        }

    async def _load(self, name: str) -> tuple[EnemyPage, list[SourceRef]]:
        """定位敌人页：重定向/直查 → 分类确认 → 搜索。"""
        batch = await self.prts.wikitext([name])
        page = batch.pages[0] if batch.pages else None
        if page is not None and not page.missing and await self._is_enemy(page.title):
            return parse_enemy_page(page.content, title=page.title), list(batch.source_refs())

        found = await self.prts.search(name, limit=5)
        for item in found.query.get("search") or []:
            if not isinstance(item, dict) or not isinstance(item.get("title"), str):
                continue
            title = str(item["title"])
            if not await self._is_enemy(title):
                continue
            candidate = await self.prts.wikitext([title])
            if candidate.pages and not candidate.pages[0].missing:
                return (
                    parse_enemy_page(candidate.pages[0].content, title=candidate.pages[0].title),
                    list(candidate.source_refs()),
                )
        raise AppError(
            ErrorCode.GAME_DATA_NOT_FOUND,
            f"找不到敌人：{name}。可以试名称或 enemy_xxx",
            {"name": name},
        )

    async def _is_enemy(self, title: str) -> bool:
        try:
            mapping = await self.prts.categories([title])
        except AppError:
            return False
        return any("敌人" in item for item in mapping.get(title) or ())


def _level(item: Mapping[str, Any]) -> dict[str, Any]:
    """一级的原始字段 → 输出形状（未知键原样留在 ``extra``，不静默丢）。"""
    payload: dict[str, Any] = {"index": item.get("index")}
    extra: dict[str, Any] = {}
    resistances: dict[str, str] = {}
    for key, value in item.items():
        if key in {"index", "技能", "天赋"}:
            continue
        target = LEVEL_FIELD_MAP.get(key)
        if target is None:
            target = RESISTANCE_FIELD_MAP.get(key)
            if target is not None:
                resistances[target] = str(value)
                continue
            extra[str(key)] = value
            continue
        payload[target] = _value(target, value)
    payload["resistances"] = resistances
    payload["skills"] = [
        {
            "slot": skill.get("slot"),
            "name": skill.get("name"),
            "effect": skill.get("effect"),
            "sp_initial": skill.get("sp_initial"),
            "sp_cost": skill.get("sp_cost"),
        }
        for skill in item.get("技能") or []
    ]
    payload["talents"] = item.get("天赋")
    if extra:
        payload["extra"] = extra
    return payload


def _value(target: str, value: Any) -> Any:
    """数值字段尽量转数字；``无``/``-`` 这类文本原样保留。"""
    if target not in _NUMERIC:
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() else number


#: 进程内共享的默认门面。
_DEFAULT: EnemyService | None = None


def default_enemy_service() -> EnemyService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = EnemyService()
    return _DEFAULT


#: 供测试引用的取值域说明。
LEVELS_SUPPORTED: Sequence[int] = (0, 1, 2)
