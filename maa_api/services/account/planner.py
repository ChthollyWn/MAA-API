"""养成规划（规格 §7.6）：从当前状态到目标状态的增量材料 → 线性规划刷关与合成方案。

数据链：

1. 当前状态：注入的 ``box``（``OperatorBoxService``）→ 练度列缺值就是**未识别**
   （``None``，M35），不再假设「刚获得」；
2. 需求：``OperatorService.get(name, sections=["costs", "modules"])`` 的材料表算增量；
3. 配方：PRTS 材料页 ``{{道具配方/加工站}}``，按需求闭包递归取（一次 ≤ 20 个标题）；
4. 掉率：``DropService.item_rates``（企鹅物流），筛出候选关卡；
5. 线性规划：``scipy.optimize.linprog(method="highs")``，变量是出击次数与合成次数。

变量、约束、目标（与规格 §7.6 逐条对应）：

- 变量：候选关卡的出击次数 ``x_s ≥ 0``、加工站配方的合成次数 ``y_r ≥ 0``；
- 约束：每种材料 ``库存 + Σ 掉率·x_s + 合成产出·y_r − 合成消耗·y_r ≥ 需求``；
- 目标：``min Σ 理智·x_s + ε·Σ y_r``（``ε = 1e-6``，只用来在理智相同的解之间挑合成
  次数最少的那个）。

候选关卡口径：企鹅物流在该服务器开放（``open is not False``）、``apCost > 0``、样本数
``times ≥ 100``，且 MAA 能导航（本机 ``stages.json`` 的常驻关，或本机活动索引能定位、
且导航文件存在的关）。

## 实现裁决（规格没写死、由本模块定，M27 T8）

1. ``box`` 是鸭子类型句柄：干员表用 ``list_operators(query=…, owned_only=False, limit=5)``
   找（名字精确匹配优先，其次 char_id；兼容 ``items`` / ``operators`` 两种行键），行里
   没有 ``masteries`` / ``modules`` 而目标又要用到时，再用 ``get_operator(char_id)`` 取
   详情。仓库依次尝试 ``box.depot_quantities()`` / ``box.depot`` / ``box.depot_service`` /
   ``box`` 自身的 ``list_items(limit=…, offset=…)``（照 ``DepotService`` 的形状分页读全表），
   也可以用构造参数 ``depot`` 直接注入 ``DepotService``。没有任何仓库来源时按库存 0 算并
   在 ``notes`` 里说明——不静默。
2. 记录存在但 ``own=False``（或根本没有记录）时：``assumed_current`` 为真，但练度一律
   留空——「没识别过」不等于「精零 1 级」，要用的维度缺什么进 ``unknowns`` 并在 notes
   说明（M35）。只有识别确实给到的值才当事实用。
3. 隐含前置：目标只写了 ``masteries`` / ``modules`` 而没写 ``elite`` / ``skill_level``
   时，按「专精与模组需要精英二、专精需要技能 7 级」把缺的前置补进目标（进 ``implied``，
   并写进 notes）。显式写了的字段是上限：显式 ``elite=1`` + 专精不越过精一，只记一条
   ``warnings`` 说明冲突、不计专精材料——宁可不猜，也不越过用户给的目标。
4. 配方闭包：以需求材料为根做 BFS（每层批量取 PRTS，≤20 标题/次），深度 ≤ 6、材料数
   ≤ 160；互相引用的配方天然由「取过的材料不再取」终止。页面写的 ``产物`` 与页面名不符的
   配方保守丢弃（防方向取反），副产物不计（保守），合成消耗的龙门币单列在 ``synthesis``。
5. 供给不动点：某材料「能无限获取」= 有候选关，或存在配方（其原料也都能无限获取或
   库存已覆盖该材料的需求）；只有库存覆盖需求的材料也算「可用」。需求无法满足的材料不
   进约束（否则必然无解），改记进 ``unobtainable`` 并把 ``feasible`` 置 False；不抛异常。
6. 整数化：先解一次线性规划，把合成次数向上取整后固定住（``y_r = ceil``）再解第二次，
   只对出击次数做取舍；最后把出击次数向上取整。因此「合成清单」与「出击次数」都是整数，
   且第二次求解保证了整数化合成之后出击次数仍然最优。
7. 掉率取不到不算失败：单个材料取掉率失败（网络、限流、材料不在表里）只影响那个材料，
   记进 ``unobtainable`` 的 ``reason``；``scipy`` 不可用或求解失败时返回需求 + ``notes``，
   ``stages`` 为空、``feasible`` 为 False。
8. 升级经验与升级龙门币走拆包常量表（M27 起预留、M29 接入，规格 §7.6 第 10 条）：目标
   等级按该精英阶段的满级算（材料目标只写到「精英几」）；经验折算成「作战记录」的点数进
   ``requirements``，与龙门币一样**只报需求、不参与刷关规划**（规格 §7.3：仓库识别不含这两
   项）。精英化、技能、专精与模组消耗的龙门币由材料表计入，升级龙门币另算，两者不重复。
   拆包常量表取不到时用内置的官方数值表兜底并在 ``notes`` 写明来源（§3.5）。
9. Agent 工具层调的入口是 ``as_dict_plan(targets, use_depot=…)``（``plan`` + ``as_dict()``），
   没给 ``box`` 时用构造时注入的账号数据句柄；``default_upgrade_planner()`` 就是按这个口径
   装配的进程内单例（干员表与仓库都用 ``db.session.session_factory``）。
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from maa_api.domain.errors import AppError
from maa_api.services.account.rules import UNKNOWN_TRAINING_FIELDS
from maa_api.services.activity_index import (
    NAVIGATION_DIRECTORY,
    ActivityIndex,
    default_activity_index,
)
from maa_api.services.external.prts import PrtsClient
from maa_api.services.game.drops import SMALL_SAMPLE_TIMES, default_drop_service
from maa_api.services.game.items import default_item_service
from maa_api.services.game.levelup import max_level_for
from maa_api.services.game.operators import OperatorService
from maa_api.services.prts_parse.item import ItemPage, parse_item_page
from maa_api.services.resource_index import iter_resource_roots
from maa_api.settings import Settings, get_settings

__all__ = [
    "SANITY_PER_DAY",
    "UpgradePlan",
    "UpgradePlanner",
    "UpgradeTarget",
    "permanent_stage_ids",
]

#: 自然恢复的理智上限（规格 §7.6：按每天 240 估算天数）。
SANITY_PER_DAY = 240

#: 目标的合成次数惩罚（只用来在理智相同的解之间做取舍）。
EPSILON = 1e-6

#: 候选关卡的样本数下限（与 `DropService.SMALL_SAMPLE_TIMES` 同一口径）。
MIN_SAMPLE = SMALL_SAMPLE_TIMES

#: 每个材料取几行掉率（`item_rates` 的默认值）。
RATE_LIMIT = 8

#: PRTS 批量取页面一次最多 20 个标题（`PrtsClient.wikitext` 的上限）。
PRTS_BATCH = 20

#: 配方闭包的深度与规模上限（防病态页面把规划拖垮）。
MAX_CLOSURE_DEPTH = 6
MAX_CLOSURE_ITEMS = 160

#: 单次规划最多查多少个材料的掉率（安全阀；命中时进 notes）。
MAX_RATE_ITEMS = 120

#: 掉率查询的并发（来源层自己限流，这里只把批次的往返压到一起）。
RATE_CONCURRENCY = 4

#: 仓库分页读取：每页条数与最多页数。
DEPOT_PAGE_SIZE = 200
MAX_DEPOT_PAGES = 20

#: 一次规划最多处理几个目标（规格 §7.6：1–10 个）。
MAX_TARGETS = 10

#: 只报需求、不参与刷关规划的材料（规格 §7.3）。
REPORT_ONLY_NAMES: tuple[str, ...] = ("龙门币", "作战记录")

#: 技能序号 → 中文数字（``技能升级材料`` 的键是 ``一8`` / ``二8`` / ``三8``）。
_CN_NUMERALS = {1: "一", 2: "二", 3: "三"}

#: 技能等级键的上限（``7`` 是技能 7 级；8/9/10 是专精 1/2/3）。
MAX_SKILL_KEY = 7

#: 没有掉率、也没有配方时的获取提示兜底。
_STATIC_HINTS: Mapping[str, str] = {
    "模组数据块": "每周任务、剿灭作战与限时活动商店兑换；不能用理智刷取",
    "数据增补条": "限时活动商店与每周任务产出",
    "数据增补仪": "限时活动商店与每周任务产出",
}
_GENERIC_HINT = "关卡打不到：可能只在限时活动、凭证交易所或任务里产出；也可以用仓库库存补足"
_HINT_CHARS = 200

#: 本机常驻关表（`stages.json`）在资源层里的相对路径。
_PERMANENT_STAGES = Path("stages.json")

#: 常驻关表的进程内缓存：``(路径, 大小, mtime_ns) → stageId 集合``。
_PERMANENT_CACHE: tuple[tuple[Any, ...], frozenset[str]] | None = None


# --------------------------------------------------------------------------- #
# 纯工具
# --------------------------------------------------------------------------- #


def _key(value: Any) -> str:
    """材料名比较键：去掉全部空白并统一大小写（「固源岩 组」与 `固源岩组` 等价）。"""
    return "".join(str(value).split()).casefold()


def _clamp(value: Any, minimum: int, maximum: int, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _optional_int(value: Any, minimum: int, maximum: int) -> int | None:
    """练度列的「可能没有值」读法（M35）：``None`` ＝ 未识别，不再折算成默认值。

    与 :func:`_clamp` 的区别只在缺值：``_clamp`` 给兜底值（旧口径「没识别过 ＝ 精零
    1 级」就是这么来的），这里把「不知道」如实传下去，由调用方决定怎么处理。
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, number))


