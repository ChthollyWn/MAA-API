"""升级经验与龙门币（规格 §7.6、§9.1 第 10 条）：拆包常量表 → 从当前到目标的增量。

数据来源与结构（2026-10-10 实测，见 `tests/fixtures/gamedata/gamedata_const.json`）：

- ``zh_CN/gamedata/excel/gamedata_const.json`` 的 ``characterExpMap``：每条经验曲线一条
  **90 项**数组，第 ``i`` 项是「升到 i+1 级」所需的经验（``i = level - 1``，1 级是 0）；
- 同表的 ``characterUpgradeCostMap``：每条曲线 **89 项**，第 ``i`` 项是「从 i+1 级升到
  i+2 级」的龙门币；
- 同表的 ``evolveGoldCost``：6 行（1★–6★），每行 2 项，是精一、精二的龙门币（``-1``
  表示该稀有度没有这一档，如 1★/2★ 不能精一）；
- 同表的 ``maxLevel``：6 行，每行的项数是该稀有度的精英阶段数，值是各阶段满级
  （实测 1★/2★ ``[30]``、3★ ``[40,55]``、4★ ``[45,60,70]``、5★ ``[50,70,80]``、
  6★ ``[50,80,90]``，与 ``character_table.json`` 的 ``phases[*].maxLevel`` 逐条一致）。

``characterExpMap`` / ``characterUpgradeCostMap`` 只有 **3 行**，是「三条经验曲线」，按稀有度
取用：1★ 用第 0 条、2★ 用第 1 条、3★–6★ 用第 2 条（见 :data:`_CURVE_BY_RARITY`）。

这一条映射**不是**表里写死的字段：拆包数据里没有「稀有度 → 曲线」的对应表，
``character_table.json`` 也没有 ``growCurve``。能直接从数据里读出来的只有各曲线的**有效区间**
（第 0 条到 49 级、第 1 条到 79 级、第 2 条到 89 级，即三条曲线的第 ``-1`` 起点分别是 50 /
80 / 90 级），它与 ``maxLevel`` 的 30 / 40–70 / 50–90 三档相容；3★–6★ 同档、1★/2★ 各自
一档是据此定下的**实现口径**。已知的不确定处只有 1★ 与 2★ 谁用第 0 / 第 1 条（两条曲线都
覆盖 30 级，差 3 点经验/级，且 1★ 升不了几级）；若实测发现反了，改 :data:`_CURVE_BY_RARITY`
一处即可。:meth:`LevelUpTable.table_digest` 把三条曲线的摘要暴露出来，便于与拆包数据对照复核。

打包数据整张取不到时用内置的官方数值表兜底（:data:`_BUILTIN_EXP_MAP` 等，逐项抄自同一次
抓取），此时 ``levelup_cost(...)["source"] == "builtin"``，并在 ``notes`` 里说明。

口径：**只算升级**的经验与龙门币；精一/精二的龙门币记在 ``elite_lmd``，不并进 ``lmd``
——``get_operator`` 的 ``costs`` 已经把精英化龙门币算进材料需求，并进来会重复计。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.gamedata import GamedataClient

__all__ = [
    "CONST_FILE",
    "ITEM_FILE",
    "MAX_RARITY",
    "MAX_LEVELS",
    "SOURCE_BUILTIN",
    "SOURCE_PACKED",
    "LevelUpTable",
    "default_level_up_table",
    "levelup_cost",
    "max_level_for",
]

#: 关卡常量表与道具表的名字（都要在 ``sources.GAMEDATA_FILES`` 白名单里）。
CONST_FILE = "gamedata_const.json"
ITEM_FILE = "item_table.json"

#: 星级上限（1★–6★）。
MAX_RARITY = 6

#: 三档来源标记（进 ``levelup_cost`` 的返回）。
SOURCE_PACKED = "packed"
SOURCE_BUILTIN = "builtin"
SOURCE_UNKNOWN = "unavailable"

#: 稀有度（1★–6★）→ 经验曲线行号（见模块说明的映射口径）。
#: 3★–6★ 共用第三条曲线：它的有效区间到 89 级，正好是 5★/6★ 的满级 80/90，也与 3★/4★
#: 的 55/70 相容；1★/2★ 的曲线只到 49 级，与它们的 30 级上限相容。
_CURVE_BY_RARITY: dict[int, int] = {1: 0, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2}

#: 稀有度 → 各精英阶段的满级（官方数值；打包数据取不到时用它兜底）。
MAX_LEVELS: Mapping[int, tuple[int, ...]] = {
    1: (30,),
    2: (30,),
    3: (40, 55),
    4: (45, 60, 70),
    5: (50, 70, 80),
    6: (50, 80, 90),
}

#: 作战记录（``expItems``）的 itemId → 每张的经验折算值（官方数值）。
BUILTIN_EXP_ITEMS: Mapping[str, int] = {
    "2001": 200,   # 基础作战记录
    "2002": 400,   # 初级作战记录
    "2003": 1000,  # 中级作战记录
    "2004": 2000,  # 高级作战记录
}


@dataclass(frozen=True, slots=True)
class LevelUpTable:
    """升级经验 / 龙门币表；数据可来自拆包常量表，也可来自内置兜底。

    两个 ``source`` 是给调用方拼 ``notes`` 与来源信封用的：``packed`` 是拆包数据，
    ``builtin`` 是内置表（拆包镜像取不到），``unavailable`` 是两者都没有（理论上不该出现）。
    """

    exp_map: tuple[tuple[int, ...], ...] = ()
    upgrade_lmd_map: tuple[tuple[int, ...], ...] = ()
    elite_lmd: tuple[tuple[int, ...], ...] = ()
    max_levels: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    exp_items: Mapping[str, int] = field(default_factory=dict)
    source: str = SOURCE_BUILTIN
    note: str = ""

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def builtin(cls, *, note: str = "") -> "LevelUpTable":
        """内置的官方数值表（单测与离线兜底）。"""
        return cls(
            exp_map=_BUILTIN_EXP_MAP,
            upgrade_lmd_map=_BUILTIN_UPGRADE_LMD_MAP,
            elite_lmd=_BUILTIN_ELITE_LMD,
            max_levels=MAX_LEVELS,
            exp_items=BUILTIN_EXP_ITEMS,
            source=SOURCE_BUILTIN,
            note=note or "拆包常量表未接入，用内置的官方数值表",
        )

    @classmethod
    def from_payloads(
        cls,
        const: Any,
        items: Any = None,
        *,
        note: str = "",
    ) -> "LevelUpTable":
        """从 ``gamedata_const.json`` / ``item_table.json`` 的原始 JSON 建表。

        结构不符（缺 ``characterExpMap`` / ``characterUpgradeCostMap``）抛
        ``EXTERNAL_RESPONSE_INVALID``，让调用方决定是降级到内置表还是报错。
        """
        if not isinstance(const, Mapping):
            raise _invalid("gamedata_const.json 顶层不是对象")
        exp_map = _int_rows(const.get("characterExpMap"))
        lmd_map = _int_rows(const.get("characterUpgradeCostMap"))
        if not exp_map or not lmd_map:
            raise _invalid("gamedata_const.json 缺 characterExpMap / characterUpgradeCostMap")
        # 精英化龙门币与各阶段满级缺失时用内置的官方数值补齐：这两项是稀有度的固定属性。
        elite = _int_rows(const.get("evolveGoldCost")) or _BUILTIN_ELITE_LMD
        levels = _level_rows(const.get("maxLevel")) or dict(MAX_LEVELS)
        exp_items = _exp_items(items)
        return cls(
            exp_map=exp_map,
            upgrade_lmd_map=lmd_map,
            elite_lmd=elite,
            max_levels=levels or dict(MAX_LEVELS),
            exp_items=exp_items,
            source=SOURCE_PACKED,
            note=note,
        )
    # -- 查询 -------------------------------------------------------------
    @property
    def available(self) -> bool:
        """能不能算：至少要有一条经验曲线与一条龙门币曲线。"""
        return bool(self.exp_map) and bool(self.upgrade_lmd_map)

    def max_level(self, rarity: int, elite: int) -> int | None:
        """某稀有度在某精英阶段的满级；查不到返回 ``None``。"""
        return max_level_for(rarity, elite, max_levels=self.max_levels)

    def exp_for(self, rarity: int, level: int) -> int:
        """从 ``level`` 升到 ``level+1`` 所需的经验（表外返回 0）。"""
        row = self._row(self.exp_map, rarity)
        return row[level - 1] if 0 < level - 1 < len(row) and row[level - 1] > 0 else 0

    def lmd_for(self, rarity: int, level: int) -> int:
        """从 ``level`` 升到 ``level+1`` 所需的龙门币（表外返回 0）。"""
        row = self._row(self.upgrade_lmd_map, rarity)
        return row[level - 1] if 0 < level - 1 < len(row) and row[level - 1] > 0 else 0

    def _row(self, table: Sequence[Sequence[int]], rarity: int) -> Sequence[int]:
        if not table:
            return ()
        curve = _CURVE_BY_RARITY.get(int(rarity), 0)
        index = curve if 0 <= curve < len(table) else 0
        return table[index]

    def cost(
        self,
        *,
        rarity: int,
        from_elite: int,
        from_level: int,
        to_elite: int,
        to_level: int,
    ) -> dict[str, Any]:
        """从 ``(from_elite, from_level)`` 到 ``(to_elite, to_level)`` 的经验与龙门币。

        - ``exp``：升级经验总和（作战记录的折算值按 :attr:`exp_items` 换算见
          :meth:`exp_cards`）；
        - ``lmd``：**只含升级**的龙门币；
        - ``elite_lmd``：精一 / 精二的龙门币（``get_operator`` 的 ``costs`` 已经算过，
          单独列出避免重复计）；
        - 目标低于当前、或精英差超过两级时返回全 0 并带 ``note``（不猜）。
        """
        result: dict[str, Any] = {
            "exp": 0,
            "lmd": 0,
            "elite_lmd": 0,
            "source": self.source,
            "note": None,
        }
        if not self.available:
            result["source"] = SOURCE_UNKNOWN
            result["note"] = self.note or "没有可用的升级经验与龙门币表"
            return result
        current_elite, current_level = int(from_elite), int(from_level)
        target_elite, target_level = int(to_elite), int(to_level)
        if target_elite < current_elite or (
            target_elite == current_elite and target_level < current_level
        ):
            result["note"] = "目标低于当前精英阶段或等级，未计升级消耗"
            return result
        if target_elite > current_elite + 2:
            result["note"] = "精英阶段最多是 2，越界时未计升级消耗"
            return result
        if not (0 <= current_elite <= 2 and 0 <= target_elite <= 2):
            result["note"] = "精英阶段只能是 0 / 1 / 2，越界时未计升级消耗"
            return result
        exp_row = self._row(self.exp_map, rarity)
        lmd_row = self._row(self.upgrade_lmd_map, rarity)
        if target_elite > current_elite:
            # 精英化发生在升级段之前：先升到当前阶段的满级，再精一/精二，然后从 1 级继续。
            for elite in range(current_elite + 1, target_elite + 1):
                cap = self.phase_cap(rarity, elite - 1)
                if cap is None:
                    result["note"] = (
                        f"没有 {rarity}★ 精英{elite - 1} 的满级数据，未计升级消耗"
                    )
                    return result
                result["elite_lmd"] += self.promotion_lmd(rarity, elite)
                result["exp"] += _sum_exp(exp_row, current_level, cap)
                result["lmd"] += _sum_lmd(lmd_row, current_level, cap)
                current_level = 1
            current_elite = target_elite
        result["exp"] += _sum_exp(exp_row, current_level, target_level)
        result["lmd"] += _sum_lmd(lmd_row, current_level, target_level)
        if self.note:
            result["note"] = self.note
        return result

    def phase_cap(self, rarity: int, elite: int) -> int | None:
        """某精英阶段的满级：优先用 ``maxLevel`` 表，缺了再从经验曲线的有效长度推。"""
        explicit = self.max_level(rarity, elite)
        if explicit is not None:
            return explicit
        row = self._row(self.exp_map, rarity)
        valid = [index + 1 for index, value in enumerate(row) if value > 0]
        return valid[-1] if valid else None

    def promotion_lmd(self, rarity: int, elite: int) -> int:
        """精 ``elite``（1 或 2）的龙门币；该稀有度没有这一档返回 0。"""
        if not self.elite_lmd:
            return 0
        index = int(rarity) - 1
        if not 0 <= index < len(self.elite_lmd):
            return 0
        row = self.elite_lmd[index]
        slot = int(elite) - 1
        if not 0 <= slot < len(row):
            return 0
        value = row[slot]
        return value if value > 0 else 0

    def exp_cards(self, exp: int) -> list[dict[str, Any]]:
        """升级经验折算成作战记录张数（从高级到低级贪心，向上取整到整张）。

        :returns: ``[{"item_id", "name", "count", "exp_each"}]``；没有 ``expItems`` 时给空表。
        """
        remaining = max(int(exp), 0)
        if not remaining or not self.exp_items:
            return []
        rows: list[dict[str, Any]] = []
        for item_id, each in sorted(
            self.exp_items.items(), key=lambda pair: (-int(pair[1]), str(pair[0]))
        ):
            if each <= 0:
                continue
            count = remaining // int(each)
            if count:
                rows.append(
                    {
                        "item_id": item_id,
                        "name": _EXP_ITEM_NAMES.get(item_id),
                        "count": count,
                        "exp_each": int(each),
                    }
                )
                remaining -= count * int(each)
        if remaining:
            # 贪心除不尽时（末位不是最小面额的整数倍）补一张最小的记录。
            smallest = min(
                (pair for pair in self.exp_items.items() if int(pair[1]) > 0),
                key=lambda pair: int(pair[1]),
                default=None,
            )
            if smallest is not None:
                rows.append(
                    {
                        "item_id": smallest[0],
                        "name": _EXP_ITEM_NAMES.get(smallest[0]),
                        "count": 1,
                        "exp_each": int(smallest[1]),
                    }
                )
        return rows

    def table_digest(self) -> dict[str, str]:
        """三条经验曲线与龙门币曲线的 sha256（前 12 位），供与拆包数据对照复核。"""
        return {
            "exp": _digest(self.exp_map),
            "upgrade_lmd": _digest(self.upgrade_lmd_map),
            "elite_lmd": _digest(self.elite_lmd),
        }

    def as_dict(self) -> dict[str, Any]:
        """给 ``plan_upgrade`` 的 ``notes`` / 来源信封用的摘要。"""
        return {
            "source": self.source,
            "available": self.available,
            "note": self.note,
            "rarity_curve": dict(_CURVE_BY_RARITY),
            "max_levels": {str(key): list(value) for key, value in self.max_levels.items()},
            "digest": self.table_digest(),
        }


def max_level_for(
    rarity: int, elite: int, *, max_levels: Mapping[int, tuple[int, ...]] | None = None
) -> int | None:
    """某稀有度在某精英阶段的满级；越界返回 ``None``（不猜）。"""
    table = max_levels if max_levels is not None else MAX_LEVELS
    levels = table.get(int(rarity))
    if not levels:
        return None
    index = int(elite)
    if not 0 <= index < len(levels):
        return None
    return int(levels[index])


def levelup_cost(
    *,
    rarity: int,
    from_elite: int,
    from_level: int,
    to_elite: int,
    to_level: int,
    table: LevelUpTable | None = None,
) -> dict[str, Any]:
    """便捷入口（规格 §7.6）：经验 / 龙门币 / 来源。

    没有显式给 ``table`` 时用内置的官方数值表——这是**同进程的纯函数**，不发网络请求；
    拆包表要走 :class:`LevelUpTable` 的 ``from_payloads`` 由调用方（规划器 / 工具）装配。
    """
    return (table or LevelUpTable.builtin()).cost(
        rarity=rarity,
        from_elite=from_elite,
        from_level=from_level,
        to_elite=to_elite,
        to_level=to_level,
    )


# --------------------------------------------------------------------------- #
# 取数（拆包常量表）
# --------------------------------------------------------------------------- #


async def load_level_up_table(
    client: GamedataClient | None = None,
    *,
    settings_provider: Callable[[], Any] | None = None,
) -> LevelUpTable:
    """从拆包常量表建表；取不到时返回内置表（``source="builtin"``，带 ``note``）。

    先取 ``gamedata_const.json``（升级经验/龙门币），再取 ``item_table.json``（作战记录的
    折算值）；后者取不到只影响 ``exp_cards``，不影响经验与龙门币本身。
    """
    from maa_api.services.external.gamedata import default_gamedata_client

    reader = client or default_gamedata_client()
    const: Any = None
    items: Any = None
    note = ""
    try:
        const = (await reader.excel(CONST_FILE)).data
    except AppError as exc:
        return LevelUpTable.builtin(note=f"拆包常量表取不到（{exc.code}），用内置的官方数值表")
    except Exception as exc:  # noqa: BLE001 - 网络以外的意外也不该让整次规划失败
        return LevelUpTable.builtin(
            note=f"拆包常量表读取失败（{type(exc).__name__}），用内置的官方数值表"
        )
    try:
        items = (await reader.excel(ITEM_FILE)).data
    except Exception:  # noqa: BLE001 - 作战记录表只影响折算，缺了不影响经验数值
        note = "作战记录的折算值取不到，经验只给折算前的数值"
    try:
        table = LevelUpTable.from_payloads(const, items, note=note)
    except AppError as exc:
        return LevelUpTable.builtin(note=f"拆包常量表结构不符（{exc.code}），用内置的官方数值表")
    return table if table.available else LevelUpTable.builtin(note="拆包常量表为空，用内置的官方数值表")


#: 进程内共享的默认表（首次调用时从拆包数据装配，失败则内置兜底）。
_DEFAULT: LevelUpTable | None = None


async def default_level_up_table(client: GamedataClient | None = None) -> LevelUpTable:
    """:func:`load_level_up_table` 的进程内单例；同一进程只取一次拆包常量表。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = await load_level_up_table(client)
    return _DEFAULT


