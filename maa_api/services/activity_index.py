"""活动索引：从本机活动资源给出「当期活动 + 它的关卡代号」（规格 §4.1）。

这份数据是**会随版本变化**的：M19 把「当期卡池、活动时间」这类内容排除在知识库之外，
所以这里不写死任何活动名或代号，而是每次从资源层现读。资源更新的落盘链见 §8.33/§8.34：
`POST /api/updates/resource` 之后 `version.json` 与 `Arknights-Tile-Pos/` 一起被刷新，
本模块按文件签名缓存，因此同一进程里下次调用就能看到新活动。

三层数据，都不联网：

- ``<层>/version.json`` 的 ``activity.name`` / ``activity.time``：当期活动的名字与开始时间；
- ``<层>/Arknights-Tile-Pos/overview.json``：关卡全表（含活动，4255 条；``stages.json``
  只有 608 个常驻关，含不了活动）；
- ``<层>/tasks/Stages/<代号前缀>.json`` 的**文件名存在性**：MAA 为这组关卡有没有定义
  识别与跳转。只判断存在性，不读内容。

## 为什么给的是排序结果，而非映射

名字到代号的映射**包内并不存在**（``version.json`` 只有名字，``overview.json`` 只有代号），
中文关卡名与活动名之间也没有字符关系（中文关卡名与活动名之间没有字符关系）。
上游那份带名字的 ``StageActivityV2.json`` 在 MAA 的 API 上，不在本机资源里；联网查它
违反 M19 定下的「运行时不联网」。

所以 :func:`infer_activity` 做的是**可解释的收敛加排序**：活动名里的期数（``#3``）落到
`stageId` 前缀的数字上，再按「是否全部关卡都在 ``activities/`` 命名空间」「有没有字母模式
后缀」「主关（``XX-<数字>``）有几关」排序。本机实测：当期活动的同期候选从 9 个收敛到 3 个，
排第一的候选有 12 个主关（另两个只有 8 个与 2 个），第一名与当期活动一致。**唯一候选时** ``confidence="high"``；**多个候选时**取第一名
但降级为 ``"low"``，并把其余候选放进 ``alternatives``，让模型知道这是推断出来的，
可以用 ``get_activity_stages`` 查某一关来核对。提不到期数时一律 ``"none"``：宁可说不知道，
也不给一个会让人点错活动的代号。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.resource_index import iter_resource_roots

__all__ = [
    "ACTIVITY_CONFIDENCE",
    "ACTIVITY_LEVEL_NAMESPACE",
    "MAX_ALTERNATIVES",
    "MAX_STAGES",
    "NAVIGATION_DIRECTORY",
    "OVERVIEW_DIRECTORY",
    "ActivityIndex",
    "ActivitySnapshot",
    "ActivityStage",
    "infer_activity",
    "main_stage_codes",
]

#: ``overview.json`` 所在子目录（与 ``stage_layout`` 同一份数据）。
OVERVIEW_DIRECTORY = "Arknights-Tile-Pos"
OVERVIEW_FILENAME = "overview.json"

#: 活动关卡导航文件的目录（内核资源层里的 ``tasks/Stages/<前缀>.json``）。
NAVIGATION_DIRECTORY = Path("tasks") / "Stages"

#: 活动关卡的 ``levelId`` 命名空间前缀：``activities/<前缀>/level_<stageId>``。
#: 主线、剿灭、肉鸽、危机合约各有自己的命名空间，这条把非活动关卡挡在候选之外。
ACTIVITY_LEVEL_NAMESPACE = "activities/"

#: 信心等级取值域，与规格 §4.1 逐字一致。
ACTIVITY_CONFIDENCE = ("high", "low", "none")

#: 一次返回的关卡数上限：一个活动最多 40 余关，60 是安全余量。
MAX_STAGES = 60

#: 非首选候选最多给几个（够模型判断，也不至于把上下文塞满）。
MAX_ALTERNATIVES = 5

_VERSION_FILENAME = "version.json"

#: 活动名里的期数：``#3`` 与 ``第三期`` 两种写法都认。
_SERIES_PATTERNS = (
    re.compile(r"#\s*(\d+)"),
    re.compile(r"第\s*([0-9]+|[一二三四五六七八九十])\s*(?:期|赛季|弹)"),
)
_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

#: ``stageId`` 前缀里的期数：``act<数字><模式>`` 这一种。
#: 只认「数字把前缀切成字母 + 数字 + 字母」这一种（``act7demo`` → ``act`` / 7 / ``demo``）；
#: ``camp_r_02`` 那种尾部数字不算：它代表期次的证据不足，实测会与真正的活动前缀撞进同一期数。
_EMBEDDED_SERIES = re.compile(
    r"^(?P<stem>[a-z]+)(?P<series>\d+)(?P<suffix>[a-z]+)$"
)

#: 主关代号：``XX-01`` / ``YY-1``。EX、TR、S、MO 等特殊关不算主关。
_MAIN_CODE = re.compile(r"^[A-Z]+-\d+$")

_CACHE_LOCK = threading.Lock()
_CACHE: "_Cache | None" = None


@dataclass(frozen=True, slots=True)
class ActivityStage:
    """一条活动关卡。``code`` 是游戏内显示的代号，``name`` 是中文关卡名。"""

    code: str
    name: str
    stage_id: str
    level_id: str | None


@dataclass(frozen=True, slots=True)
class ActivityCandidate:
    """同期的一个候选前缀（``act7demo``）与它的量级。"""

    prefix: str
    suffix: str
    stages_total: int
    main_stages: int
    code_range: tuple[str, str] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id_prefix": self.prefix,
            "mode": self.suffix,
            "stages_total": self.stages_total,
            "main_stages": self.main_stages,
            "code_range": list(self.code_range) if self.code_range else None,
        }


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """当期活动的只读快照（规格 §4.2 的返回形状）。"""

    name: str
    started_at: date | None
    series: int | None
    stage_id_prefix: str | None
    confidence: str
    matched_by: str
    related: tuple[str, ...]
    alternatives: tuple[ActivityCandidate, ...]
    stages: tuple[ActivityStage, ...]
    navigation: str
    resource_updated_at: str | None
    source: str

    @property
    def code_range(self) -> tuple[str, str] | None:
        """主关代号区间（``("XX-01", "XX-12")``）；没有主关时为 ``None``。"""
        codes = [stage.code for stage in self.stages if _MAIN_CODE.match(stage.code)]
        if not codes:
            return None
        ordered = sorted(codes, key=_code_sort_key)
        return (ordered[0], ordered[-1])

    def as_dict(self) -> dict[str, Any]:
        """工具与注入段共用的 wire 形状；``stages`` 按上限截断。"""
        return {
            "activity": {
                "name": self.name,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "series": self.series,
            },
            "stage_id_prefix": self.stage_id_prefix,
            "confidence": self.confidence,
            "matched_by": self.matched_by,
            "code_range": list(self.code_range) if self.code_range else None,
            "related": list(self.related),
            "alternatives": [item.as_dict() for item in self.alternatives[:MAX_ALTERNATIVES]],
            "navigation": self.navigation,
            "stages": [
                {
                    "code": stage.code,
                    "name": stage.name,
                    "stage_id": stage.stage_id,
                }
                for stage in self.stages[:MAX_STAGES]
            ],
            "stages_total": len(self.stages),
            "resource_updated_at": self.resource_updated_at,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class _Layer:
    """一个资源层的解析结果。``stages_by_prefix`` 的值已按代号排序。"""

    root: Path
    version: dict[str, Any]
    stages_by_prefix: dict[str, list[ActivityStage]]
    navigation_prefixes: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Cache:
    root: Path
    signature: tuple[Any, ...]
    layer: _Layer


# ---------------------------------------------------------------------------
# 纯函数：推断规则（与 IO 分开，边界逐个写死）
# ---------------------------------------------------------------------------
def _series_from_name(name: str) -> int | None:
    """活动名里的期数；认 ``#3`` / ``第三期``，否则 ``None``。"""
    for pattern in _SERIES_PATTERNS:
        match = pattern.search(name)
        if match is None:
            continue
        token = match.group(1)
        if token.isdigit():
            return int(token)
        if token in _CN_DIGITS:
            return _CN_DIGITS[token]
    return None