def _as_rarity(value: Any, default: int = 1) -> int:
    """星级归一：``6`` / ``"6"`` / ``"TIER_6"`` / ``"6★"`` 都读成 6。

    账号数据的 ``rarity`` 列是识别结果里的原始形状（内核给 ``TIER_6``，作业站给数字），
    两种都认；读不出时按最低星兜底（经验曲线按 1★–2★ 那条走，宁少不多）。
    """
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value if 1 <= value <= 6 else default
    text = str(value).strip().upper()
    if text.startswith("TIER_"):
        text = text[5:]
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        return default
    number = int(digits[:2])
    return number if 1 <= number <= 6 else default


def _chunks(items: Sequence[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _is_report_only(name: str) -> bool:
    return any(marker in name for marker in REPORT_ONLY_NAMES)


def _module_type_code(value: Any) -> str:
    """模组类型归一：``LOR-X`` / ``lor-x`` / ``x`` → ``X``（取最后一段字母）。"""
    text = str(value or "").strip().upper()
    if "-" in text:
        text = text.rsplit("-", 1)[-1]
    return text


def _materials(rows: Any) -> list[tuple[str, int]]:
    """材料表归一成 ``[(名称, 数量)]``；映射、``{"name","amount"}`` 列表与二元组都吃。"""
    if isinstance(rows, Mapping):
        rows = [{"name": key, "amount": value} for key, value in rows.items()]
    out: list[tuple[str, int]] = []
    for row in rows or ():
        if isinstance(row, Mapping):
            name = row.get("name") or row.get("item_name") or row.get("item_id")
            amount = row.get("amount")
            if amount is None:
                amount = row.get("count")
            if amount is None:
                amount = row.get("quantity")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            name, amount = row[0], row[1]
        else:
            continue
        if name is None or isinstance(amount, bool):
            continue
        try:
            quantity = int(amount or 0)
        except (TypeError, ValueError):
            continue
        if quantity:
            out.append((str(name), quantity))
    return out


def _merge(rows: Iterable[tuple[str, int]]) -> list[dict[str, Any]]:
    """同名相加合并（保持首次出现顺序），给 wire 用。"""
    merged: dict[str, int] = {}
    for name, amount in rows:
        merged[name] = merged.get(name, 0) + amount
    return [{"name": name, "amount": amount} for name, amount in merged.items()]


def _clip(value: Any, limit: int = _HINT_CHARS) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _hint_for(name: str, obtain: str | None) -> str:
    """获取提示：优先 PRTS 材料页的「获得方式」，其次内置表，最后通用兜底。"""
    cleaned = _clip(obtain)
    if cleaned:
        return cleaned
    for key, hint in _STATIC_HINTS.items():
        if _key(key) == _key(name):
            return hint
    return _GENERIC_HINT


# --------------------------------------------------------------------------- #
# 测试接缝：本机常驻关表、scipy
# --------------------------------------------------------------------------- #


def permanent_stage_ids() -> frozenset[str]:
    """本机 ``stages.json`` 的常驻关 ``stageId`` 集合（读不到给空集合，不抛异常）。

    按资源层加载链的反向顺序找第一个能读的 ``stages.json``（增量层覆盖基础层）；
    按文件签名缓存，资源更新后自动重读。测试通过 ``monkeypatch`` 换掉本函数。
    """
    global _PERMANENT_CACHE
    for root in reversed(iter_resource_roots()):
        path = Path(root) / _PERMANENT_STAGES
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        signature = (str(path), stat.st_size, stat.st_mtime_ns)
        cached = _PERMANENT_CACHE
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ids = frozenset(
            str(row.get("stageId"))
            for row in payload or ()
            if isinstance(row, Mapping) and row.get("stageId")
        )
        _PERMANENT_CACHE = (signature, ids)
        return ids
    return frozenset()


def _linprog_call(**kwargs: Any) -> Any:
    """``scipy.optimize.linprog`` 的间接调用（延迟导入；测试可以换成会抛的假实现）。"""
    from scipy.optimize import linprog

    return linprog(**kwargs)


def _has_navigation_file(prefix: str) -> bool:
    """本机有没有 ``tasks/Stages/<前缀>.json``（只看存在性，与 M24 同规则）。"""
    if not prefix:
        return False
    for root in iter_resource_roots():
        try:
            if (Path(root) / NAVIGATION_DIRECTORY / f"{prefix}.json").is_file():
                return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UpgradeTarget:
    """一个养成目标（由工具参数映射过来）。"""

    operator: str
    elite: int | None = None
    skill_level: int | None = None
    masteries: Mapping[int, int] | None = None
    modules: tuple[tuple[str, int], ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UpgradeTarget":
        """工具参数 → 目标；字符串键、列表形状都接受，越界值夹到合法区间。"""
        operator = str(payload.get("operator") or payload.get("name") or "").strip()
        elite = payload.get("elite")
        skill_level = payload.get("skill_level")
        return cls(
            operator=operator,
            elite=None if elite is None else _clamp(elite, 0, 2, 0),
            skill_level=None if skill_level is None else _clamp(skill_level, 1, 7, 1),
            masteries=_normalize_masteries(payload.get("masteries")),
            modules=tuple(_normalize_modules(payload.get("modules"))),
        )


@dataclass(frozen=True, slots=True)
class UpgradePlan:
    """一次规划的纯计算结果（``as_dict()`` 是 wire 形状）。"""

    targets: tuple[dict[str, Any], ...] = ()
    requirements: Mapping[str, int] = field(default_factory=dict)
    stages: tuple[dict[str, Any], ...] = ()
    synthesis: tuple[dict[str, Any], ...] = ()
    total_sanity: int = 0
    days_estimated: float = 0.0
    surplus: Mapping[str, int] = field(default_factory=dict)
    unobtainable: tuple[dict[str, Any], ...] = ()
    #: M35：因为练度未识别而没敢算的维度（``{"operator", "field", "reason"}``）。
    unknowns: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()
    feasible: bool = True
    total_runs: int = 0
    synthesis_lmd: int = 0
    server: str = "CN"
    sanity_per_day: int = SANITY_PER_DAY

    def as_dict(self) -> dict[str, Any]:
        """wire 形状：元组与映射都摊成 JSON 友好的容器。"""
        return {
            "targets": [dict(row) for row in self.targets],
            "requirements": dict(self.requirements),
            "stages": [dict(row) for row in self.stages],
            "synthesis": [dict(row) for row in self.synthesis],
            "total_sanity": self.total_sanity,
            "total_runs": self.total_runs,
            "days_estimated": self.days_estimated,
            "sanity_per_day": self.sanity_per_day,
            "surplus": dict(self.surplus),
            "unobtainable": [dict(row) for row in self.unobtainable],
            "unknowns": [dict(row) for row in self.unknowns],
            "feasible": self.feasible,
            "synthesis_lmd": self.synthesis_lmd,
            "server": self.server,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class _Recipe:
    """一条可用的加工站配方（键都是 :func:`_key` 归一后的材料键）。"""

    product: str
    output: int
    materials: tuple[tuple[str, int], ...]
    lmd_cost: int


@dataclass(slots=True)
class _StageOption:
    """一个候选关卡：理智消耗 + 各材料的每次出击期望产出。"""

    stage_id: str
    stage_code: str | None
    ap_cost: int
    rates: dict[str, float] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _SolveOutcome:
    runs: tuple[int, ...]
    counts: tuple[int, ...]
    status: str | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class _SolveResult:
    """``_solve`` 的产物：``plan`` 与 ``plan_needs`` 共用同一段求解。"""

    options: list[_StageOption]
    stages: list[dict[str, Any]]
    synthesis: list[dict[str, Any]]
    total_sanity: int
    surplus: dict[str, int]
    unobtainable: list[dict[str, Any]]
    allowed: set[str]
    synthesis_lmd: int
    feasible: bool
    has_recipes: bool


# --------------------------------------------------------------------------- #
# 目标 → 增量材料
# --------------------------------------------------------------------------- #


def _normalize_masteries(value: Any) -> dict[int, int] | None:
    """专精映射归一：``{1..3: 0..3}``、``{"1": 3}``、``[3, 0, 0]`` 都吃。"""
    if value is None:
        return None
    result: dict[int, int] = {}
    if isinstance(value, Mapping):
        rows = value.items()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = [(index, item) for index, item in enumerate(value, start=1)]
    else:
        return None
    for raw_key, raw_value in rows:
        try:
            slot = int(str(raw_key))
        except (TypeError, ValueError):
            continue
        if slot not in (1, 2, 3):
            continue
        result[slot] = _clamp(raw_value, 0, 3, 0)
    return result


def _normalize_modules(value: Any) -> list[tuple[str, int]]:
    """模组归一：``{"X": 3}`` / ``[{"type","level"}]`` / ``[("X", 3)]`` 都吃。"""
    if value is None:
        return []
    rows: list[tuple[Any, Any]] = []
    if isinstance(value, Mapping):
        rows = [(key, item) for key, item in value.items()]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, Mapping):
                rows.append(
                    (
                        item.get("type") or item.get("type_code") or item.get("module")
                        or item.get("name"),
                        item.get("level"),
                    )
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append((item[0], item[1]))
    out: list[tuple[str, int]] = []
    for raw_type, raw_level in rows:
        code = _module_type_code(raw_type)
        if not code:
            continue
        out.append((code, _clamp(raw_level, 0, 3, 0)))
    return out


def _normalize_current(row: Mapping[str, Any]) -> dict[str, Any]:
    """账号数据行 → 当前状态。

    M35：六个练度列没有值就是 ``None``（未识别），不再 clamp 成 1 / 0 / 空映射——
    「没识别过」在旧口径里会被读成「精零 1 级、无专精、无模组」，那是冒充事实。
    ``own`` 没有未知态（每次识别都给），``rarity`` 另走 :func:`_as_rarity` 兜底。
    """
    masteries = _optional_masteries(row.get("masteries"))
    modules = _optional_modules(row.get("modules"))
    return {
        "char_id": row.get("char_id"),
        "name": row.get("name"),
        "own": bool(row.get("own")),
        "rarity": _as_rarity(row.get("rarity")),
        "elite": _optional_int(row.get("elite"), 0, 2),
        "level": _optional_int(row.get("level"), 1, 90),
        "potential": _optional_int(row.get("potential"), 0, 6),
        "skill_level": _optional_int(row.get("skill_level"), 1, 7),
        "masteries": masteries,
        "modules": modules,
    }


def _optional_masteries(raw: Any) -> dict[str, int] | None:
    """专精列的当前值：``None`` ＝ 未识别；``{}`` ＝ 已知「没有专精」（M35）。"""
    if raw is None:
        return None
    normalized = _normalize_masteries(raw) or {}
    return {str(slot): normalized[slot] for slot in sorted(normalized)}


def _optional_modules(raw: Any) -> dict[str, int] | None:
    """模组列的当前值：``None`` ＝ 未识别；``{}`` ＝ 已知「没有模组」（M35）。"""
    if raw is None:
        return None
    return {code: level for code, level in _normalize_modules(raw)}


def _unknown_current() -> dict[str, Any]:
    """没有任何可用记录时的当前状态：六个练度列全 ``None``。

    M35 之前这里返回 ``FRESH_OPERATOR``（精零 1 级 0 潜技能 1），把「不知道」写成了
    一个具体的低练度值；现在只保留确定知道的部分（名字/拥有都是否），练度留给
    ``unknowns`` 去说。
    """
    return {
        "char_id": None,
        "name": None,
        "own": False,
        # 没有记录时不知道星级：按最低星兜底（与 `_as_rarity` 同一个口径）。
        "rarity": 1,
        "elite": None,
        "level": None,
        "potential": None,
        "skill_level": None,
        "masteries": None,
        "modules": None,
    }


#: 未识别字段的中文名（M35）：wire 里的 ``field`` 用列名，给人看的话用它。
UNKNOWN_TRAINING_LABELS: Mapping[str, str] = {
    "elite": "精英化",
    "level": "等级",
    "potential": "潜能",
    "skill_level": "技能等级",
    "masteries": "专精",
    "modules": "模组",
}


def _unknown_entry(operator: str, field: str) -> dict[str, Any]:
    """``unknowns`` 的一条：哪个干员的哪一维没识别，所以那一维的材料没敢算。"""
    return {"operator": operator, "field": field, "reason": "未识别，需先确认"}


def _order_unknowns(fields: Iterable[str]) -> list[str]:
    """去重并按 ``UNKNOWN_TRAINING_FIELDS`` 排序（wire 里字段顺序稳定，前端好渲染）。"""
    unique = list(dict.fromkeys(fields))
    known = [field for field in UNKNOWN_TRAINING_FIELDS if field in unique]
    return [*known, *(field for field in unique if field not in UNKNOWN_TRAINING_FIELDS)]


def _match_module(
    entries: Sequence[Mapping[str, Any]],
    type_code: str,
    type_by_name: Mapping[str, str],
) -> Mapping[str, Any] | None:
    """在 ``costs["modules"]`` 里按模组类型找条目（类型码或模组名都能对上）。"""
    wanted = _module_type_code(type_code)
    for entry in entries:
        name = str(entry.get("name") or "")
        if name and _key(name) == _key(type_code):
            return entry
        code = type_by_name.get(name)
        if code and _module_type_code(code) == wanted:
            return entry
    if len(entries) == 1 and not type_by_name:
        # `modules` 段拿不到时只有一个模组才能确定是它，否则不猜。
        return entries[0]
    return None


def _target_increments(
    costs: Mapping[str, Any],
    type_by_name: Mapping[str, str],
    current: Mapping[str, Any],
    target: UpgradeTarget,
) -> tuple[dict[str, int], dict[str, Any], list[str], list[str], list[str]]:
    """算「从当前到目标」的增量材料。

    :returns: ``(材料, 生效目标, 隐含补上的前置, 警告, 现状未知的字段)``。

    M35：目标要用到的维度只要现状**未知**（``None``），就不产生该维度的材料增量——
    不知道起点，「从当前到目标」的差就没法算；字段名进第五个返回值，由调用方汇总成
    ``unknowns`` 与一句人话。已知维度的增量照算，不受影响。
    """
    items: dict[str, int] = {}
    implied: list[str] = []
    warnings: list[str] = []
    unknown: list[str] = []

    def add(rows: Any) -> None:
        for name, amount in _materials(rows):
            items[name] = items.get(name, 0) + amount

    def mark_unknown(field: str) -> None:
        if field not in unknown:
            unknown.append(field)

    cur_elite = current.get("elite")
    cur_skill = current.get("skill_level")
    cur_masteries_rows = current.get("masteries")
    cur_masteries = (
        None
        if cur_masteries_rows is None
        else {
            int(slot): int(level)
            for slot, level in cur_masteries_rows.items()
            if str(slot).isdigit()
        }
    )
    cur_modules_rows = current.get("modules")
    cur_modules = (
        None
        if cur_modules_rows is None
        else {str(code): int(level) for code, level in cur_modules_rows.items()}
    )

    wanted_masteries = {
        int(slot): int(level)
        for slot, level in (target.masteries or {}).items()
        if int(level) > 0 and int(slot) in _CN_NUMERALS
    }
    wanted_modules = tuple(target.modules or ())

    target_elite = target.elite if target.elite is not None else cur_elite
    if (
        target.elite is None
        and (wanted_masteries or wanted_modules)
        and (cur_elite is None or cur_elite < 2)
    ):
        target_elite = 2
        implied.append("elite")
    elite_blocked = target_elite is not None and target_elite < 2

    target_skill = (
        target.skill_level if target.skill_level is not None else cur_skill
    )
    if (
        target.skill_level is None
        and wanted_masteries
        and not elite_blocked
        and (cur_skill is None or cur_skill < 7)
    ):
        target_skill = 7
        implied.append("skill_level")
    skill_blocked = target_skill is not None and target_skill < 7

    if (
        (wanted_masteries or wanted_modules)
        and target_elite is not None
        and target_elite < 2
    ):
        warnings.append(
            f"专精与模组需要精英二，目标只到精{target_elite}：这部分材料未计入"
        )
    if wanted_masteries and target_skill is not None and target_skill < 7:
        warnings.append(
            f"专精需要技能 7 级，目标只到技能 {target_skill} 级：专精材料未计入"
        )

    elite_costs = costs.get("elite") if isinstance(costs.get("elite"), Mapping) else {}
    if target_elite is None:
        pass  # 目标没提精英化，现状也未知：这一维没有要算的增量
    elif cur_elite is None:
        mark_unknown("elite")
    else:
        for level in range(cur_elite + 1, target_elite + 1):
            rows = elite_costs.get(f"精{level}")
            if not rows:
                warnings.append(f"材料数据里没有精英{level}（精{level}）的材料")
                continue
            add(rows)

    skill_costs = (
        costs.get("skill_levels") if isinstance(costs.get("skill_levels"), Mapping) else {}
    )
    if target_skill is None:
        pass
    elif cur_skill is None:
        mark_unknown("skill_level")
    else:
        for level in range(cur_skill + 1, min(target_skill, MAX_SKILL_KEY) + 1):
            rows = skill_costs.get(str(level))
            if not rows:
                warnings.append(f"材料数据里没有技能 {level} 级的材料")
                continue
            add(rows)

    if wanted_masteries and not elite_blocked and not skill_blocked:
        if cur_masteries is None:
            mark_unknown("masteries")
        else:
            for slot in sorted(wanted_masteries):
                numeral = _CN_NUMERALS[slot]
                start = max(min(cur_masteries.get(slot, 0), 3), 0) + 1
                for level in range(start, min(wanted_masteries[slot], 3) + 1):
                    rows = skill_costs.get(f"{numeral}{MAX_SKILL_KEY + level}")
                    if not rows:
                        warnings.append(f"材料数据里没有技能 {slot} 的专精{level}材料")
                        continue
                    add(rows)

    module_entries = costs.get("modules") if isinstance(costs.get("modules"), Sequence) else []
    if wanted_modules and not elite_blocked:
        if cur_modules is None:
            mark_unknown("modules")
        else:
            for type_code, want_level in wanted_modules:
                entry = _match_module(module_entries, type_code, type_by_name)
                if entry is None:
                    warnings.append(f"材料数据里找不到类型为「{type_code}」的模组")
                    continue
                levels = {
                    int(row["level"]): row.get("materials") or []
                    for row in entry.get("levels") or ()
                    if isinstance(row, Mapping) and str(row.get("level", "")).isdigit()
                }
                start = max(min(cur_modules.get(type_code, 0), 3), 0) + 1
                for level in range(start, min(want_level, 3) + 1):
                    rows = levels.get(level)
                    if not rows:
                        warnings.append(f"「{type_code}」模组没有 {level} 级的材料数据")
                        continue
                    add(rows)

    effective = {
        "elite": target_elite,
        "level": current.get("level"),
        "potential": current.get("potential"),
        "skill_level": target_skill,
        "masteries": {str(slot): level for slot, level in sorted(wanted_masteries.items())},
        "modules": [{"type": code, "level": level} for code, level in wanted_modules],
    }
    return items, effective, implied, warnings, unknown


# --------------------------------------------------------------------------- #
# 线性规划
# --------------------------------------------------------------------------- #


def _supply_analysis(
    *,
    needs: Mapping[str, int],
    depot: Mapping[str, int],
    has_stage: Mapping[str, bool],
    recipes: Sequence[_Recipe],
) -> tuple[list[str], list[_Recipe], list[str]]:
    """供给不动点：``(要约束的材料, 可用配方, 无法满足需求)``。

    一个材料「能无限获取」= 有候选关卡，或存在一条配方（它的原料都能获取）；某条配方
    「可用」则要求它的每个原料都能获取，或者库存够这个材料自己的需求（库存可以喂给
    合成）。需求超出「能无限获取」能力、库存又不够的材料进第三个返回值，不参与约束
    ——否则线性规划必然无解，而用户要的是「哪些刷不到」的清单。
    """

    def obtainable(key: str) -> bool:
        """能不能拿到这个材料（无限量，或库存够它自己的需求）。"""
        if has_stage.get(key):
            return True
        return depot.get(key, 0) > 0 and depot.get(key, 0) >= needs.get(key, 0)

    unlimited: set[str] = {key for key in has_stage if has_stage[key]}
    changed = True
    while changed:
        changed = False
        for recipe in recipes:
            if recipe.product in unlimited:
                continue
            if all(obtainable(key) or key in unlimited for key, _ in recipe.materials):
                unlimited.add(recipe.product)
                changed = True

    def producible(key: str) -> bool:
        return key in unlimited

    usable = [
        recipe
        for recipe in recipes
        if all(
            key in unlimited or (depot.get(key, 0) > 0 and depot.get(key, 0) >= needs.get(key, 0))
            for key, _ in recipe.materials
        )
    ]
    consumed_by_usable: set[str] = set()
    for recipe in usable:
        consumed_by_usable.update(key for key, _ in recipe.materials)

    rows = [
        key
        for key in list(needs) + sorted(consumed_by_usable - set(needs))
        if (needs.get(key, 0) > depot.get(key, 0) and producible(key))
        or key in consumed_by_usable
    ]
    unobtainable = [
        key for key in needs if needs[key] > depot.get(key, 0) and not producible(key)
    ]
    return rows, usable, unobtainable


def _build_lp(
    *,
    rows: Sequence[str],
    stages: Sequence[_StageOption],
    recipes: Sequence[_Recipe],
    needs: Mapping[str, int],
    depot: Mapping[str, int],
) -> tuple[list[float], list[list[float]], list[float]]:
    """组装 ``min c·z s.t. A_ub·z ≤ b_ub``（约束取负号写成 ≤ 形式）。"""
    stage_count = len(stages)
    size = stage_count + len(recipes)
    costs = [float(stage.ap_cost) for stage in stages] + [EPSILON] * len(recipes)
    matrix: list[list[float]] = []
    bounds: list[float] = []
    for key in rows:
        row = [0.0] * size
        for index, stage in enumerate(stages):
            row[index] = -float(stage.rates.get(key, 0.0))
        for index, recipe in enumerate(recipes):
            produced = recipe.output if recipe.product == key else 0
            consumed = sum(amount for name, amount in recipe.materials if name == key)
            row[stage_count + index] = -float(produced - consumed)
        matrix.append(row)
        bounds.append(-float(needs.get(key, 0) - depot.get(key, 0)))
    return costs, matrix, bounds


def _solve_allocation(
    *,
    rows: Sequence[str],
    stages: Sequence[_StageOption],
    recipes: Sequence[_Recipe],
    needs: Mapping[str, int],
    depot: Mapping[str, int],
) -> _SolveOutcome:
    """两次线性规划：先定合成次数（向上取整后固定），再定出击次数。"""
    if not rows:
        return _SolveOutcome(runs=(0,) * len(stages), counts=(0,) * len(recipes))
    if not stages and not recipes:
        return _SolveOutcome(
            runs=(0,) * len(stages),
            counts=(0,) * len(recipes),
            status="没有可用的候选关卡与配方，无法给出刷关方案",
        )
    costs, matrix, bounds = _build_lp(
        rows=rows, stages=stages, recipes=recipes, needs=needs, depot=depot
    )
    stage_count = len(stages)
    try:
        first = _linprog_call(
            c=costs,
            A_ub=matrix or None,
            b_ub=bounds or None,
            bounds=[(0, None)] * len(costs),
            method="highs",
        )
    except Exception as exc:  # scipy 缺失、数值异常……都不该让整次规划失败
        return _SolveOutcome(
            runs=(0,) * len(stages),
            counts=(0,) * len(recipes),
            status=f"线性规划不可用（{type(exc).__name__}: {exc}）",
        )
    if first is None or not getattr(first, "success", False):
        message = getattr(first, "message", None) or "求解器没有给出解"
        return _SolveOutcome(
            runs=(0,) * len(stages),
            counts=(0,) * len(recipes),
            status=f"线性规划无解（{message}）",
        )

    counts = tuple(int(math.ceil(value - 1e-9)) for value in first.x[stage_count:])
    runs_raw = list(first.x[:stage_count])
    warning: str | None = None
    if any(counts):
        # 合成次数是整数：固定住再解一次，出击次数才与整数化后的合成量匹配。
        try:
            second = _linprog_call(
                c=[float(stage.ap_cost) for stage in stages] + [0.0] * len(counts),
                A_ub=matrix or None,
                b_ub=bounds or None,
                bounds=[(0, None)] * stage_count + [(float(value), float(value)) for value in counts],
                method="highs",
            )
        except Exception:
            second = None
        if second is not None and getattr(second, "success", False):
            runs_raw = list(second.x[:stage_count])
        else:
            warning = (
                "合成次数向上取整后没有求出配套的出击次数，出击次数沿用取整前的解；"
                "个别材料的富余可能略有偏差"
            )
    runs = tuple(int(math.ceil(value - 1e-9)) for value in runs_raw)
    return _SolveOutcome(runs=runs, counts=counts, warning=warning)


# --------------------------------------------------------------------------- #
# 规划器
# --------------------------------------------------------------------------- #


class UpgradePlanner:
    """养成规划的领域服务；依赖全部可注入（测试与多来源装配）。"""

    def __init__(
        self,
        *,
        operators: Any = None,
        drops: Any = None,
        items: Any = None,
        index: Any = None,
        depot: Any = None,
        box: Any = None,
        levelup: Any = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._operators = operators
        self._drops = drops
        self._items = items
        self._index = index
        self._depot = depot
        #: ``as_dict_plan`` 没显式给 ``box`` 时的默认账号数据句柄（装配时注入）。
        self._account_box = box
        #: 升级经验 / 龙门币表（M29，规格 §7.6）。可以是 :class:`LevelUpTable`，也可以是
        #: 返回它的 awaitable（``levelup.load_level_up_table``）；缺省按内置官方数值表兜底。
        self._levelup = levelup
        self._levelup_table: Any = None
        self._levelup_loaded = False
        self._levelup_note = ""
        self._levelup_exp = 0
        self._settings_provider = settings_provider

    @property
    async def levelup(self) -> Any:
        """升级经验 / 龙门币表；第一次用到时才装配（内置兜底，不发网络请求就可用）。

        * 显式注入了 ``levelup``：直接用它（``await`` 一个 awaitable，否者原样返回），
          加载失败时**不**静默换内置表——注入方要能看见失败。
        * 没有注入：先试拆包常量表（``levelup.load_level_up_table``），取不到时它自己
          返回内置表（``source="builtin"``，带 ``note``）。
        """
        if self._levelup_loaded:
            return self._levelup_table
        self._levelup_loaded = True
        if self._levelup is not None:
            loaded = self._levelup
            if hasattr(loaded, "__await__"):
                loaded = await loaded
            self._levelup_table = loaded
            return self._levelup_table
        from maa_api.services.game.levelup import load_level_up_table

        self._levelup_table = await load_level_up_table()
        return self._levelup_table

    # -- 惰性默认装配 -----------------------------------------------------
    @property
    def operators(self) -> Any:
        if self._operators is None:
            from maa_api.services.game.operators import default_operator_service

            self._operators = default_operator_service()
        return self._operators

    @property
    def drops(self) -> Any:
        if self._drops is None:
            self._drops = default_drop_service()
        return self._drops

    @property
    def items(self) -> Any:
        if self._items is None:
            self._items = default_item_service()
        return self._items

    @property
    def index(self) -> ActivityIndex:
        if self._index is None:
            self._index = default_activity_index()
        return self._index

    @property
    def prts(self) -> PrtsClient:
        """配方闭包用的 PRTS 客户端（借 ``ItemService`` 的那个，便于共用缓存）。"""
        client = getattr(self.items, "prts", None)
        return client if client is not None else PrtsClient()

    # -- 对外 -------------------------------------------------------------
    async def as_dict_plan(
        self,
        targets: Sequence[UpgradeTarget | Mapping[str, Any]],
        *,
        use_depot: bool = True,
        server: str | None = None,
        box: Any = None,
    ) -> dict[str, Any]:
        """``plan`` + ``as_dict()``：Agent 工具层一步取 wire 形状。

        没给 ``box`` 时用构造时注入的账号数据句柄（``default_upgrade_planner`` 装的就是
        它），这样工具层不必自己拼 ``OperatorBoxService``。
        """
        handle = box if box is not None else self._account_box
        plan = await self.plan(
            targets, use_depot=use_depot, server=server, box=handle
        )
        return plan.as_dict()

    async def plan(
        self,
        targets: Sequence[UpgradeTarget | Mapping[str, Any]],
        *,
        use_depot: bool = True,
        server: str | None = None,
        box: Any = None,
    ) -> UpgradePlan:
        """算一次养成规划；依赖真机/网络的失败一律降级，不抛给调用方。"""
        settings = self._settings_provider()
        default_server = getattr(getattr(settings, "channel", None), "server", None) or "CN"
        server_name = str(server or default_server).upper()

        notes: list[str] = []
        prepared: list[UpgradeTarget] = []
        for item in targets or ():
            prepared.append(item if isinstance(item, UpgradeTarget) else UpgradeTarget.from_dict(item))
        if len(prepared) > MAX_TARGETS:
            prepared = prepared[:MAX_TARGETS]
            notes.append(f"目标超过 {MAX_TARGETS} 个，只算了前 {MAX_TARGETS} 个")
        if not prepared:
            return UpgradePlan(notes=("targets 为空：没有要规划的目标",), server=server_name)

        needs: dict[str, int] = {}
        display: dict[str, str] = {}

        def remember(name: str) -> str:
            key = _key(name)
            display.setdefault(key, name)
            return key

        target_rows: list[dict[str, Any]] = []
        for target in prepared:
            row, increments = await self._plan_target(target, box=box, notes=notes)
            target_rows.append(row)
            for name, amount in increments.items():
                key = remember(name)
                needs[key] = needs.get(key, 0) + amount

        depot = await self._resolve_depot(
            box, use_depot=use_depot, notes=notes, display=display
        )
        depot = {key: value for key, value in depot.items() if value > 0}

        report_only = {key for key in needs if _is_report_only(display.get(key, key))}
        if report_only:
            notes.append(
                "龙门币与作战记录只报需求、不参与刷关规划（仓库识别不含这两项，规格 §7.3）"
            )
        farm_needs = {key: value for key, value in needs.items() if key not in report_only}

        solved = await self._solve(
            farm_needs, depot=depot, display=display, server_name=server_name, notes=notes
        )
        stage_rows = solved.stages
        synthesis = solved.synthesis
        total_sanity = solved.total_sanity
        surplus = solved.surplus
        unobtainable = solved.unobtainable
        synthesis_lmd = solved.synthesis_lmd
        requirements = {
            display.get(key, key): amount for key, amount in needs.items() if amount
        }
        notes.extend(self._data_notes(server_name, solved.options, allowed=solved.allowed))
        if not use_depot:
            notes.append("use_depot=False：没有用仓库库存抵扣")
        # M29：升级经验与龙门币已接入拆包常量表（规格 §7.6 第 10 条）。
        if self._levelup_exp:
            if self._levelup_note:
                notes.append(self._levelup_note)
            notes.append(
                f"升级经验与龙门币已计入需求（作战记录 {self._levelup_exp} 点折算值、"
                f"升级龙门币见 requirements）；精英化、技能、专精与模组消耗的龙门币"
                "另由材料表计入，两者不重复"
            )
        else:
            notes.append(
                "未计入升级所需的经验与龙门币（拆包常量表不可用或没有可升的级）；"
                "精英化、技能、专精与模组消耗的龙门币已计入需求"
            )
        if solved.has_recipes:
            notes.append(
                "合成按 PRTS 材料页的加工站配方（需求闭包递归取），副产物不计（保守），"
                "合成消耗的龙门币单列在 synthesis"
            )

        unknowns = tuple(
            entry for row in target_rows for entry in row.get("unknowns", ())
        )
        return UpgradePlan(
            targets=tuple(target_rows),
            requirements=requirements,
            stages=tuple(stage_rows),
            synthesis=tuple(synthesis),
            total_sanity=total_sanity,
            days_estimated=round(total_sanity / SANITY_PER_DAY, 2),
            surplus=surplus,
            unobtainable=tuple(unobtainable),
            unknowns=unknowns,
            notes=tuple(dict.fromkeys(notes)),
            feasible=solved.feasible,
            total_runs=sum(row["runs"] for row in stage_rows),
            synthesis_lmd=synthesis_lmd,
            server=server_name,
        )

    async def plan_needs(
        self,
        needs: Mapping[str, int],
        *,
        server: str | None = None,
        display: Mapping[str, str] | None = None,
    ) -> UpgradePlan:
        """按「还缺多少材料」重算规划（规格 §7.7：计划流水线结束后重算剩余场次）。

        与 :meth:`plan` 只差入口：``needs`` 已经是净需求（需求 − 当前仓库，
        由调用方扣好），这里不重算目标增量、也不再扣一次仓库；``display`` 给的
        是材料显示名（:class:`UpgradePlan` 的 ``requirements`` 键就是它），
        缺省时直接用 ``needs`` 的键。继续走「配方闭包 → 掉率 → 候选关 → 线性
        规划」，掉率按 ``include_closed=False`` 取，所以已关闭的关卡天然被剔除。
        """
        settings = self._settings_provider()
        default_server = getattr(getattr(settings, "channel", None), "server", None) or "CN"
        server_name = str(server or default_server).upper()

        names: dict[str, str] = {}
        for raw_key, raw_name in (display or {}).items():
            names[_key(str(raw_key))] = str(raw_name)

        notes: list[str] = []
        farm_needs: dict[str, int] = {}
        for raw_name, raw_amount in (needs or {}).items():
            try:
                amount = int(raw_amount)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            name = str(raw_name)
            names.setdefault(_key(name), name)
            key = _key(name)
            farm_needs[key] = farm_needs.get(key, 0) + amount

        report_only = {key for key in farm_needs if _is_report_only(names.get(key, key))}
        if report_only:
            notes.append(
                "龙门币与作战记录只报需求、不参与刷关规划（仓库识别不含这两项，规格 §7.3）"
            )
            farm_needs = {
                key: value for key, value in farm_needs.items() if key not in report_only
            }
        if not farm_needs:
            notes.append("没有剩余需求：不需要刷关")
            return UpgradePlan(notes=tuple(notes), server=server_name)

        solved = await self._solve(
            farm_needs, depot={}, display=names, server_name=server_name, notes=notes
        )
        notes.extend(self._data_notes(server_name, solved.options, allowed=solved.allowed))
        return UpgradePlan(
            requirements={names.get(key, key): amount for key, amount in farm_needs.items()},
            stages=tuple(solved.stages),
            synthesis=tuple(solved.synthesis),
            total_sanity=solved.total_sanity,
            days_estimated=round(solved.total_sanity / SANITY_PER_DAY, 2),
            surplus=solved.surplus,
            unobtainable=tuple(solved.unobtainable),
            notes=tuple(dict.fromkeys(notes)),
            feasible=solved.feasible,
            total_runs=sum(row["runs"] for row in solved.stages),
            synthesis_lmd=solved.synthesis_lmd,
            server=server_name,
        )

    async def _solve(
        self,
        farm_needs: Mapping[str, int],
        *,
        depot: Mapping[str, int],
        display: dict[str, str],
        server_name: str,
        notes: list[str],
    ) -> _SolveResult:
        """把「净需求 + 仓库」解成关卡与场次；``plan`` 与 ``plan_needs`` 共用。

        只依赖 ``farm_needs``（已归一并扣过仓库），不重算目标增量；``display``
        缺的键补成键本身，``notes`` 就地追加解释。
        """
        recipes, meta, closure_notes = await self._load_recipes(
            list(farm_needs), display=display
        )
        notes.extend(closure_notes)

        universe = set(farm_needs)
        for recipe in recipes.values():
            universe.add(recipe.product)
            universe.update(key for key, _ in recipe.materials)
        for key in universe:
            display.setdefault(key, key)

        rates, status = await self._load_rates(sorted(universe), display=display,
                                               server=server_name, notes=notes)
        options, stage_notes = self._candidate_stages(rates, display=display)
        notes.extend(stage_notes)
        covered = {key for stage in options for key in stage.rates}
        for key, rows in rates.items():
            if rows and key not in covered and key not in status:
                status[key] = (
                    "掉率行都不满足候选条件（关卡开放、理智消耗 > 0、样本数 ≥ "
                    f"{MIN_SAMPLE}、MAA 能导航）"
                )

        has_stage: dict[str, bool] = {}
        for stage in options:
            for key in stage.rates:
                has_stage[key] = True

        rows, usable_recipes, unsatisfied = _supply_analysis(
            needs=farm_needs,
            depot=depot,
            has_stage=has_stage,
            recipes=list(recipes.values()),
        )
        outcome = _solve_allocation(
            rows=rows,
            stages=options,
            recipes=usable_recipes,
            needs=farm_needs,
            depot=depot,
        )
        if outcome.status is not None:
            notes.append(outcome.status)
        if outcome.warning is not None:
            notes.append(outcome.warning)

        allowed = set(rows)
        stage_rows, total_sanity, surplus = self._render_stages(
            options, outcome.runs, allowed=allowed, needs=farm_needs, depot=depot,
            recipes=usable_recipes, counts=outcome.counts, display=display,
        )
        synthesis = self._render_synthesis(
            usable_recipes, outcome.counts, display=display, depot=depot
        )
        synthesis_lmd = sum(row["lmd_total"] for row in synthesis)

        unobtainable = self._render_unobtainable(
            unsatisfied, needs=farm_needs, depot=depot, display=display,
            meta=meta, status=status,
        )
        if unobtainable:
            notes.append(
                "有材料无法只靠刷关与合成凑齐：见 unobtainable 的缺口与获取提示；"
                "其余材料已按可用途径规划"
            )
        feasible = not unobtainable and outcome.status is None
        if outcome.status is not None:
            stage_rows = []
            synthesis = []
            synthesis_lmd = 0
            total_sanity = 0
            surplus = {}

        return _SolveResult(
            options=options,
            stages=stage_rows,
            synthesis=synthesis,
            total_sanity=total_sanity,
            surplus=surplus,
            unobtainable=unobtainable,
            allowed=allowed,
            synthesis_lmd=synthesis_lmd,
            feasible=feasible,
            has_recipes=bool(recipes),
        )

    # -- 目标 -------------------------------------------------------------
    async def _plan_target(
        self, target: UpgradeTarget, *, box: Any, notes: list[str]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        row: dict[str, Any] = {
            "operator": target.operator,
            "char_id": None,
            "assumed_current": False,
            "current": _unknown_current(),
            "target": {},
            "requirements": {},
            "implied": [],
            "warnings": [],
            "unknowns": [],
        }
        if not target.operator:
            row["warnings"].append("目标缺少 operator 名字")
            notes.append("有一个目标没写干员名，已跳过")
            return row, {}

        costs, type_by_name, warning = await self._load_costs(target)
        if warning is not None:
            row["warnings"].append(warning)
            notes.append(warning)
            return row, {}

        current, assumed, current_notes, current_warnings = await self._current_state(
            box, target
        )
        row["assumed_current"] = assumed
        row["current"] = current
        row["warnings"].extend(current_warnings)
        notes.extend(current_notes)

        increments, effective, implied, warnings, unknown_fields = _target_increments(
            costs, type_by_name, current, target
        )
        row["target"] = effective
        row["implied"] = implied
        row["warnings"].extend(warnings)
        if implied:
            notes.append(
                f"「{target.operator}」只写了专精/模组，按前置补齐 {'、'.join(implied)}"
            )
        levelup, levelup_note, levelup_unknown = await self._levelup_increment(
            current, effective, notes
        )
        if levelup_note:
            row["warnings"].append(levelup_note)
            if levelup_note not in notes:
                notes.append(levelup_note)
        for name, amount in levelup.items():
            increments[name] = increments.get(name, 0) + amount

        # M35：现状未知的维度不硬算，列进 unknowns 并说一句人话（规格 §4.7）。
        missing = _order_unknowns([*unknown_fields, *levelup_unknown])
        if missing:
            row["unknowns"] = [_unknown_entry(target.operator, field) for field in missing]
            labels = "、".join(UNKNOWN_TRAINING_LABELS[field] for field in missing)
            unknown_note = (
                f"「{target.operator}」的{labels}未识别，未计入相应增量；"
                "先在账号页绑定森空岛账号并同步练度后再算"
            )
            row["warnings"].append(unknown_note)
            if unknown_note not in notes:
                notes.append(unknown_note)

        row["requirements"] = {
            item["name"]: item["amount"] for item in _merge(increments.items())
        }
        return row, increments

    async def _levelup_increment(
        self,
        current: Mapping[str, Any],
        effective: Mapping[str, Any],
        notes: list[str],
    ) -> tuple[dict[str, int], str | None, list[str]]:
        """升级经验（折算成作战记录）与升级龙门币（规格 §7.6、M29）。

        精英化、技能、专精与模组消耗的龙门币已经在材料表里；这里只补**升级**那部分，
        不重复计精英化龙门币。拆包常量表取不到时用内置的官方数值表兜底，来源写进
        ``notes``（§3.5：禁止冒充）。

        :returns: ``(材料, 警告/说明, 现状未知的字段名)``。M35：当前精英化或等级没识别
            时算不出「从当前到满级」那一段，返回未知字段名交给调用方汇总，不再按
            「精零 1 级」硬算。
        """
        rarity = _as_rarity(current.get("rarity"))
        from_elite = _optional_int(current.get("elite"), 0, 2)
        from_level = _optional_int(current.get("level"), 1, 90)
        if from_elite is None or from_level is None:
            missing = [
                field
                for field, value in (("elite", from_elite), ("level", from_level))
                if value is None
            ]
            return {}, None, missing
        table = await self.levelup
        if table is None or not getattr(table, "available", False):
            return {}, "升级经验与龙门币表不可用，这部分未计入需求", []
        to_elite = _optional_int(effective.get("elite"), 0, 2)
        if to_elite is None:
            # 目标没写精英化：按当前阶段原地升级。
            to_elite = from_elite
        to_level = self._resolve_target_level(
            rarity,
            current_level=from_level,
            current_elite=from_elite,
            to_elite=to_elite,
            notes=notes,
        )
        if to_level is None:
            return {}, None, []
        if getattr(table, "note", ""):
            # 来源说明（拆包数据 / 内置兜底）只记一次，最后统一进 notes（§3.5）。
            self._levelup_note = str(table.note)
        try:
            cost = table.cost(
                rarity=rarity,
                from_elite=from_elite,
                from_level=from_level,
                to_elite=to_elite,
                to_level=to_level,
            )
        except Exception as exc:  # noqa: BLE001 - 表坏了只影响这一段
            return {}, (
                f"升级经验与龙门币计算失败（{type(exc).__name__}），这部分未计入需求"
            ), []
        if cost.get("note"):
            # 表自己的说明（内置兜底、目标低于当前…）不是「这段算不出来」。
            self._levelup_note = self._levelup_note or str(cost["note"])
        exp = int(cost.get("exp") or 0)
        lmd = int(cost.get("lmd") or 0)
        if not exp and not lmd:
            return {}, None, []
        self._levelup_exp = exp
        return {"作战记录": exp, "龙门币": lmd}, None, []

    def _resolve_target_level(
        self,
        rarity: int,
        *,
        current_level: int,
        current_elite: int,
        to_elite: int,
        notes: list[str],
    ) -> int | None:
        """目标等级：该精英阶段的满级（材料目标不写等级，规格 §7.6 只规划到精英几）。

        已经在该阶段且已在满级时返回 ``None``（没有可升的级）。满级查不到也返回
        ``None`` 并写一条 ``notes``，不猜。传入的当前值必须是**已知**的练度
        （M35：调用方先确认过，未识别不会走到这里）。
        """
        levels = getattr(self._levelup_table, "max_levels", None)
        known = levels if isinstance(levels, Mapping) else None
        if to_elite == current_elite:
            cap = max_level_for(rarity, current_elite, max_levels=known)
        else:
            # 要精英化：当前阶段先升到满级（精英化本身不改等级），精英化之后从 1 级练到
            # 新阶段的满级。两者由 `LevelUpTable.cost` 分别按各自的 cap 取。
            cap = max_level_for(rarity, to_elite, max_levels=known)
        if cap is None:
            notes.append("没有该稀有度与精英阶段的满级数据，未计升级所需的经验与龙门币")
            return None
        if cap == current_level and to_elite == current_elite:
            return None
        return max(cap, current_level)

    async def _load_costs(
        self, target: UpgradeTarget
    ) -> tuple[dict[str, Any], dict[str, str], str | None]:
        """取干员的材料表（需要模组时一并取 ``modules`` 段拿类型码）。"""
        sections = ["costs"] + (["modules"] if target.modules else [])
        try:
            payload = await self.operators.get(target.operator, sections=sections)
        except AppError as exc:
            return {}, {}, f"取不到「{target.operator}」的养成材料（{exc.code}）"
        except Exception as exc:  # 真机/网络以外的意外也不该让整次规划失败
            return {}, {}, f"取不到「{target.operator}」的养成材料（{type(exc).__name__}）"
        if not isinstance(payload, Mapping):
            return {}, {}, f"「{target.operator}」的材料数据形状不对，已跳过"
        candidates = payload.get("candidates")
        if isinstance(candidates, Sequence) and candidates:
            names = "、".join(str(item.get("title")) for item in candidates[:5]
                              if isinstance(item, Mapping))
            return {}, {}, f"「{target.operator}」有多个同名候选（{names}），无法确定是哪一个"
        costs = payload.get("costs")
        if not isinstance(costs, Mapping):
            unavailable = payload.get("unavailable") or []
            return {}, {}, (
                f"「{target.operator}」的 PRTS 材料页没有可用数据"
                f"（unavailable={list(unavailable)}）"
            )
        type_by_name: dict[str, str] = {}
        for entry in payload.get("modules") or ():
            if isinstance(entry, Mapping) and entry.get("name") and entry.get("type_code"):
                type_by_name[str(entry["name"])] = str(entry["type_code"])
        return costs, type_by_name, None

    async def _current_state(
        self, box: Any, target: UpgradeTarget
    ) -> tuple[dict[str, Any], bool, list[str], list[str]]:
        """找干员的当前状态。

        M35：找不到记录（或没标拥有）时**不**再假设「刚获得」——那是拿一个编出来的
        低练度当事实。只返回「不知道」的当前状态（练度全 ``None``、``assumed_current``
        为真），要用的维度缺什么由 ``_target_increments`` 收进 ``unknowns``。
        """
        fresh = _unknown_current()
        if box is None:
            return fresh, True, [
                f"没有账号数据（box 未注入），「{target.operator}」的练度未识别，只算已知维度"
            ], []
        try:
            payload = await box.list_operators(
                query=target.operator, owned_only=False, limit=5
            )
        except Exception as exc:
            return fresh, True, [
                f"账号数据读取失败（{type(exc).__name__}），"
                f"「{target.operator}」的练度未识别，只算已知维度"
            ], []

        rows = _rows_of(payload)
        row = _pick_operator(rows, target.operator)
        if row is None:
            return fresh, True, [
                f"账号数据里没有「{target.operator}」的记录，练度未识别并标 assumed_current"
            ], []

        needs_detail = bool(target.masteries or target.modules)
        if needs_detail and "masteries" not in row and "modules" not in row:
            char_id = row.get("char_id")
            getter = getattr(box, "get_operator", None)
            if char_id and callable(getter):
                try:
                    detail = await getter(str(char_id))
                except Exception:
                    detail = None
                detail_row = _rows_of(detail) if isinstance(detail, Mapping) else []
                if detail_row:
                    row = {**row, **detail_row[0]}

        current = _normalize_current(row)
        if not current["own"]:
            return fresh, True, [], [
                "账号数据没有把该干员标为已拥有，练度未识别（含 assumed_current）"
            ]
        return current, False, [], []

    # -- 仓库 -------------------------------------------------------------
    async def _resolve_depot(
        self, box: Any, *, use_depot: bool, notes: list[str], display: dict[str, str]
    ) -> dict[str, int]:
        """读仓库数量（键是 :func:`_key` 归一后的材料名）；读不到按 0 计并写 notes。"""
        if not use_depot:
            return {}
        direct = getattr(box, "depot_quantities", None) if box is not None else None
        if callable(direct):
            try:
                payload = await direct()
            except Exception as exc:
                notes.append(
                    f"仓库数据读取失败（{type(exc).__name__}），库存按 0 计"
                )
                return {}
            return _counts_from(dict(_normalize_counts(payload)), display)

        source = None
        for candidate in (
            self._depot,
            getattr(box, "depot", None) if box is not None else None,
            getattr(box, "depot_service", None) if box is not None else None,
            box,
        ):
            if candidate is not None and callable(getattr(candidate, "list_items", None)):
                source = candidate
                break
        if source is None:
            notes.append("没有可读的仓库数据（use_depot 无库存可抵扣），库存按 0 计")
            return {}

        raw: dict[str, int] = {}
        offset = 0
        exhausted = False
        for _page in range(MAX_DEPOT_PAGES):
            try:
                payload = await source.list_items(limit=DEPOT_PAGE_SIZE, offset=offset)
            except Exception as exc:
                notes.append(
                    f"仓库数据读取失败（{type(exc).__name__}），已读到的 {len(raw)} 项照用"
                )
                exhausted = True
                break
            entries, total = _depot_entries(payload)
            for name, amount in _normalize_counts(entries).items():
                raw[name] = raw.get(name, 0) + amount
            offset += len(entries)
            if not entries or (isinstance(total, int) and offset >= total):
                exhausted = True
                break
        if not exhausted:
            notes.append(
                f"仓库条目超过分页上限（{MAX_DEPOT_PAGES} × {DEPOT_PAGE_SIZE}），"
                "只用了读到的部分做抵扣"
            )
        return _counts_from(raw, display)

    # -- 配方闭包 ---------------------------------------------------------
    async def _load_recipes(
        self, roots: Sequence[str], *, display: dict[str, str]
    ) -> tuple[dict[str, _Recipe], dict[str, dict[str, Any]], list[str]]:
        """按需求闭包批量取 PRTS 材料页，返回 ``(配方, 材料元信息, 降级说明)``。"""
        recipes: dict[str, _Recipe] = {}
        meta: dict[str, dict[str, Any]] = {}
        notes: list[str] = []
        seen: set[str] = set()
        frontier = [key for key in dict.fromkeys(roots) if key not in seen]
        depth = 0
        while frontier and depth < MAX_CLOSURE_DEPTH and len(seen) < MAX_CLOSURE_ITEMS:
            depth += 1
            todo = [key for key in frontier if key not in seen][: PRTS_BATCH * 6]
            frontier = []
            for chunk in _chunks(todo, PRTS_BATCH):
                try:
                    batch = await self.prts.wikitext([display.get(key, key) for key in chunk])
                except AppError as exc:
                    notes.append(
                        f"PRTS 材料页取数失败（{exc.code}），合成方案可能不完整"
                    )
                    continue
                except Exception as exc:
                    notes.append(
                        f"PRTS 材料页取数失败（{type(exc).__name__}），合成方案可能不完整"
                    )
                    continue
                found: dict[str, ItemPage] = {}
                for page in getattr(batch, "pages", ()) or ():
                    if getattr(page, "missing", False):
                        continue
                    content = getattr(page, "content", None)
                    if not isinstance(content, str) or not content:
                        continue
                    parsed = parse_item_page(content, title=str(getattr(page, "title", "")))
                    for alias in (getattr(page, "title", None), getattr(page, "redirect_from", None)):
                        if isinstance(alias, str) and alias:
                            found.setdefault(_key(alias), parsed)
                for key in chunk:
                    if key in seen:
                        continue
                    seen.add(key)
                    parsed = found.get(key)
                    if parsed is None:
                        continue
                    display.setdefault(key, parsed.name or display.get(key, key))
                    meta[key] = {"title": parsed.title, "obtain": parsed.obtain}
                    recipe = parsed.recipe
                    if recipe is None:
                        continue
                    raw_materials = [
                        (str(name), int(amount))
                        for name, amount in recipe.materials
                        if amount
                    ]
                    materials = tuple((_key(name), amount) for name, amount in raw_materials)
                    if not materials:
                        continue
                    declared = _key(recipe.product) if recipe.product else key
                    if declared != key:
                        notes.append(
                            f"「{display.get(key, key)}」的材料页写的产物是"
                            f"「{recipe.product}」，方向存疑，保守不用这条配方"
                        )
                        continue
                    recipes[key] = _Recipe(
                        product=key,
                        output=max(int(recipe.product_count or 1), 1),
                        materials=materials,
                        lmd_cost=max(int(recipe.lmd_cost or 0), 0),
                    )
                    for name, _amount in raw_materials:
                        # 闭包里的材料名按原样留一份，wire 上不出现归一化后的小写名。
                        display.setdefault(_key(name), name)
                        if _key(name) not in seen and len(seen) + len(frontier) < MAX_CLOSURE_ITEMS:
                            frontier.append(_key(name))
        return recipes, meta, list(dict.fromkeys(notes))

    # -- 掉率与候选关卡 ---------------------------------------------------
    async def _load_rates(
        self, keys: Sequence[str], *, display: Mapping[str, str], server: str,
        notes: list[str],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        """并发取掉率（键 → 行）；失败按材料记原因，不影响其它材料。"""
        selected = list(keys)[:MAX_RATE_ITEMS]
        if len(keys) > len(selected):
            notes.append(
                f"本次需要 {len(keys)} 个材料的掉率，超过单次上限 {MAX_RATE_ITEMS}，"
                "多出来的材料没有取掉率"
            )
        semaphore = asyncio.Semaphore(RATE_CONCURRENCY)
        rates: dict[str, list[dict[str, Any]]] = {}
        status: dict[str, str] = {}

        async def fetch(key: str) -> tuple[str, Any]:
            async with semaphore:
                try:
                    payload = await self.drops.item_rates(
                        display.get(key, key),
                        limit=RATE_LIMIT,
                        include_closed=False,
                        server=server,
                    )
                except Exception as exc:
                    return key, exc
                return key, payload

        results = await asyncio.gather(*(fetch(key) for key in selected)) if selected else []
        for key, value in results:
            if isinstance(value, Exception):
                code = getattr(value, "code", None) or type(value).__name__
                status[key] = f"企鹅物流没有取到掉率（{code}）"
                rates[key] = []
                continue
            rows = value.get("rows") if isinstance(value, Mapping) else None
            rates[key] = [row for row in (rows or ()) if isinstance(row, Mapping)]
            if not rates[key]:
                status[key] = "企鹅物流没有这个材料的掉率记录"
        for key in keys[len(selected):]:
            status[key] = f"超过单次掉率查询上限（{MAX_RATE_ITEMS}），这次没查"
        return rates, status

    def _candidate_stages(
        self, rates: Mapping[str, Sequence[Mapping[str, Any]]], *,
        display: Mapping[str, str],
    ) -> tuple[list[_StageOption], list[str]]:
        """按规格 §7.6 的口径筛候选关：开放、理智 > 0、样本 ≥ 100、MAA 能导航。"""
        by_stage: dict[str, _StageOption] = {}
        dropped: set[str] = set()
        navigable: dict[str, bool] = {}
        state = {
            "permanent": permanent_stage_ids(),
            "index_failed": False,
            "filter_available": False,
        }
        state["filter_available"] = bool(state["permanent"])

        for key, rows in rates.items():
            for row in rows:
                if row.get("open") is False:
                    continue
                times = row.get("times")
                if not isinstance(times, int) or times < MIN_SAMPLE:
                    continue
                ap_cost = row.get("ap_cost")
                if not isinstance(ap_cost, int) or ap_cost <= 0:
                    continue
                rate = row.get("rate")
                if not isinstance(rate, (int, float)) or rate <= 0:
                    continue
                stage_id = str(row.get("stage_id") or "")
                if not stage_id:
                    continue
                if stage_id not in navigable:
                    navigable[stage_id] = self._navigable(stage_id, state=state)
                if not navigable[stage_id]:
                    dropped.add(stage_id)
                    continue
                stage = by_stage.get(stage_id)
                if stage is None:
                    stage = _StageOption(
                        stage_id=stage_id,
                        stage_code=row.get("stage_code") if isinstance(row.get("stage_code"), str) else None,
                        ap_cost=ap_cost,
                    )
                    by_stage[stage_id] = stage
                stage.rates[key] = stage.rates.get(key, 0.0) + float(rate)
                stage.details.append(
                    {
                        "item_id": row.get("item_id"),
                        "item_name": display.get(key, key),
                        "rate": float(rate),
                        "sanity_per_item": row.get("sanity_per_item"),
                        "times": times,
                        "started_at": row.get("started_at"),
                        "ended_at": row.get("ended_at"),
                    }
                )

        notes: list[str] = []
        if dropped:
            sample = "、".join(sorted(dropped)[:3])
            notes.append(
                f"剔除 {len(dropped)} 个 MAA 不能导航的候选关（如 {sample}）"
            )
        if not state["filter_available"]:
            notes.append(
                "本机 stages.json 与活动索引都读不到，未做导航可行性过滤："
                "候选关卡只按企鹅物流口径筛（开放、理智 > 0、样本 ≥ 100）"
            )
        return sorted(by_stage.values(), key=lambda stage: stage.stage_id), notes

    def _navigable(self, stage_id: str, *, state: dict[str, Any]) -> bool:
        """MAA 能不能导航这一关（本机常驻关，或活动索引里有导航文件的关）。"""
        if stage_id in state["permanent"]:
            state["filter_available"] = True
            return True
        if state["index_failed"]:
            return not state["filter_available"]
        try:
            payload = self.index.find_stage(stage_id)
        except Exception:
            state["index_failed"] = True
            # 活动索引读不到：连常驻关表也没有就只能放行（不做凭空的过滤）。
            return not state["filter_available"]
        state["filter_available"] = True
        matches = []
        if isinstance(payload, Mapping):
            matches = [row for row in payload.get("matches") or () if isinstance(row, Mapping)]
        match = next(
            (row for row in matches if _key(row.get("stage_id")) == _key(stage_id)), None
        )
        if match is None:
            return False
        prefix = str(match.get("activity") or "")
        return self._navigation_state(prefix) == "available"

    def _navigation_state(self, prefix: str) -> str:
        """本机对某个 stageId 前缀有没有导航文件（``available`` / ``missing``）。"""
        try:
            snapshot = self.index.snapshot()
        except Exception:
            snapshot = None
        if snapshot is not None and getattr(snapshot, "stage_id_prefix", None) == prefix:
            state = getattr(snapshot, "navigation", None)
            if state in {"available", "missing", "unknown"}:
                return str(state)
        return "available" if _has_navigation_file(prefix) else "missing"

    # -- 渲染 -------------------------------------------------------------
    def _render_stages(
        self,
        stages: Sequence[_StageOption],
        runs: Sequence[int],
        *,
        allowed: set[str],
        needs: Mapping[str, int],
        depot: Mapping[str, int],
        recipes: Sequence[_Recipe],
        counts: Sequence[int],
        display: Mapping[str, str],
    ) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
        rows: list[dict[str, Any]] = []
        total_sanity = 0
        supply: dict[str, float] = {}
        for index, stage in enumerate(stages):
            taken = runs[index] if index < len(runs) else 0
            if taken <= 0:
                continue
            sanity = taken * stage.ap_cost
            total_sanity += sanity
            for key, rate in stage.rates.items():
                supply[key] = supply.get(key, 0.0) + rate * taken
            rows.append(
                {
                    "stage_id": stage.stage_id,
                    "stage_code": stage.stage_code,
                    "runs": taken,
                    "sanity_per_run": stage.ap_cost,
                    "sanity": sanity,
                    "items": [
                        detail for detail in stage.details if detail["item_name"] and
                        _key(detail["item_name"]) in allowed
                    ],
                }
            )
        for index, recipe in enumerate(recipes):
            taken = counts[index] if index < len(counts) else 0
            if taken <= 0:
                continue
            supply[recipe.product] = supply.get(recipe.product, 0.0) + recipe.output * taken
            for key, amount in recipe.materials:
                supply[key] = supply.get(key, 0.0) - amount * taken
        rows.sort(key=lambda row: (-row["sanity"], row["stage_id"]))
        surplus = {
            display.get(key, key): int(round(supply.get(key, 0.0)))
            + depot.get(key, 0)
            - needs.get(key, 0)
            for key in set(needs) | set(allowed)
        }
        return rows, total_sanity, {key: value for key, value in sorted(surplus.items()) if value > 0}

    def _render_synthesis(
        self,
        recipes: Sequence[_Recipe],
        counts: Sequence[int],
        *,
        display: Mapping[str, str],
        depot: Mapping[str, int],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, recipe in enumerate(recipes):
            taken = counts[index] if index < len(counts) else 0
            if taken <= 0:
                continue
            materials = [
                {"name": display.get(key, key), "amount": amount}
                for key, amount in recipe.materials
            ]
            rows.append(
                {
                    "product": display.get(recipe.product, recipe.product),
                    "count": taken,
                    "materials": materials,
                    "total_materials": [
                        {"name": item["name"], "amount": item["amount"] * taken}
                        for item in materials
                    ],
                    "lmd_cost": recipe.lmd_cost,
                    "lmd_total": recipe.lmd_cost * taken,
                }
            )
        rows.sort(key=lambda row: row["product"])
        return rows

    def _render_unobtainable(
        self,
        keys: Sequence[str],
        *,
        needs: Mapping[str, int],
        depot: Mapping[str, int],
        display: Mapping[str, str],
        meta: Mapping[str, Mapping[str, Any]],
        status: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key in keys:
            name = display.get(key, key)
            have = depot.get(key, 0)
            obtain = (meta.get(key) or {}).get("obtain")
            rows.append(
                {
                    "name": name,
                    "amount": needs.get(key, 0),
                    "have": have,
                    "missing": max(needs.get(key, 0) - have, 0),
                    "reason": status.get(key)
                    or "既没有可用的候选关卡，也没有可用的加工站配方",
                    "hint": _hint_for(name, obtain if isinstance(obtain, str) else None),
                }
            )
        rows.sort(key=lambda row: row["name"])
        return rows

    def _data_notes(
        self,
        server: str,
        stages: Sequence[_StageOption],
        *,
        allowed: set[str],
    ) -> list[str]:
        """候选口径 + 掉率统计区间与样本数（规格 §7.6 的「假设与数据来源」）。"""
        notes = [
            f"候选关卡：企鹅物流在 {server} 开放、理智消耗 > 0、样本数 ≥ {MIN_SAMPLE}，"
            "且 MAA 能导航（stages.json 常驻关 + 有导航文件的活动关）"
        ]
        times: list[int] = []
        starts: list[str] = []
        ends: list[str] = []
        for stage in stages:
            for detail in stage.details:
                if _key(detail.get("item_name")) not in allowed:
                    continue
                if isinstance(detail.get("times"), int):
                    times.append(int(detail["times"]))
                if detail.get("started_at"):
                    starts.append(str(detail["started_at"]))
                if detail.get("ended_at"):
                    ends.append(str(detail["ended_at"]))
        if times:
            window = f"{min(starts)} 起" if starts else "统计区间未知"
            if ends:
                window += f"，截至 {max(ends)}"
            else:
                window += "，至今"
            notes.append(
                f"掉率来自企鹅物流（{server}）：{window}，"
                f"参与规划的样本数 {min(times)}–{max(times)}"
            )
        return notes


# --------------------------------------------------------------------------- #
# 模块级小工具
# --------------------------------------------------------------------------- #


def _rows_of(payload: Any) -> list[dict[str, Any]]:
    """从 ``list_operators`` / ``get_operator`` 的返回里取行（兼容 items/operators）。"""
    if isinstance(payload, Mapping):
        for key in ("items", "operators", "rows"):
            rows = payload.get(key)
            if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
                return [dict(row) for row in rows if isinstance(row, Mapping)]
        if payload.get("char_id"):
            return [dict(payload)]
        return []
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return [dict(row) for row in payload if isinstance(row, Mapping)]
    return []


def _pick_operator(rows: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    """按名字（精确、忽略空白与大小写）或 char_id 选行；同名前缀不算命中。"""
    needle = _key(name)
    matched = [row for row in rows if _key(row.get("name")) == needle or _key(row.get("char_id")) == needle]
    if not matched:
        return None
    owned = [row for row in matched if row.get("own")]
    return (owned or matched)[0]


def _depot_entries(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    """``list_items`` 的返回 → ``(行, total)``。"""
    if isinstance(payload, Mapping):
        rows = payload.get("items")
        total = payload.get("total") if isinstance(payload.get("total"), int) else None
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            return [dict(row) for row in rows if isinstance(row, Mapping)], total
        return [], total
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return [dict(row) for row in payload if isinstance(row, Mapping)], None
    return [], None


def _normalize_counts(payload: Any) -> dict[str, int]:
    """仓库数量归一成 ``{材料名: 数量}``：``{"items": [...]}``、行列表、映射都吃。"""
    counts: dict[str, int] = {}
    if isinstance(payload, Mapping) and not any(
        key in payload for key in ("items", "rows", "counts", "quantities")
    ):
        for name, amount in payload.items():
            if isinstance(amount, Mapping):
                amount = amount.get("count", amount.get("quantity"))
            if isinstance(amount, bool) or amount is None:
                continue
            try:
                counts[str(name)] = counts.get(str(name), 0) + int(amount or 0)
            except (TypeError, ValueError):
                continue
        return counts
    if isinstance(payload, Mapping):
        for key in ("items", "rows", "counts", "quantities"):
            if key in payload:
                payload = payload[key]
                break
    if isinstance(payload, Mapping):
        for name, amount in payload.items():
            if isinstance(amount, bool) or amount is None:
                continue
            try:
                counts[str(name)] = counts.get(str(name), 0) + int(amount or 0)
            except (TypeError, ValueError):
                continue
        return counts
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        for row in payload:
            if not isinstance(row, Mapping):
                continue
            name = row.get("name") or row.get("item_name") or row.get("item_id")
            amount = row.get("count")
            if amount is None:
                amount = row.get("quantity")
            if amount is None:
                amount = row.get("amount")
            if name is None or isinstance(amount, bool) or amount is None:
                continue
            try:
                counts[str(name)] = counts.get(str(name), 0) + int(amount or 0)
            except (TypeError, ValueError):
                continue
    return {name: value for name, value in counts.items() if value > 0}


def _counts_from(raw: Mapping[str, int], display: dict[str, str]) -> dict[str, int]:
    """``{材料名: 数量}`` → ``{归一键: 数量}``，顺便记下展示名。"""
    counts: dict[str, int] = {}
    for name, amount in raw.items():
        key = _key(name)
        display.setdefault(key, name)
        counts[key] = counts.get(key, 0) + amount
    return {key: value for key, value in counts.items() if value > 0}


#: 进程内共享的默认规划器（装配时可用 `app.state.planner_service` 覆盖）。
_DEFAULT: UpgradePlanner | None = None


def default_upgrade_planner() -> UpgradePlanner:
    """默认规划器：干员表与仓库都走账号数据库的默认会话工厂。

    干员查询、材料页与掉率用各自的默认服务（``OperatorService`` /
    ``ItemService`` / ``DropService``），因此这条路径不需要额外装配。
    """
    global _DEFAULT
    if _DEFAULT is None:
        from maa_api.db import session as db_session
        from maa_api.services.account.box import OperatorBoxService
        from maa_api.services.account.depot import DepotService

        factory = db_session.session_factory
        _DEFAULT = UpgradePlanner(
            box=OperatorBoxService(factory),
            depot=DepotService(factory),
        )
    return _DEFAULT