def reset_default_level_up_table() -> None:
    """丢弃进程内单例（测试用）。"""
    global _DEFAULT
    _DEFAULT = None


# --------------------------------------------------------------------------- #
# 纯工具
# --------------------------------------------------------------------------- #


def _sum_exp(row: Sequence[int], start: int, stop: int) -> int:
    """从 ``start`` 级升到 ``stop`` 级（不含）所需的经验。

    ``row[i]`` 是「i+1 级 → i+2 级」的经验，所以区间是 ``row[start-1 : stop-1]``：
    从 1 级升到 50 级要 49 次升级，取 ``row[0:49]``。
    """
    if not row:
        return 0
    low = max(int(start) - 1, 0)
    high = min(max(int(stop) - 1, 0), len(row))
    return sum(value for value in row[low:high] if value > 0)


def _sum_lmd(row: Sequence[int], start: int, stop: int) -> int:
    """从 ``start`` 级升到 ``stop`` 级（不含）所需的龙门币（索引口径同 :func:`_sum_exp`）。"""
    if not row:
        return 0
    low = max(int(start) - 1, 0)
    high = min(max(int(stop) - 1, 0), len(row))
    return sum(value for value in row[low:high] if value > 0)


def _int_rows(raw: Any) -> tuple[tuple[int, ...], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    rows: list[tuple[int, ...]] = []
    for row in raw:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            continue
        values: list[int] = []
        for value in row:
            number = _as_int(value)
            values.append(number if number is not None else -1)
        rows.append(tuple(values))
    return tuple(rows)


def _level_rows(raw: Any) -> dict[int, tuple[int, ...]]:
    rows = _int_rows(raw)
    return {index + 1: row for index, row in enumerate(rows) if row}


def _exp_items(items: Any) -> dict[str, int]:
    """``item_table.json`` 的 ``expItems`` → ``{itemId: 每张经验}``；缺了用内置表。"""
    payload = items.get("expItems") if isinstance(items, Mapping) else None
    if not isinstance(payload, Mapping):
        return dict(BUILTIN_EXP_ITEMS)
    out: dict[str, int] = {}
    for key, value in payload.items():
        if not isinstance(value, Mapping):
            continue
        gain = _as_int(value.get("gainExp"))
        item_id = value.get("id") or key
        if gain and gain > 0 and isinstance(item_id, str):
            out[item_id] = gain
    return out or dict(BUILTIN_EXP_ITEMS)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _digest(rows: Iterable[Sequence[int]]) -> str:
    payload = json.dumps([list(row) for row in rows], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _invalid(message: str) -> AppError:
    return AppError(
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
        f"拆包常量表结构不符：{message}",
        {"kind": "gamedata"},
    )


#: 作战记录的中文名（``item_table.json`` 里也有，这里只是给折算结果补名字）。
_EXP_ITEM_NAMES: Mapping[str, str] = {
    "2001": "基础作战记录",
    "2002": "初级作战记录",
    "2003": "中级作战记录",
    "2004": "高级作战记录",
}

# --------------------------------------------------------------------------- #
# 内置的官方数值表（逐项抄自 2026-10-10 抓取的 gamedata_const.json）
# --------------------------------------------------------------------------- #

#: 三条经验曲线（1★–2★ / 3★–4★ / 5★–6★），第 i 项是「升到 i+1 级」的经验；``-1`` 是表尾占位。
_BUILTIN_EXP_MAP = (
    # 1★
    (
        100, 117, 134, 151, 168, 185, 202, 219, 236, 253, 270, 287,
        304, 321, 338, 355, 372, 389, 406, 423, 440, 457, 474, 491,
        508, 525, 542, 559, 574, 589, 605, 621, 637, 653, 669, 685,
        701, 716, 724, 739, 749, 759, 770, 783, 804, 820, 836, 852,
        888, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
        -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
        -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
        -1, -1, -1, -1, -1, -1,
    ),
    # 2★
    (
        120, 172, 224, 276, 328, 380, 432, 484, 536, 588, 640, 692,
        744, 796, 848, 900, 952, 1004, 1056, 1108, 1160, 1212, 1264, 1316,
        1368, 1420, 1472, 1524, 1576, 1628, 1706, 1784, 1862, 1940, 2018, 2096,
        2174, 2252, 2330, 2408, 2584, 2760, 2936, 3112, 3288, 3464, 3640, 3816,
        3992, 4168, 4344, 4520, 4696, 4890, 5326, 6019, 6312, 6505, 6838, 7391,
        7657, 7823, 8089, 8355, 8621, 8887, 9153, 9419, 9605, 9951, 10448, 10945,
        11442, 11939, 12436, 12933, 13430, 13927, 14549, -1, -1, -1, -1, -1,
        -1, -1, -1, -1, -1, -1,
    ),
    # 3★
    (
        191, 303, 415, 527, 639, 751, 863, 975, 1087, 1199, 1311, 1423,
        1535, 1647, 1759, 1871, 1983, 2095, 2207, 2319, 2431, 2543, 2655, 2767,
        2879, 2991, 3103, 3215, 3327, 3439, 3602, 3765, 3928, 4091, 4254, 4417,
        4580, 4743, 4906, 5069, 5232, 5395, 5558, 5721, 5884, 6047, 6210, 6373,
        6536, 6699, 6902, 7105, 7308, 7511, 7714, 7917, 8120, 8323, 8526, 8729,
        9163, 9597, 10031, 10465, 10899, 11333, 11767, 12201, 12729, 13069, 13747, 14425,
        15103, 15781, 16459, 17137, 17815, 18493, 19171, 19849, 21105, 22361, 23617, 24873,
        26129, 27385, 28641, 29897, 31143, -1,
    ),
)

#: 三条龙门币曲线，第 i 项是「i+1 → i+2 级」的龙门币；``-1`` 是表尾占位。
_BUILTIN_UPGRADE_LMD_MAP = (
    # 1★
    (
        30, 36, 43, 50, 57, 65, 73, 81, 90, 99, 108,
        118, 128, 138, 149, 160, 182, 206, 231, 258, 286, 315,
        346, 378, 411, 446, 482, 520, 557, 595, 635, 677, 720,
        764, 809, 856, 904, 952, 992, 1042, 1086, 1131, 1178, 1229,
        1294, 1353, 1413, 1474, 1572, -1, -1, -1, -1, -1, -1,
        -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
        -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
        -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
        -1,
    ),
    # 2★
    (
        48, 71, 95, 120, 146, 173, 201, 231, 262, 293, 326,
        361, 396, 432, 470, 508, 548, 589, 631, 675, 719, 765,
        811, 859, 908, 958, 1010, 1062, 1116, 1171, 1245, 1322, 1400,
        1480, 1562, 1645, 1731, 1817, 1906, 1996, 2171, 2349, 2531, 2717,
        2907, 3100, 3298, 3499, 3705, 3914, 4127, 4344, 4565, 4807, 5294,
        6049, 6413, 6681, 7098, 7753, 8116, 8378, 8752, 9132, 9518, 9909,
        10306, 10709, 11027, 11533, 12224, 12926, 13639, 14363, 15097, 15843, 16599,
        17367, 18303, -1, -1, -1, -1, -1, -1, -1, -1, -1,
        -1,
    ),
    # 3★
    (
        76, 124, 173, 225, 279, 334, 392, 451, 513, 577, 642,
        710, 780, 851, 925, 1001, 1079, 1159, 1240, 1324, 1410, 1498,
        1588, 1680, 1773, 1869, 1967, 2067, 2169, 2273, 2413, 2556, 2702,
        2851, 3003, 3158, 3316, 3477, 3640, 3807, 3976, 4149, 4324, 4502,
        4684, 4868, 5055, 5245, 5438, 5634, 5867, 6103, 6343, 6587, 6835,
        7086, 7340, 7599, 7861, 8127, 8613, 9108, 9610, 10120, 10637, 11163,
        11696, 12238, 12882, 13343, 14159, 14988, 15828, 16681, 17545, 18422, 19311,
        20213, 21126, 22092, 23722, 25380, 27065, 28778, 30519, 32287, 34083, 35906,
        37745,
    ),
)

#: 精一 / 精二的龙门币（按 1★–6★ 排列；``-1`` 表示该稀有度没有这一档）。
_BUILTIN_ELITE_LMD = (
    # 1★
    (
        -1, -1,
    ),
    # 2★
    (
        -1, -1,
    ),
    # 3★
    (
        10000, -1,
    ),
    # 4★
    (
        15000, 60000,
    ),
    # 5★
    (
        20000, 120000,
    ),
    # 6★
    (
        30000, 180000,
    ),
)

#: 各稀有度、各精英阶段的满级（``maxLevel`` 的抄本）。
_BUILTIN_MAX_LEVEL = (
    # 1★
    (
        30,
    ),
    # 2★
    (
        30,
    ),
    # 3★
    (
        40, 55,
    ),
    # 4★
    (
        45, 60, 70,
    ),
    # 5★
    (
        50, 70, 80,
    ),
    # 6★
    (
        50, 80, 90,
    ),
)