def _embedded_series(prefix: str) -> tuple[str, int, str] | None:
    """``act7demo`` → (``act``, 7, ``demo``)；不满足「字母+数字+字母」返回 ``None``。"""
    match = _EMBEDDED_SERIES.match(prefix)
    if match is None:
        return None
    return match.group("stem"), int(match.group("series")), match.group("suffix")


def _code_sort_key(code: str) -> tuple[int, int, str]:
    """主关按代号数字排在前，特殊关（EX / TR / S）排在后。

    ``XX-01`` < ``XX-02`` < … < ``XX-12`` < ``XX-EX-1``：不能直接取第一条数字，
    否则 ``XX-EX-1`` 里的 1 会把它排到 ``XX-02`` 前面。
    """
    main = _MAIN_CODE.match(code)
    if main is not None:
        return (0, int(code.rsplit("-", 1)[1]), code)
    digits = re.search(r"(\d+)", code)
    return (1, int(digits.group(1)) if digits else 10**6, code)


def main_stage_codes(stages: list[ActivityStage]) -> list[str]:
    """主关代号（``XX-<数字>``），按代号排序。"""
    return sorted(
        (stage.code for stage in stages if _MAIN_CODE.match(stage.code)), key=_code_sort_key
    )


def _all_in_activity_namespace(stages: list[ActivityStage]) -> bool:
    return bool(stages) and all(
        (stage.level_id or "").startswith(ACTIVITY_LEVEL_NAMESPACE) for stage in stages
    )


def _candidate(prefix: str, stages: list[ActivityStage]) -> ActivityCandidate | None:
    """能当候选的前缀：有字母模式后缀、且全部关卡都在活动命名空间里。"""
    parsed = _embedded_series(prefix)
    if parsed is None:
        return None
    if not _all_in_activity_namespace(stages):
        return None
    codes = main_stage_codes(stages)
    return ActivityCandidate(
        prefix=prefix,
        suffix=parsed[2],
        stages_total=len(stages),
        main_stages=len(codes),
        code_range=(codes[0], codes[-1]) if codes else None,
    )


def infer_activity(
    name: str,
    stages_by_prefix: dict[str, list[ActivityStage]],
) -> tuple[
    str | None,
    int | None,
    str,
    str,
    tuple[str, ...],
    tuple[ActivityCandidate, ...],
    list[ActivityStage],
]:
    """把活动名推成 ``(前缀, 期数, confidence, matched_by, related, 其余候选, 关卡表)``。

    规则与规格 §4.1 一一对应：提不到期数就 ``none``；同期的候选家族只有一个才
    ``high``，多个则按「主关数 → 关卡总数 → 前缀」排序取第一名并降级为 ``low``，
    其余候选进 ``alternatives``。``related`` 是同一类玩法的其它期次，只作提示。
    """
    series = _series_from_name(name)
    if series is None:
        return None, None, "none", "none", (), (), []

    candidates: list[ActivityCandidate] = []
    for prefix, stages in stages_by_prefix.items():
        parsed = _embedded_series(prefix)
        if parsed is None or parsed[1] != series:
            continue
        candidate = _candidate(prefix, stages)
        if candidate is None:
            continue
        candidates.append(candidate)

    if not candidates:
        return None, series, "none", "none", (), (), []

    candidates.sort(key=lambda item: (-item.main_stages, -item.stages_total, item.prefix))
    winner = candidates[0]
    # related 只列**同一类玩法**的其它期次（act7demo 的前几期）：
    # 同期的其它玩法在 alternatives 里，混在一起会让模型误判。
    winner_family = _embedded_series(winner.prefix)
    assert winner_family is not None
    related = tuple(
        sorted(
            prefix
            for prefix in stages_by_prefix
            if (parsed := _embedded_series(prefix)) is not None
            and (parsed[0], parsed[2]) == (winner_family[0], winner_family[2])
            and parsed[1] != series
        )
    )
    confidence = "high" if len(candidates) == 1 else "low"
    return (
        winner.prefix,
        series,
        confidence,
        "series-number",
        related,
        tuple(candidates[1:]),
        list(stages_by_prefix[winner.prefix]),
    )


# ---------------------------------------------------------------------------
# IO：资源层
# ---------------------------------------------------------------------------
def _layer_signature(root: Path) -> tuple[Any, ...] | None:
    """资源层的版本指纹：``version.json``、``overview.json`` 与导航目录的文件签名。

    ``update_resource`` 换掉这些文件之后签名必变，因此不需要重启进程或监听事件。
    导航目录只进「文件名 + 大小 + mtime」的清单：这一层决定 ``navigation`` 的取值，
    漏掉它会出现「新增了某期的导航文件、同一进程里却一直报 ``missing``」。
    """
    parts: list[Any] = []
    for path in (root / _VERSION_FILENAME, root / OVERVIEW_DIRECTORY / OVERVIEW_FILENAME):
        try:
            stat = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None
        parts.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    parts.append(_navigation_signature(root))
    return tuple(parts)


def _navigation_signature(root: Path) -> tuple[Any, ...]:
    """``tasks/Stages/*.json`` 的 ``(文件名, 大小, mtime)`` 清单；目录不存在给空元组。"""
    directory = root / NAVIGATION_DIRECTORY
    entries: list[Any] = []
    try:
        for path in sorted(directory.iterdir()):
            if path.suffix != ".json":
                continue
            stat = path.stat()
            entries.append((path.name, stat.st_size, stat.st_mtime_ns))
    except OSError:
        return ()
    return tuple(entries)


def _navigation_prefixes(root: Path) -> frozenset[str]:
    """``tasks/Stages/`` 下的文件名（不含扩展名）；目录不存在返回空集合。"""
    directory = root / NAVIGATION_DIRECTORY
    try:
        return frozenset(path.stem for path in directory.iterdir() if path.suffix == ".json")
    except OSError:
        return frozenset()


def _load_failed(reason: str) -> AppError:
    return AppError(
        ErrorCode.RESOURCE_LOAD_FAILED,
        f"活动资源不可用：{reason}。请先更新活动资源后再试",
        {"resource": f"{OVERVIEW_DIRECTORY}/{OVERVIEW_FILENAME}"},
    )


def _load_layer(root: Path) -> _Layer:
    """解析一个资源层；结构不对抛 ``RESOURCE_LOAD_FAILED``。"""
    try:
        version = json.loads((root / _VERSION_FILENAME).read_text(encoding="utf-8"))
        overview = json.loads(
            (root / OVERVIEW_DIRECTORY / OVERVIEW_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise _load_failed(f"{_VERSION_FILENAME} / {OVERVIEW_FILENAME} 读取失败") from exc
    if not isinstance(version, dict) or not isinstance(overview, dict):
        raise _load_failed("version.json 与 overview.json 必须是对象")

    stages_by_prefix: dict[str, list[ActivityStage]] = {}
    for item in overview.values():
        if not isinstance(item, dict):
            continue
        stage_id = item.get("stageId")
        code = item.get("code")
        if not isinstance(stage_id, str) or not stage_id or not isinstance(code, str) or not code:
            continue
        level_id = item.get("levelId")
        stages_by_prefix.setdefault(stage_id.split("_", 1)[0], []).append(
            ActivityStage(
                code=code,
                name=str(item.get("name") or ""),
                stage_id=stage_id,
                level_id=str(level_id) if isinstance(level_id, str) else None,
            )
        )
    for stages in stages_by_prefix.values():
        stages.sort(key=lambda stage: _code_sort_key(stage.code))
    return _Layer(
        root=root,
        version=version,
        stages_by_prefix=stages_by_prefix,
        navigation_prefixes=_navigation_prefixes(root),
    )


def _cached_layer(root: Path) -> _Layer | None:
    """读某一层并写缓存；文件不全返回 ``None``。"""
    global _CACHE

    signature = _layer_signature(root)
    if signature is None:
        return None
    with _CACHE_LOCK:
        cached = _CACHE
        if cached is not None and cached.root == root and cached.signature == signature:
            return cached.layer
    layer = _load_layer(root)
    with _CACHE_LOCK:
        _CACHE = _Cache(root=root, signature=signature, layer=layer)
    return layer


def _current_layer() -> _Layer | None:
    """按加载链的反向顺序找第一个「两个文件都在」的层。"""
    for root in reversed(iter_resource_roots()):
        layer = _cached_layer(root)
        if layer is not None:
            return layer
    return None


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------
class ActivityIndex:
    """活动索引门面；``resource_root`` 只为测试注入单层目录。"""

    def __init__(self, resource_root: str | Path | None = None) -> None:
        self._explicit_root = Path(resource_root) if resource_root is not None else None

    def snapshot(self) -> ActivitySnapshot:
        """当期活动快照；资源缺失或损坏抛 ``RESOURCE_LOAD_FAILED``。"""
        return self._snapshot_from_layer(self._layer())

    def stages_of_prefix(self, prefix: str) -> list[ActivityStage]:
        """取某个 stageId 前缀下的全部关卡（按代号排序）；未知前缀返回空表。"""
        return list(self._layer().stages_by_prefix.get(prefix, ()))

    def find_stage(self, query: str) -> dict[str, Any]:
        """按 stageId / code / 中文名 / levelId 查关卡，返回**全部**候选。

        查不到是正常结果（``matches: []``），不是错误：用户可能只是问了一个不存在的
        关卡名，模型需要看到「没有」而不是一个异常。多条候选按 ``stageId`` 排序，
        并用 ``is_current`` 标出属于当期活动的那一条、用 ``period`` 标出期数。

        匹配**分级**：``stageId`` → ``code`` → 中文名 → ``levelId``，某一级有命中就
        不再往下一级找。之所以分级、不取并集，是因为一个词可能同时是某关的代号与另一关的
        名字（实测「切尔诺伯格」既是一条剿灭代号、也是另一关的中文名），并集会把两关
        混在一起返回，级联则按「标识比名字更明确」的口径给出最相关的那一级。
        """
        layer = self._layer()
        snapshot = self._snapshot_from_layer(layer)
        current = {stage.stage_id for stage in snapshot.stages}
        needle = query.strip().casefold()
        fields = ("stage_id", "code", "name", "level_id")
        for field in fields:
            matches: list[dict[str, Any]] = []
            for prefix, stages in layer.stages_by_prefix.items():
                parsed = _embedded_series(prefix)
                for stage in stages:
                    value = getattr(stage, field)
                    if not value or str(value).casefold() != needle:
                        continue
                    matches.append(
                        {
                            "code": stage.code,
                            "name": stage.name,
                            "stage_id": stage.stage_id,
                            "level_id": stage.level_id,
                            "activity": prefix,
                            "period": parsed[1] if parsed else None,
                            "mode": parsed[2] if parsed else None,
                            "is_current": stage.stage_id in current,
                        }
                    )
            if matches:
                matches.sort(key=lambda item: item["stage_id"])
                return {
                    "query": query,
                    "matches": matches,
                    "matches_total": len(matches),
                    "matched_field": field,
                    "current_activity": snapshot.name,
                    "source": snapshot.source,
                }
        return {
            "query": query,
            "matches": [],
            "matches_total": 0,
            "matched_field": None,
            "current_activity": snapshot.name,
            "source": snapshot.source,
        }

    # -- 内部 -------------------------------------------------------------
    def _layer(self) -> _Layer:
        layer = (
            _cached_layer(self._explicit_root)
            if self._explicit_root is not None
            else _current_layer()
        )
        if layer is None:
            raise _load_failed("资源层里找不到 version.json 或 Arknights-Tile-Pos/overview.json")
        return layer

    def _snapshot_from_layer(self, layer: _Layer) -> ActivitySnapshot:
        activity = layer.version.get("activity")
        if not isinstance(activity, dict) or not isinstance(activity.get("name"), str):
            raise _load_failed("version.json 没有可用的 activity.name")
        name = str(activity["name"])
        raw_updated = layer.version.get("last_updated")
        prefix, series, confidence, matched_by, related, alternatives, stages = infer_activity(
            name, layer.stages_by_prefix
        )
        if prefix is None:
            navigation = "unknown"
        else:
            navigation = "available" if prefix in layer.navigation_prefixes else "missing"
        return ActivitySnapshot(
            name=name,
            started_at=_started_at(activity.get("time")),
            series=series,
            stage_id_prefix=prefix,
            confidence=confidence,
            matched_by=matched_by,
            related=related,
            alternatives=alternatives,
            stages=tuple(stages),
            navigation=navigation,
            resource_updated_at=str(raw_updated) if isinstance(raw_updated, str) else None,
            source=str(layer.root),
        )


def _started_at(value: Any) -> date | None:
    """``activity.time``（epoch 秒）→ 服务器本地日期；非数值或越界返回 ``None``。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=float(value))
    except (OverflowError, OSError, ValueError):
        return None
    return moment.astimezone().date()


#: 进程内共享的默认门面；工具与知识库注入段都用它，签名缓存保证不反复读盘。
_DEFAULT = ActivityIndex()


def default_activity_index() -> ActivityIndex:
    """默认门面；测试可自行构造 ``ActivityIndex(resource_root)``。"""
    return _DEFAULT
